# AGENTS.md

## Purpose
This repo has two related concerns:
- Factor computation pipeline (Python package under `src/ats`)
- Gmail-driven Trading 212 PDF ingestion for `fund_nav` and `positions`
- Lightweight dashboard deployment on Vercel (`dashboard/index.py`)

The dashboard reads directly from Supabase/Postgres and renders an HTML table.

## High-Level Layout
- `src/ats/`: Core package.
- `src/ats/orchestration.py`: Dagster dynamic orchestration entrypoint.
  - Fetches source tickers from Supabase.
  - Dynamically maps per-ticker factor computation.
  - Writes the final factor matrix to Supabase via `batch_insert_polars_df`.
- `src/ats/dataIO/supabase_integration.py`: DB I/O for local/backend pipeline.
  - `fetch_table`, `batch_insert`, `batch_insert_polars_df`, `delete_all_rows`, `delete_rows_by_values`, `table_exists`.
- `src/ats/dataIO/statement_table.py`: parses Trading 212 statement summary rows from PDF into `StatementTable`.
- `src/ats/dataIO/open_positions.py`: parses Trading 212 open positions from PDF into `Position` rows.
- `src/ats/gmail_sync.py`: scheduled Gmail ingestion flow.
  - Reads latest `fund_nav` date from Postgres.
  - Runs `gmail-downloader [YYYY-MM-DD]`.
  - Parses new PDFs and refreshes `fund_nav` plus `positions`.
- `src/ats/dashboard.py`: local Flask dashboard app (package-level app).
- `dashboard/index.py`: Vercel Flask function entrypoint for production dashboard deployment.
- `dashboard/requirements.txt`: Vercel-only minimal Python dependencies.

## Data Flow
1. Source universe table (e.g., `us_midcap` / `us_midcap400`) is read from Supabase.
2. Dagster dynamic mapping creates one per-ticker factor computation from `yahoo_finance_ticker`.
3. `EquityTicker` computes `stm`, `ltm`, `beta`, analyst rating, and analyst price target deviation.
4. The final factor matrix is inserted into the configured metrics table with `as_of_date`.
5. Gmail sync reads the latest `fund_nav.date`, downloads newer Trading 212 PDFs, inserts fresh `fund_nav` rows from statement tables, then truncates and reloads `positions` from the newest PDF's open positions section.
6. Vercel dashboard can display any table via query param `?table=<name>`.

## Local Commands
- Install/sync deps: `uv sync`
- Run factor orchestration: `uv run ats <source_table> <target_table> <market_index>`
- Run option risk premium update: `uv run update-option-risk-premium <largecap|midcap|us_largecap|us_midcap>`
- Run Gmail sync locally: `uv run trading212-gmail-sync`
- Run local dashboard: `uv run dashboard-local`
- Run tests: `uv run pytest -q`
- Build wheel: `just build`

## CI/CD Architecture
- CI/CD is Jenkins-only. GitHub Actions and GitHub webhooks are not part of the active deployment path.
- Jenkins uses four pipeline jobs stored in repo:
  - `jenkins/Jenkinsfile`: multibranch build pipeline for `main`.
  - `jenkins/Jenkinsfile.run`: scheduled pipeline that runs the already-built Docker image.
  - `jenkins/Jenkinsfile.gmail`: scheduled pipeline that runs the Gmail statement import at `07:00` in `Europe/Berlin`.
  - `jenkins/Jenkinsfile.option-risk-premium`: manual long-running pipeline that updates `option_implied_risk_premium` for `us_largecap_metrics` and `us_midcap_metrics`.
- Jenkins should be configured to discover only `main`, with periodic scans instead of webhook triggers.
- The build pipeline checks whether the current `main` commit differs from the last successfully built commit recorded on the Jenkins host. It rebuilds only when a new commit is present, unless `FORCE_REBUILD=true`.
- On rebuild, Jenkins builds the wheel with `just build`, optionally runs `uv run pytest -q`, then builds the Docker image locally as `ats:latest`.
- The scheduled run job is responsible for the daily execution of ATS commands at `08:00` in `Europe/Berlin` time, which covers CET/CEST automatically.
- The Gmail sync job is responsible for the daily Trading 212 statement import at `07:00` in `Europe/Berlin` time, which covers CET/CEST automatically.
- The option risk premium job is intentionally separate and manual because Polygon free-tier option aggregate calls make the full large/midcap update long-running.
- Docker registry push/pull is intentionally not used in the active setup.
- After a rebuild, Jenkins can prune dangling images and builder cache to limit disk growth.

## Docker/Scheduler Notes
- The Docker image is built from the prebuilt wheel in `dist/`; it does not copy the source tree directly.
- The image contains `/usr/local/bin/run-ats-commands.sh`, which runs each table listed in `ATS_COMMANDS` sequentially.
- The image installs `poppler-utils`, so `pdftotext` is available for Trading 212 PDF parsing inside the container.
- `docker/cron-entrypoint.sh` still supports the legacy in-container cron mode, but the active deployment path should prefer Jenkins scheduling via `jenkins/Jenkinsfile.run`.
- The image includes `cron` and `libpq5` so both legacy cron mode and direct Jenkins-triggered runs can execute `psycopg` successfully inside the container.
- Host-side Jenkins state for image rebuild detection should live in `BUILD_STATE_DIR`, typically `/var/lib/jenkins/ats-build`.
- Runtime env files for the scheduled run job should live in a Jenkins-managed path such as `/var/lib/jenkins/ats/app.env`.
- Gmail runtime state should live in a Jenkins-managed host path such as `/var/lib/jenkins/ats-gmail`, with persistent `config/token.json`, `output/.state.json`, and downloaded PDFs mounted into the container.

