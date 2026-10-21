# Standalone stock data collection

Independent Python 3.13+ project: no ATS, Supabase or Dagster imports. Runtime
libraries are yfinance, pandas, NumPy, SciPy and python-dotenv; HTTP, compression,
SQLite, logging and pacing use the standard library. `uv.lock` pins dependencies.

```sh
cd data_collection
uv sync --frozen
uv run pytest -q
uv run collect-stocks --help
uv run backfill-options --help
```

## Sources and output

| Dataset | Provider | Data |
|---|---|---|
| `prices` | Massive | Daily OHLCV/VWAP, split-adjusted, with original bar fields |
| `options` | Massive + local NumPy/SciPy | Dated ATM call/put IV, realized volatility, IV minus RV, variance spread |
| `reference` | Massive | Current company identifiers, market cap and shares |
| `short_interest` | Massive | Dated short interest and days-to-cover reports |
| `short_volume` | Massive | Dated off-exchange short-sale activity |
| `news` | Massive | Articles and original ticker-specific sentiment insights |
| `insider_transactions` | Massive | Form 4 records, all returned codes/security types |
| `cash_flow` | Yahoo | Available quarterly cash-flow statements |
| `analyst_actions` | Yahoo | Available upgrade/downgrade actions |
| `earnings` | Yahoo | Available actual/estimated EPS and surprises |

These ten feeds are the defaults. Float is excluded because its endpoint supplies
only a snapshot. Legacy `float`, `income_statement` and `balance_sheet` remain
explicitly selectable, with the latter two supplied by Yahoo. Yahoo is never
instantiated for a prices/options-only run. No Twelve Data or paid options
snapshot/Greeks endpoint is used. Provider entitlements can change; denied feeds
are logged as errors rather than replaced with fabricated values.

Massive `adjusted=true` adjusts splits, **not dividends**: these prices are not
Yahoo's total-return adjusted close. Financial/Yahoo feeds and reference remain
current snapshots regardless of a historical requested window. Fiscal period
ends are not earnings announcement timestamps; short-interest settlement dates
are not release timestamps. This collector does not certify point-in-time
fundamental/news factors or classify insider transactions.

One JSON line contains one ticker dictionary with `schema_version=2`,
`history_window`, timestamps and named `datasets`. Each feed includes provider,
scope, retrieval timestamps, data and status (`ok`, `empty`, `partial`, `error`).
A failed request has `data=null`; unavailable option sessions retain dated rows
with null metrics and a reason. Empty data is not an economic zero.

```python
from data_collection import collect_ticker, iter_tickers
from data_collection.option_metrics import OptionSettings

stock = collect_ticker("AAPL", datasets=["prices", "options"],
                       checkpoint_path="output/aapl.sqlite")
for stock in iter_tickers(["AAPL", "MSFT"], datasets=["prices", "cash_flow"]):
    print(stock["ticker"], stock["datasets"]["prices"]["status"])
```

## Normal collection and separate historical backfill

Normal runs default to the preceding weekday (not the unfinished current day),
and compute options for the latest available session in the requested window.
Explicit dates override that window. Holidays without bars produce empty feeds;
the collector does not invent a session. The CLI's bulk price mode retrieves
all selected tickers with one grouped-market request per date/adjustment, and
caches filtered responses for the exact universe. The first options run also
warms the trailing volatility window; later overlapping runs reuse that cache.
Python functions default to per-ticker prices; set `price_mode="bulk"` and
`market_cache_path` when using `iter_tickers` for a large universe.

```sh
export MASSIVE_API_KEY=...  # Or POLYGON_IO; never commit the value.
export MASSIVE_RATE_STATE="$PWD/output/cache/massive-rate.sqlite"
uv run collect-stocks --tickers-file universe.txt \
  --market-cache output/cache/market.sqlite \
  --output output/daily-2026-10-20/stocks.jsonl --log-to-stderr

# Separate process: every available stock session in the explicit date range.
uv run backfill-options --tickers-file universe.txt \
  --start 2025-10-20 --end 2026-10-19 \
  --output output/backfill-2025-2026/stocks.jsonl --log-to-stderr
```

