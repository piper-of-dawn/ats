# AGENTS.md

## Purpose
This repo has two related concerns:
- Factor computation pipeline (Python package under `src/ats`)
- Lightweight dashboard deployment on Vercel (`api/index.py`)

The dashboard reads directly from Supabase/Postgres and renders an HTML table.

## High-Level Layout
- `main.py`: CLI entrypoint (delegates to `ats.cli.main`).
- `src/ats/`: Core package.
- `src/ats/jobs.py`: Batch orchestration.
  - `build_jobs(table_name)`: builds `{ticker, representative_index_ticker}` jobs.
  - `run_jobs(jobs, as_of_date=None)`: runs multiprocessing pipeline, writes to `factor_metrics`.
- `src/ats/processing.py`: per-ticker processing logic (`process_ticker`).
- `src/ats/dataIO/supabase_integration.py`: DB I/O for local/backend pipeline.
  - `fetch_table`, `batch_insert`, `batch_insert_polars_df`, `table_exists`.
- `src/ats/dashboard.py`: local Flask dashboard app (package-level app).
- `api/index.py`: Vercel Flask function for production dashboard deployment.
- `api/requirements.txt`: Vercel-only minimal Python dependencies.
- `tests/test_run_jobs_integration.py`: integration test for `run_jobs` write+cleanup behavior.

## Data Flow
1. Source universe table (e.g., `us_midcap` / `us_midcap400`) is read from Supabase.
2. Jobs are created from `yahoo_finance_ticker` and `representative_index_ticker`.
3. `run_jobs` processes in parallel (`spawn` context) and computes `stm`, `ltm`, `beta`.
4. Results are inserted into `factor_metrics` with `as_of_date`.
5. Vercel dashboard can display any table via query param `?table=<name>`.

## Local Commands
- Install/sync deps: `uv sync`
- Run pipeline CLI: `uv run python main.py <table_name>` (or package CLI if configured)
- Run local dashboard: `uv run dashboard-local`
- Run tests: `uv run pytest -q`
- Run integration test only: `uv run pytest tests/test_run_jobs_integration.py -q -rs`
- Build wheel: `just build`

## CI/CD Architecture
- CI/CD is Jenkins-only. GitHub Actions and GitHub webhooks are not part of the active deployment path.
- Jenkins uses a multibranch pipeline with script path `jenkins/Jenkinsfile`.
- Jenkins should be configured to discover only `main`, with periodic scans instead of webhook triggers.
- On `main`, Jenkins builds the wheel with `just build`, optionally runs `uv run pytest -q`, then builds the Docker image locally as `ats:latest`.
- Deployment is local to the Jenkins machine. The pipeline writes `docker-compose.yml` and `deploy.env` into `DEPLOY_PATH` and runs `docker compose up -d`.
- Docker registry push/pull is intentionally not used in the active setup.
- After deployment, Jenkins can prune dangling images and builder cache to limit disk growth.

## Docker/Scheduler Notes
- The Docker image is built from the prebuilt wheel in `dist/`; it does not copy the source tree directly.
- The container is a scheduler container, not a one-shot CLI container.
- `docker/cron-entrypoint.sh` creates a cron job that runs daily at `08:00 UTC`.
- Runtime command selection is passed through `ATS_COMMANDS`, a comma-separated list of table names. Each command is executed sequentially by the cron runner.
- The image includes `cron` and `libpq5` so scheduled jobs can run `psycopg` successfully inside the container.
- Host-side deploy metadata lives in `DEPLOY_PATH`, typically `/var/lib/jenkins/ats`. Keep only `docker-compose.yml`, `deploy.env`, and `app.env` there.

## Vercel Deployment Notes
- Vercel config is in `vercel.json` and targets `api/index.py` with `@vercel/python`.
- Keep Vercel deps minimal in `api/requirements.txt`.
- `.vercelignore` excludes backend-heavy files (including `uv.lock`, `pyproject.toml`) so Vercel does not install full backend deps.

## Environment Variables
### Local/backend pipeline
- `SUPABASE_PASSWORD` used by `src/ats/dataIO/supabase_integration.py`.

### Jenkins/Docker deployment
- `ATS_COMMANDS`: comma-separated table names run by the scheduler container, e.g. `us_midcap,us_smallcap`.
- `SUPABASE_PASSWORD` and any DB connection env vars should be provided through the runtime app env file referenced by `DEPLOY_APP_ENV_FILE`.

### Vercel dashboard (`api/index.py`)
Connection priority:
1. `SUPABASE_DB_URL`
2. `DATABASE_URL`
3. `POSTGRES_URL`
4. `POSTGRES_URL_NON_POOLING`
5. Fallback host/user/password using `SUPABASE_PASSWORD`

`api/index.py` sanitizes DSN query params before connecting to avoid psycopg URI parsing issues.

## Known Gotchas
- Supabase pooler + prepared statements can trigger `DuplicatePreparedStatement`.
  - Mitigation in code: `prepare_threshold=None` on psycopg connections.
- Multiprocessing fork warnings in tests:
  - Mitigation in code: `get_context("spawn")` in `run_jobs`.
- Vercel builds can accidentally pull backend lock/deps if `.vercelignore` is missing or incorrect.
- Jenkins must have access to the local Docker daemon; the `jenkins` user needs permission to access `/var/run/docker.sock`.
- Jenkins agents on Arch need `postgresql-libs` installed or `psycopg` imports will fail due to missing `libpq`.
- `DEPLOY_PATH` must be writable by the `jenkins` user. `/var/lib/jenkins/ats` is the intended default; avoid root-owned deploy directories unless explicitly managed.

## Conventions for Future Changes
- Keep Vercel function isolated and minimal; avoid importing heavy package modules there.
- Keep SQL table names identifier-safe using `psycopg.sql.Identifier`.
- For integration tests that write rows, always clean up in `finally`.
- Prefer adding small, explicit functions over broad module-level side effects.
- Do not reintroduce container registry pushes or remote deploy hops unless the deployment architecture is intentionally changed.
