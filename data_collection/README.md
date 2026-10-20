# Standalone stock data collection

This directory is an independently installable Python project. It can be copied
into its own repository: it imports no ATS, Supabase, Dagster or dashboard code.
It collects source dictionaries, with compressed SQLite checkpoints and rotating
logs. It does not calculate factor exposures.

```sh
cd data_collection
uv sync --frozen
uv run collect-stocks --help
uv run pytest -q
```

The only direct runtime dependencies are `yfinance`, `pandas`, and
`python-dotenv`. HTTP, rate limiting, logging, compression, and SQLite use the
Python standard library. `pytest` is a development dependency. `uv.lock` pins
runtime and transitive dependencies; use `uv sync --frozen --no-dev` for runtime
only. Python 3.13 or newer is required.

## Python and CLI

```python
from dotenv import load_dotenv
from data_collection import collect_ticker, collect_tickers, iter_tickers

load_dotenv(".env")  # MASSIVE_API_KEY; legacy POLYGON_IO also works

stock = collect_ticker("AAPL")
prices = stock["datasets"]["prices"]["data"]

# Select a universe; runs sequentially with one shared Massive cooldown.
stocks = collect_tickers(
    ["AAPL", "MSFT", "GOOG"],
    checkpoint_path="output/stocks.checkpoint.sqlite",
)

# Select feeds as well as tickers to control calls and collection time.
stocks = collect_tickers(
    ["AAPL", "MSFT"],
    datasets=["prices", "short_interest", "float", "news"],
    start_date="2026-10-01",
    end_date="2026-10-20",
)

# For long runs, save each completed ticker instead of waiting for all 500.
for stock in iter_tickers(["AAPL", "MSFT"], datasets=["reference", "news"]):
    print(stock["ticker"], stock["datasets"]["news"]["status"])
```

Each result contains `ticker`, `started_at`, `finished_at`, `history_window` and
`datasets`. Each feed has `provider`, `scope`, collection timestamps, a `status`
(`ok`, `empty`, `error`) and JSON-serializable `data`. Failed feeds have `data=None`
and an `error` with a type and sanitized message. Other feeds continue. Empty data
means the provider returned no observations, not a validated economic zero.

| Dataset name | Source | Returned data |
|---|---|---|
| `prices` | Yahoo | Daily OHLC, adjusted close, volume, dividends and splits |
| `cash_flow` | Yahoo | Available quarterly statements, one record per quarter |
| `analyst_actions` | Yahoo | Available dated upgrade/downgrade actions |
| `earnings` | Yahoo | Available quarterly actual/estimated EPS and surprises |
| `options` | Yahoo | Current puts/calls at expirations bracketing 30/90 days |
| `reference` | Massive | Current company, market cap, shares and identifiers |
| `short_interest` | Massive | Dated short interest and days-to-cover reports |
| `float` | Massive | Latest dated float measurement |
| `short_volume` | Massive | Dated off-exchange short-sale activity |
| `news` | Massive | Articles, original insights and ticker-specific insights |
| `insider_transactions` | Massive | All returned Form 4 codes/types for this ticker |

All the above are enabled by default. `income_statement` and `balance_sheet`
are also selectable Yahoo quarterly feeds. Use `datasets=[]` for metadata only.
No Twelve Data or paid Massive endpoint is used. Form 4 is currently free in beta.

Historical feeds default to the last 730 calendar days, with inclusive bounds.
Initial history downloads can require many news pages and take longer than our
daily-update budget. For incremental collection, supply a recent `start_date`;
request enough existing history or combine with saved data for rolling factors.

`current_snapshot` feeds are fetched as they exist **now**, even with a past end
date. Financial statements can be restated; earnings dates label fiscal periods,
not announcement times. Short interest uses settlement dates, not publication
dates. Options can lack maturities bracketing a target, or have stale prices. The
collector preserves source records; it does not certify point-in-time exposures,
compute IV/skew, classify insider trades, or extract guidance from news.

The standalone Massive helper follows every pagination link, retries transient failures,
and spaces request starts by 13 seconds in this process. The earlier two-page
allowances are planning estimates, not truncation limits. Run one process per API
allowance: independent processes do not share its limiter. No auth runs on import.
If a later page fails, that feed is marked `error`; partial pages are not returned.

