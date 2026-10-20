# ATS Rust API tools

Read-only Trading 212 v0 client and CLI, with the earlier FRED experiment
retained as a separate library module. This package does not write to Supabase
or replace the Python Gmail/PDF ingestion pipeline.

## Setup

Requires Rust 1.93.1 or newer for the checked-in dependency lockfile.

Generate a Trading 212 API key/secret pair for the intended account and environment.
Grant only the account, portfolio, instrument, and history read permissions needed
by these commands. The secret is shown only once. Optional IP restrictions must
allow the machine making requests.

Export `TRADING212_API_KEY` and `TRADING212_API_SECRET` in your shell or supply them
through your process manager's secret environment. `.env.example` lists the names;
the application **does not automatically load `.env` files**. Do not put real
credentials in source files or command arguments. `.env` and `.env.*` are ignored,
except for the placeholder example.

```sh
cargo build --locked
cargo run --locked -- trading212 --help
cargo run --locked -- trading212 summary
cargo run --locked -- trading212 positions --ticker AAPL_US_EQ
cargo run --locked -- trading212 instruments
cargo run --locked -- trading212 history orders --limit 50 --ticker AAPL_US_EQ
cargo run --locked -- trading212 history dividends --all
cargo run --locked -- trading212 history transactions --time 2026-01-01T00:00:00Z --all
```

The environment defaults to **demo**. Use `trading212 --environment live summary`
for an explicitly selected live account; ensure the supplied credentials match.
Commands never place, change, or cancel orders, and do not generate reports.

All successful commands emit compact JSON to stdout. History defaults to one page
with `items` and `nextPagePath`. `--all` emits **one page per line (NDJSON)**, follows
pagination, and may take a long time for large histories. This output is not one
combined JSON array. Diagnostics go to stderr; failures have nonzero exit status.
If a later page fails, earlier output remains valid but the export is incomplete;
check the exit status before treating a file as a complete history. Avoid running
multiple exports against the same account concurrently.

## What to understand before using the API

Reviewed against the official documentation on 2026-09-26. The API is in beta;
recheck the linked reference when expanding coverage.

### Authentication and environments

HTTP Basic authentication uses the API key as username and secret as password.
The client lets `reqwest` construct the Authorization header, keeps credentials
out of URLs, and does not print response bodies in error messages. Redirects are
disabled. Demo uses `https://demo.trading212.com/api/v0`; live uses
`https://live.trading212.com/api/v0`. Supported accounts are Invest and Stocks ISA;
SIPP is not currently supported.