`backfill-options` requires both dates and a durable output path, collects only
Massive prices/options, uses per-ticker prices by default, and fills the complete
requested options window (`history_days=-1`). It does not fetch today's Yahoo
snapshots as purported historical fundamentals. Bars are limited by the key's
history entitlement; allow extra price warm-up history before the requested
start. Prices must exist for a stock/date to create an options session row.

Ticker files accept newline/comma/space separators and `#` comments. Symbols
normalize to uppercase; duplicates are removed. Use Massive/Wikipedia share-class
notation such as BRK.B; the Yahoo-only feeds translate it to BRK-B. CLI selections
accept `--datasets prices news` or `--datasets "prices,news"`.

## Option calculations

For each valuation date:

1. Use the same session's **unadjusted** stock close as nominal spot. Use
   split-adjusted closes only for trailing daily log returns.
2. List contracts with `as_of` equal to the valuation date and `expired=false`
   (evaluated relative to `as_of`). Follow all pagination links. Select the
   earliest expiry at least 30 calendar days ahead, within another 45 days, and
   the nearest-to-spot strike with both a standard call and put. Preserve the
   actual contract symbols; exclude adjusted deliverables.
3. Fetch each selected contract's whole requested daily price range once and
   reuse it across sessions. Require positive-volume trades from that exact
   valuation session; never carry forward an older option close.
4. Solve call and put Black-Scholes IV in a NumPy batch with SciPy's vectorized
   `elementwise.find_root` and compiled `special.ndtr` normal CDF. Invalid prices,
   arbitrage violations, flat prices or convergence failures become nulls.
5. Save the arithmetic mean of call/put IV. Calculate annualized realized
   volatility as `std(last 21 daily log returns, ddof=1) * sqrt(252)`.
   Save `implied_risk_premium = IV - RV` and `variance_spread = IV**2 - RV**2`.

All volatilities/rates are decimals; actual dates, expiry, tenor, strike, raw
spot, option prices/volumes and model assumptions are retained for audit.
CLI/Jenkins parameters set minimum tenor, RV window, annual continuously
compounded risk-free rate (default .04) and dividend yield (default 0).
The yield/rate are explicit constant assumptions, not historical curves.
This is a European Black-Scholes approximation to US equity options, often
American. Daily last trades can be asynchronous. Neither the average IV nor
IV-minus-trailing-RV is a model-free forward variance risk premium or a
constant-30-day interpolated volatility index.

## Pacing, recovery and errors

**If retrieval breaks, rerunning the same command resumes from the last
completed checkpoint.** Completed feeds and complete nonempty options API
responses are committed independently as compressed JSON in SQLite. An
interrupted multi-page response restarts at its first page; already-complete
contract catalogues and contract histories are reused. Failed/partial feeds
retry. Empty feeds are completed checkpoints; use a new run to refresh them.

A checkpoint binds the date window, API origin and option model settings. Omitted
dates reuse the saved window; conflicting settings are rejected before rewriting
existing JSONL. Version-1 checkpoints are refused because they contain Yahoo
prices/options: choose a new output/RUN_ID for version 2. Keep old files intact.
SQLite is authoritative: restart rebuilds JSONL from saved/new results, removing
torn lines and duplicate ticker rows. Do not run two writers on the same output
or checkpoint. Existing output without checkpoints is preserved and rejected.

Massive request starts are paced at 13 seconds, including paginated requests
and retries. HTTP 429/5xx/network failures retry at most three times with at
least a 60-second cooldown. Set **the same `MASSIVE_RATE_STATE` SQLite path**
for concurrent daily/backfill jobs using the same API allowance. Both Jenkins
files mount it at `/data/cache/massive-rate.sqlite`. Without this setting,
pacing coordinates only one process. Shared jobs divide the available capacity;
other clients using the key also consume quota. Keep state on a local filesystem.

Logs record UTC timestamps, ticker/feed, bulk progress, missing sessions, errors
and resume events; they rotate at 5 MiB with three backups. Credentials/raw
exception bodies are excluded. Default log: `logs/data_collection.log`; override
with `--log-file` or Python `log_path`. CLI exit codes: 0 without failed feeds,
1 for errors/partial feeds, 130 for interruption. Other feeds continue after a
feed error. No Supabase writes or legacy ATS job changes occur.

### One-year capacity estimate