**If retrieval breaks, rerunning the same command resumes from the last completed
feed checkpoint. Completed feeds are reused; failed or interrupted feeds are
retried.** Checkpointing is enabled automatically when the CLI uses `--output`,
or explicitly with `--checkpoint`. In Python, pass `checkpoint_path`.

Checkpoints are committed after each complete feed and stored as compressed JSON
in SQLite. This is feed-level checkpointing: an interrupted paginated feed starts
again at its first page, and interrupted options retrieval restarts that feed.
Other completed feeds for that ticker are retained. `empty` responses are logged
as warnings and count as completed; errors remain eligible for retry on restart.
Preserved feeds retain their original retrieval timestamps and gain
`resumed_from_checkpoint=True` in the returned dictionary.

The date window is fixed in the checkpoint file. If dates are omitted on restart,
the original dates are reused, including after midnight. Conflicting explicit
dates are rejected. For a **new daily collection**, use a new output/checkpoint
file so current snapshots are refreshed. Keep checkpoint files on a persistent
local volume and use one writer per checkpoint/output pair.

The CLI writes and flushes one JSON line per ticker. On restart it rebuilds that
output using saved feeds and newly fetched results, preventing duplicate ticker
rows and repairing incomplete JSON lines. The compressed SQLite file is the
authoritative recovery store. An existing output without a checkpoint is left
untouched; choose a new output path for legacy files.

```sh
uv run collect-stocks AAPL MSFT --env-file .env --output output/stocks.jsonl
# Restart after an interruption: run exactly the same command.
uv run collect-stocks AAPL MSFT --env-file .env --output output/stocks.jsonl
uv run collect-stocks AAPL --datasets reference float
uv run collect-stocks AAPL MSFT --datasets prices cash_flow \
  --output output/yahoo.jsonl --log-file logs/yahoo-collection.log
```

All collection functions log to `logs/data_collection.log` by default. Set
`log_path` in Python or `--log-file` in the CLI to override it. Logs include UTC
timestamps, ticker, provider, feed, successful retrievals, resume events, empty
responses and sanitized errors. Files rotate at 5 MiB, with three older files
retained. Credentials, raw responses and arbitrary exception text are excluded.
CLI exit codes: 0 for completion without failed feeds, 1 for failures, 130 for
keyboard interruption. Without a checkpoint, the functions still log but cannot
resume. Existing ATS Jenkins jobs and database writes are unaffected.

For 500 tickers, use `--tickers-file universe.txt` instead of a long command line.
The file accepts newline/comma/space-separated symbols and `#` comments. Symbols
are normalized and deduplicated. `--datasets "prices,news"` also accepts one
comma-separated argument, useful for Jenkins parameters.

## Runtime keys

Yahoo needs no API key. For Massive, supply one of:

- `MASSIVE_API_KEY` environment variable (preferred).
- `POLYGON_IO` environment variable for compatibility.
- `--env-file /path/to/collection.env`, using either variable above.
- `--api-key-file /run/secrets/massive`, a plain-text key mounted at runtime.
- `collect_ticker(..., api_key=...)` in Python.

An explicit key file or Python argument overrides environment keys. Credentials
are never build arguments or image contents, and logs exclude their values. The
CLI fails before collection if selected Massive feeds have no configured key.
`.env.example` documents the variable without supplying a real value.

## Docker

Build from this directory, which is the entire Docker build context:

```sh
docker build --target test --tag stock-data-collection:test .
docker build --target runtime --tag piperofthedawn/ats:data-collection .
```

The runtime contains Python and the installed standalone package with its locked
dependencies. Tests, ATS source, development dependencies and credentials are
excluded. It runs as an unprivileged user. The test target runs the offline tests,
including mocked Jenkins Docker commands; it makes no provider calls.

Supply your key in the shell environment, then select a universe:

```sh
mkdir -p state
printf 'AAPL\nMSFT\n' > state/tickers.txt  # Replace with your full universe.
docker run --rm --init --stop-timeout 30 \
  --user "$(id -u):$(id -g)" \
  --env MASSIVE_API_KEY \
  --mount "type=bind,src=$PWD/state,dst=/data" \
  piperofthedawn/ats:data-collection \
  --tickers-file /data/tickers.txt \
  --output /data/runs/initial/stocks.jsonl \
  --log-file /data/runs/initial/collection.log --log-to-stderr
```