Sources: [authentication](https://docs.trading212.com/api/section/authentication),
[API key setup and permissions](https://helpcentre.trading212.com/hc/en-us/articles/14584770928157-Trading-212-API-key),
[limitations](https://docs.trading212.com/api/section/general-information/api-limitations).

### Endpoints and account-wide budgets

| Command | GET path below `/api/v0` | Documented limit |
| --- | --- | --- |
| `summary` | `/equity/account/summary` | 1 request / 5 seconds |
| `positions` | `/equity/positions` | 1 request / second |
| `instruments` | `/equity/metadata/instruments` | 1 request / 50 seconds |
| `history orders` | `/equity/history/orders` | 6 requests / minute |
| `history dividends` | `/equity/history/dividends` | 6 requests / minute |
| `history transactions` | `/equity/history/transactions` | 6 requests / minute |

Limits are shared by API keys and IP addresses for the same account. The client
paces requests conservatively (history requests are spaced ten seconds apart),
and observes `x-ratelimit-limit`, `period`, `remaining`, `reset`, and `used` headers.
The reset value is a Unix timestamp. Cloned clients share scheduling state;
separate clients or processes do not coordinate, so server limits remain authoritative.
Instrument metadata is refreshed by Trading 212 every ten minutes; the client
fetches it on request and does not keep a persistent cache.

Sources: [rate limits](https://docs.trading212.com/api/section/rate-limiting),
[headers](https://docs.trading212.com/api/section/rate-limiting/response-headers),
[summary](https://docs.trading212.com/api/accounts/getaccountsummary.md),
[positions](https://docs.trading212.com/api/positions/getpositions.md),
[instruments](https://docs.trading212.com/api/instruments/instruments.md),
[history](https://docs.trading212.com/api/historical-events).

### Pagination and data meaning

History page sizes are 1–50. Follow the returned `nextPagePath` until null; it
already contains `/api/v0`. The pager checks the origin and endpoint before
sending credentials, and rejects repeated page paths. Order/dividend history
accepts a Trading 212 ticker filter; transactions accept a starting time. The CLI
requires a timezone-qualified RFC 3339 time. Cursors are treated as opaque API data.

Tickers such as `AAPL_US_EQ` are Trading 212 identifiers, not Yahoo symbols.
Instrument metadata includes ISIN and currency. Position `averagePricePaid` and
`currentPrice` are in instrument currency, while `walletImpact` has its own currency.
Do not assume every number in a response uses the account currency. Transaction
amounts use the transaction currency; dividend payloads also distinguish account
and instrument amounts.

Payloads remain JSON, including unknown fields, and `serde_json` arbitrary precision
preserves numeric values without passing through `f64`. This is transport tooling:
it does not calculate NAV, convert currencies, normalize dates, or define accounting
rules. Before replacing the PDF pipeline, reconcile statement dates, snapshot times,
account totals, FX, and cash flows against actual statements. The documented summary
and position endpoints provide current state, not a dated statement-equivalence guarantee.

Sources: [pagination](https://docs.trading212.com/api/section/pagination/example),
[position fields](https://docs.trading212.com/api/positions/getpositions.md),
[transactions](https://docs.trading212.com/api/historical-events/transactions.md),
[dividends](https://docs.trading212.com/api/historical-events/dividends.md).

### Failures and retries

The reusable HTTP client has a ten-second connect timeout and thirty-second request
timeout. GET requests get at most three attempts for transport failures, HTTP 408,
429, or 5xx. Backoff is one then two seconds, extended by endpoint pacing,
`Retry-After` (seconds or HTTP date), and rate-limit reset information. An automatic
wait exceeding 120 seconds returns an error with the remaining wait instead.
401/403 errors identify authentication, environment, permission, or IP restriction
checks without exposing credentials. Invalid JSON is an error and is not retried.

There is no arbitrary-URL or write-request CLI. A future trading implementation needs
its own explicit design for execution, duplicate submissions, uncertain outcomes,
and reconciliation; GET retry rules must not simply be reused for order submission.

## Library

`Trading212Client::from_env(Environment::Demo)` constructs a reusable client.
`summary`, `positions`, and `instruments` return `ApiResponse<serde_json::Value>`.
`orders`, `dividends`, and `transactions` accept `HistoryOptions` and return a
single `ApiResponse<Page<Value>>`. `history(kind, options)` returns a `HistoryPager`;
call `next_page().await` until it returns `None`. `ApiResponse` includes parsed
`RateLimit` metadata. Configuration, transport, HTTP, decoding, and pagination
failures are represented by `ApiError`.

The original FRED helper is `ats_rust::fred::get_data_from_fred(&client, series, key)`.
It now parses JSON, checks HTTP status, and strips the credential-bearing URL from
returned HTTP errors. It is not invoked by the Trading 212 commands.

## Validation

```sh
cargo fmt --check
cargo check --locked
cargo test --locked
cargo clippy --locked --all-targets -- -D warnings
```

Tests use a local mock HTTP server and dummy credentials. They cover auth, routing,
query encoding, decimal precision, pagination, rate scheduling, retries, error
redaction, and CLI validation. They do not prove account-specific permissions or
live service compatibility. For an authenticated smoke check, supply demo credentials
and run `cargo run --locked -- trading212 --environment demo summary`.