Assume 503 stocks, approximately 252 trading sessions, one contract-catalogue
page per stock/session, at most three stock-history calls per stock, and 13
seconds between request starts. If `U` is the number of distinct selected option
contracts per stock (calls and puts combined), then:

`calls = 503 * (252 + U + 3)`; `elapsed days ~= calls * 13 / 86400`.

| Distinct contracts per stock (`U`) | Estimated calls | Continuous days |
|---|---:|---:|
| 100 | 178,565 | 26.9 |
| 250 | 254,015 | 38.2 |
| 504 (new call and put every session) | 381,777 | 57.4 |
| 504, with two catalogue pages per session | 508,533 | 76.5 |

These are planning scenarios, not measured request counts. Budget **about two
months** with single-page catalogues, or **about 77 days** with two pages, plus
retries and time used by daily jobs.
The dated catalogue requests alone take about 19 days with this implementation.
NumPy/SciPy accelerate computation; API pacing dominates elapsed time. A full
year backfill is never launched automatically by the normal collection job.

## Keys, Docker and Jenkins

Supply Massive's key with `MASSIVE_API_KEY`, legacy `POLYGON_IO`,
`--env-file /path/to/file`, `--api-key-file /run/secrets/massive`, or Python
`api_key=...`. Explicit key files/Python arguments override environment keys.
Yahoo needs no key. Missing keys fail before collection. Secrets are runtime
parameters, never image build arguments.

```sh
docker build --target test -t stock-data-collection:test .
docker build --target runtime -t piperofthedawn/ats:data-collection-v2 .
# Export MASSIVE_API_KEY first. Create the writable host state directory.
docker run --rm --init --stop-timeout 30 --user "$(id -u):$(id -g)" \
  --env MASSIVE_API_KEY --env MASSIVE_RATE_STATE=/data/cache/massive-rate.sqlite \
  --mount "type=bind,src=$PWD/state,dst=/data" \
  piperofthedawn/ats:data-collection-v2 --tickers-file /data/universe.txt \
  --market-cache /data/cache/market.sqlite --output /data/runs/daily/stocks.jsonl

# Historical worker uses the same image and shared rate-state mount.
docker run --rm --init --stop-timeout 30 --user "$(id -u):$(id -g)" \
  --env MASSIVE_API_KEY --env MASSIVE_RATE_STATE=/data/cache/massive-rate.sqlite \
  --mount "type=bind,src=$PWD/state,dst=/data" --entrypoint backfill-options \
  piperofthedawn/ats:data-collection-v2 --tickers-file /data/universe.txt \
  --start 2025-10-20 --end 2026-10-19 --output /data/runs/year/stocks.jsonl
```

The runtime is unprivileged, contains only the standalone package and runtime
dependencies, and sends SIGINT on stop. State survives container removal. The
Docker test target executes offline tests and mocked Docker/Jenkins commands.

Two separate Pipeline-from-SCM jobs on `main`:

- `data_collection/Jenkinsfile`: normal collection, latest-session options,
  bulk prices/cache, 72-hour timeout. Blank RUN_ID becomes daily-v2-YYYY-MM-DD UTC.
- `data_collection/Jenkinsfile.backfill`: manual history worker, requires
  START_DATE/END_DATE, separate RUN_ID, 90-day timeout. Does not schedule itself.

Both pull `IMAGE_REF` (default `piperofthedawn/ats:data-collection-v2`) before
`docker run`, disable checkout/concurrent builds within their job, and mount
`STATE_DIR` (default `/home/karma/data-collection`). They share the rate-state
file, but use distinct `runs/RUN_ID` output/checkpoints/logs. TICKERS selects the
universe (AAPL/MSFT are demo defaults). A changed universe requires a new RUN_ID.
Use a stable explicit RUN_ID to resume; a new ID starts a fresh collection.

`MASSIVE_CREDENTIAL_ID` defaults to Jenkins Secret text `massive-api-key`.
Optional `DOCKERHUB_CREDENTIAL_ID` is a Username with password credential
(e.g. `dockerhub-data-collection`); registry login uses stdin and a temporary
Docker config removed after pull. `IMAGE_REF` may also be pinned to a returned
registry digest. No key value is included in Docker command arguments. Jenkins
needs Docker daemon access and write permission on STATE_DIR. Existing ATS
pipelines under `jenkins/` retain their local-image workflow.