`--env MASSIVE_API_KEY` forwards an already-exported key without placing its
value in the command. Alternatively use Docker's `--env-file` or a read-only key
file mount with the collector's `--api-key-file`. The `/data` bind mount retains
output, checkpoints, logs and Yahoo cache after the container is removed. The
host UID/GID override makes bind-mounted files owned by the invoking user.
Stopping the image sends SIGINT so the collector can exit and log an interruption.
Rerun with the same output path to resume; use a different run directory to start
a new daily collection. `--log-to-stderr` streams the same progress to Docker and
Jenkins while retaining the rotating file.

To publish the image after testing, authenticate Docker to your Docker Hub
account, then run:

```sh
docker push piperofthedawn/ats:data-collection
docker image inspect --format '{{json .RepoDigests}}' piperofthedawn/ats:data-collection
```

The push output supplies the registry digest. Use that returned digest when an
immutable image reference is needed.

## Jenkins

Create a Pipeline-from-SCM job on `main`, with script path
`data_collection/Jenkinsfile`. If this directory is copied into a separate
repository, use script path `Jenkinsfile`. Alternatively paste the Jenkinsfile
into a new Pipeline job. The job pulls the published Docker Hub image before
every collection; it needs no Python installation or source checkout to run.
Automatic workspace checkout is disabled.

`IMAGE_REF` defaults to `piperofthedawn/ats:data-collection`, so each build fetches
the current published tag. Set it to `piperofthedawn/ats@sha256:<digest>` to pin a
specific release. Pull failures stop the job before collection starts; the job
does not fall back to a cached image. The pulled registry digest is logged.

For a private repository or authenticated pulls, create a Jenkins **Username
with password** credential containing your Docker Hub username and access token,
then set `DOCKERHUB_CREDENTIAL_ID` to its ID. Login uses standard input and a
temporary Docker configuration that is removed after pulling. Leave it blank
for anonymous pulls or the agent's existing Docker login.

Create a Jenkins **Secret text** credential containing the Massive key, then set
`MASSIVE_CREDENTIAL_ID` to its ID (default: `massive-api-key`). The credential is
bound only around `docker run` and passed as `--env MASSIVE_API_KEY`; its value is
neither interpolated into Groovy nor written into a host env file.

The Jenkins agent must have Docker daemon access on the same host as its writable
`STATE_DIR` (default `/var/lib/jenkins/data-collection`). Required plugins are the
usual Declarative Pipeline/SCM, Credentials Binding and Timestamper plugins. Paste
all 500 tickers into the multiline `TICKERS` parameter; AAPL/MSFT are demo defaults.
The job pulls its runtime image, then executes `docker run --pull=never --rm`
with the selected universe and a persistent bind mount. Building, testing and
publishing a new image remain separate release steps.

Other parameters:

| Parameter | Use |
|---|---|
| `RUN_ID` | Stable run name, default `initial`; reuse to resume, change for a new collection |
| `DATASETS` | Optional comma/space-separated feeds; blank selects all defaults |
| `START_DATE`, `END_DATE` | Optional inclusive history bounds; omitted values reuse saved dates |
| `IMAGE_REF` | Published image tag or digest, default `piperofthedawn/ats:data-collection` |
| `DOCKERHUB_CREDENTIAL_ID` | Optional Docker Hub username/token credential for pulling |
| `STATE_DIR` | Absolute persistent directory on the agent/Docker host |

Files are retained under `STATE_DIR/runs/RUN_ID`: `tickers.txt`, `stocks.jsonl`,
`stocks.jsonl.checkpoint.sqlite`, and `collection.log` (plus rotated logs).
Changing a saved run's ticker selection is rejected; use a new `RUN_ID`. Feed
errors make the job fail while preserving checkpoints, so another build retries
the failed feeds. A timeout or abort stops the container and retains checkpoints.

The job is manual, prohibits concurrent builds of itself and has a 72-hour
timeout. An initial two-year backfill can exceed our daily-update call estimate;
resume the same `RUN_ID` if necessary. Avoid other jobs/processes using the same
free-tier allowance simultaneously: each process has its own 13-second limiter.
This collector job pulls from Docker Hub. Existing ATS pipelines under `jenkins/`
continue to use their local image workflow.