## Vercel Deployment Notes
- Vercel config is in `vercel.json` and targets `dashboard/index.py` with `@vercel/python`.
- Keep Vercel deps minimal in `dashboard/requirements.txt`.
- `.vercelignore` excludes backend-heavy files (including `uv.lock`, `pyproject.toml`) so Vercel does not install full backend deps.

## Environment Variables
### Local/backend pipeline
- `SUPABASE_PASSWORD` used by `src/ats/dataIO/supabase_integration.py`.

### Jenkins/Docker deployment
- `ATS_COMMANDS`: comma-separated table names run sequentially inside the container, e.g. `us_midcap,us_smallcap`.
- `SUPABASE_PASSWORD` and any DB connection env vars should be provided through the runtime app env file passed to the scheduled Jenkins run job.
- `POLYGON_IO` must be present in the runtime app env file for option risk premium updates.
- Gmail sync runtime env file is generated by `jenkins/Jenkinsfile.gmail` on each run and should include:
  - `GMAIL_QUERY`
  - `OUTPUT_DIR`
  - `OAUTH_CLIENT_SECRET_FILE`
  - `TOKEN_FILE`
  - `STATE_FILE`
  - `MAX_RESULTS`
  - `DRY_RUN`
- Jenkins credentials for Gmail sync:
  - Secret file credential for the Google Desktop OAuth client JSON, typically `gmail-oauth-client-secret`.
  - Optional Secret file credential for a bootstrap `token.json`, used only for the first run if the persistent runtime token file does not exist yet.

## Gmail Downloader Notes
- The repo vendors the Gmail downloader and PDF parsers locally under `src/ats/`; it does not need the external `gmail_parser` wheel.
- Console script: `gmail-downloader`, which dispatches to `ats.gmail_downloader:main`.
- Source methods used by this repo:
  - `ats.gmail_downloader.download_pdfs(after_date=None)`: downloads matching Gmail PDF attachments, appending Gmail `after:` filtering when a date is provided.
  - `ats.dataIO.statement_table.parse_statement_table(pdf_path)`: parses first-page account summary values, including `account_value`, from a Trading 212 PDF.
  - `ats.dataIO.statement_table.parse_statement_tables(directory)`: batch parses all PDFs in a directory.
  - `ats.dataIO.open_positions.parse_open_positions(pdf_path)`: extracts invest open positions rows with `Ticker`, `ISIN`, `Currency`, `Value`, and derived `Country`.
- Gmail downloader behavior:
  - Loads optional `config/.env`, then reads env vars directly.
  - Requires a Google OAuth Desktop app JSON, not a web client JSON.
  - Persists token JSON to `TOKEN_FILE`.
  - Persists attachment dedupe state by `messageId:attachmentId` in `STATE_FILE`.
  - Names downloads like `YYYY-MM-DD_<message-prefix>_<filename>.pdf`.

### Vercel dashboard (`dashboard/index.py`)
Connection priority:
1. `SUPABASE_DB_URL`
2. `DATABASE_URL`
3. `POSTGRES_URL`
4. `POSTGRES_URL_NON_POOLING`
5. Fallback host/user/password using `SUPABASE_PASSWORD`

`dashboard/index.py` imports the Flask app from `dashboard.app` for Vercel deployment.

## Known Gotchas
- Supabase pooler + prepared statements can trigger `DuplicatePreparedStatement`.
  - Mitigation in code: `prepare_threshold=None` on psycopg connections.
- Gmail scheduled jobs cannot complete first-time OAuth interactively.
  - Mitigation in deployment: pre-seed `/var/lib/jenkins/ats-gmail/config/token.json` from a Jenkins Secret file credential, then allow the persistent host-mounted token to refresh in place on later runs.
- Trading 212 PDF parsing depends on `pdftotext`.
  - Mitigation in Docker image: install `poppler-utils`.
- Vercel builds can accidentally pull backend lock/deps if `.vercelignore` is missing or incorrect.
- Jenkins must have access to the local Docker daemon; the `jenkins` user needs permission to access `/var/run/docker.sock`.
- Jenkins agents on Arch need `postgresql-libs` installed or `psycopg` imports will fail due to missing `libpq`.
- `BUILD_STATE_DIR` and any runtime env file path used by Jenkins must be writable/readable by the `jenkins` user.

## Conventions for Future Changes
- Keep the Vercel entrypoint isolated and minimal; avoid adding heavy logic there.
- Keep SQL table names identifier-safe using `psycopg.sql.Identifier`.
- For integration tests that write rows, always clean up in `finally`.
- Prefer adding small, explicit functions over broad module-level side effects.
- Do not reintroduce container registry pushes or remote deploy hops unless the deployment architecture is intentionally changed.
- Prefer Jenkins-scheduled `docker run` over in-container cron for production scheduling.
