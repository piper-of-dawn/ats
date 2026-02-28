from datetime import date
import os

import psycopg
import pytest
from dotenv import load_dotenv

TARGET_TABLE = "test_table_metrics"
SOURCE_TABLE = "us_midcap"
RUN_DATE = date(2099, 1, 1)


def _count_rows(conn, tickers):
    with conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT COUNT(*)
            FROM {TARGET_TABLE}
            WHERE ticker = ANY(%s) AND as_of_date = %s
            """,
            (tickers, RUN_DATE),
        )
        return cur.fetchone()[0]


def _delete_rows(conn, tickers):
    with conn.cursor() as cur:
        cur.execute(
            f"""
            DELETE FROM {TARGET_TABLE}
            WHERE ticker = ANY(%s) AND as_of_date = %s
            """,
            (tickers, RUN_DATE),
        )


def test_run_jobs_writes_to_test_table_metrics_from_us_midcap():
    load_dotenv()
    if not os.getenv("SUPABASE_PASSWORD"):
        pytest.skip("SUPABASE_PASSWORD is required for this integration test")

    try:
        from ats.dataIO.supabase_integration import _get_conn_params, table_exists
        from ats.jobs import build_jobs, run_jobs
    except ModuleNotFoundError as exc:
        if exc.name == "yfinance":
            pytest.skip("yfinance is required to execute run_jobs integration test")
        raise

    if not table_exists(SOURCE_TABLE):
        pytest.skip(f"Supabase table {SOURCE_TABLE} is not available")
    if not table_exists(TARGET_TABLE):
        pytest.skip(f"Supabase table {TARGET_TABLE} is not available")

    jobs = build_jobs(SOURCE_TABLE)[:10]
    assert len(jobs) == 10
    tickers = [job["ticker"] for job in jobs]

    conn_params = _get_conn_params()
    with psycopg.connect(**conn_params) as conn:
        before = _count_rows(conn, tickers)
        try:
            run_jobs(jobs, table_name="test_table", as_of_date=RUN_DATE)
            after = _count_rows(conn, tickers)
            assert after >= before + len(jobs)
        finally:
            _delete_rows(conn, tickers)
            conn.commit()
            assert _count_rows(conn, tickers) == 0
