# ATS

## Ticker data access

All Polygon/Massive HTTP requests use `ats.dataIO.massive.request_json`.
Set `POLYGON_IO` in the calling environment, or pass `api_key` explicitly.
The default server is `https://api.massive.com`; `POLYGON_API_BASE_URL` can
override it. Financial-statement and dividend functions also retain their
legacy `env.toml` credential fallback.

```python
from ats.ticker import EquityTicker

stock = (
    EquityTicker("AAPL")
    .get_short_interest()
    .get_short_volume()
    .get_float()
    .get_news_sentiment()
)

print(stock.short_interest)
print(stock.news_sentiment)

# Fetch all reports within an inclusive date range.
stock.get_short_interest("2026-08-01", "2026-09-26")
```

Each method returns the ticker and stores a list of source records on the
corresponding data attribute. Attributes start as `None`; a successful
empty response stores `[]`. Calls refresh the data and clear the attribute
before fetching, so failed refreshes leave `None` and raise `MassiveAPIError`.
Invalid date ranges raise `ValueError`.

| Fetch method | Source-record attribute |
| --- | --- |
| `get_short_interest()` | `short_interest` |
| `get_short_volume()` | `short_volume` |
| `get_float()` | `float_data` |
| `get_news_sentiment()` | `news_sentiment` |

The former `get_massive_*` methods and `massive_*` attributes have been renamed;
update callers to the names above. Provider naming stays inside the HTTP adapter.

Short-interest and short-volume methods fetch only the latest available record
by default. Supply `start_date` to fetch paginated history; `end_date` defaults
to today. An end date alone selects the latest record on or before that date.
Date arguments accept `datetime.date` or ISO `YYYY-MM-DD` strings.
`get_float()` fetches only the latest float record (or none), retaining the
provider's effective date; it does not download float history. News defaults
to seven UTC calendar dates including today (or the supplied end date), with
inclusive date bounds.
Articles retain original `insights` and add `ticker_insights` filtered to the
requested ticker. Fetching alone does not calculate scores.

The shared helper serializes requests and spaces starts at least 13 seconds
apart. Every pagination request and retry uses the same limiter. It retries
connection failures, HTTP 429 and HTTP 5xx up to three total attempts, waiting
at least 60 seconds or a longer `Retry-After`. Each attempt has a 30-second
timeout. Other failures raise immediately. Credentials are sent in headers
and excluded from helper error messages.

The limiter coordinates **one process only**. Separate processes or applications
using the same key must coordinate their own schedules. Historical access and
dataset availability remain subject to the account's provider entitlements.

Existing `get_*_parallel` batch entrypoints remain compatible but now execute
sequentially; their worker, batch-size and batch-sleep arguments are ignored.
The shared helper owns pacing. Existing option-premium calculations are retained;
there is no new IV method, scheduled collector or database integration for these
four ticker methods.

## Analysis from loaded ticker data

Analysis functions accept an `EquityTicker` with the needed records already loaded.
They make no API calls and do not modify the ticker. Price loading is also
explicit: reading `price_data` on a new ticker returns `None`.

```python
from ats.momentum import calculate_momentum
from ats.fundamentals.analyst_metrics import calculate_analyst_metrics

market = EquityTicker("^GSPC").fetch_price_data()
stock = EquityTicker("AAPL", mkt_index=market).fetch_price_data().fetch_analyst_data()
momentum = calculate_momentum(stock)  # beta, stm, ltm, and prepared returns
analyst = calculate_analyst_metrics(stock)  # rating and target deviation
```

`ats.momentum.MomentumConfig` controls lookback, winsorization, raw versus
idiosyncratic returns, half lives, and volatility model. Both signals use the
same fresh price window. `prepare_returns(stock, config=...)` returns the
aligned returns and beta without calculating signals.

The functions in `ats.shorts` and `ats.positioning` return chronological
**Polars DataFrames**. Short calculations live in `ats.shorts`; news calculations
live in `ats.positioning`.

| Analysis function | Output and defaults |
| --- | --- |
| `short_interest_trend(stock)` | Shares short, report-to-report change in shares and percent, consecutive increases. |
| `days_to_cover_trend(stock)` | Provider-reported days to cover, change in days and percent, consecutive increases. |
| `short_volume_anomaly(stock, recent_window=5, baseline_window=60)` | Short-sale percentage, recent and baseline means, baseline sample standard deviation, percentage-point difference and anomaly score. Windows use returned trading observations. |
| `short_interest_as_a_percentage_of_float(stock, max_float_age_days=None)` | Each historical shares-short observation divided by the latest available free float, times 100. The denominator is fixed across the series; its effective date is included. An explicit age limit opts into dated matching instead. |
| `news_and_sentiment_trend(stock)` | Daily unique-article count, positive/negative/neutral/unlabeled counts, labeled count and sentiment score. |
| `news_attention_anomaly(stock, recent_window=7, baseline_window=60)` | Recent daily article-count mean versus a preceding baseline of calendar days, including days with no returned articles. |

```python
from datetime import date, timedelta
from ats import positioning, shorts
from ats.ticker import EquityTicker

start = date.today() - timedelta(days=180)
stock = (
    EquityTicker("IREN")
    .get_short_interest(start_date=start)
    .get_short_volume(start_date=start)
    .get_float()
    .get_news_sentiment(start_date=start)
)

shorts.short_interest_trend(stock)
shorts.days_to_cover_trend(stock)
shorts.short_volume_anomaly(stock)
shorts.short_interest_as_a_percentage_of_float(stock)
positioning.news_and_sentiment_trend(stock)
positioning.news_attention_anomaly(stock)
```

Anomaly scores are `(recent_mean - baseline_mean) / baseline_std`; the recent
window is excluded from the baseline. These are descriptive standardized
differences, not significance tests. Insufficient history, invalid denominators,
missing measurements and zero baseline variance produce null results. Default
windows need 65 short-volume observations or 67 calendar days of fetched news.

Sentiment is `(positive - negative) / labeled_count`, ranging from -1 to +1.
Unlabeled or conflicting labels are excluded from that denominator but included
in attention counts. News is deduplicated by ID, article URL or normalized title
on the same UTC date; this does not detect every paraphrased syndicated story.
Daily sentiment is null when there are no labeled articles.

News analyses default to the fetched date range. Optional `start_date` and
`end_date` may restrict it but cannot extend it. When assigning externally loaded
news records directly, provide both bounds and ensure that range was fully fetched.
A zero count means no articles returned by the source, not universal news absence.

Float percentages stay null without a compatible float date. Set
`max_float_age_days` explicitly to permit carrying an earlier measurement forward;
a future float is never used for an earlier short-interest report. Date matching
does not prove that a historical observation was publicly available on that date.
Short-sale volume measures trading activity, not the buildup in outstanding shorts.
