from datetime import date
import os

import psycopg
import pytest
from dotenv import load_dotenv


def _count_rows(conn, tickers, as_of_date):
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT COUNT(*)
            FROM factor_metrics
            WHERE ticker = ANY(%s) AND as_of_date = %s
            """,
            (tickers, as_of_date),
        )
        return cur.fetchone()[0]


def _delete_rows(conn, tickers, as_of_date):
    with conn.cursor() as cur:
        cur.execute(
            """
            DELETE FROM factor_metrics
            WHERE ticker = ANY(%s) AND as_of_date = %s
            """,
            (tickers, as_of_date),
        )


def test_run_jobs_writes_and_cleans_first_10_us_midcap_rows():
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

    source_table = "us_midcap"
    if not table_exists(source_table):
        source_table = "us_midcap400"
    if not table_exists(source_table):
        pytest.skip("Neither us_midcap nor us_midcap400 table is available")
    if not table_exists("factor_metrics"):
        pytest.skip("Supabase table factor_metrics is not available")

    jobs = build_jobs(source_table)[:10]
    assert len(jobs) == 10
    tickers = [job["ticker"] for job in jobs]
    run_date = date(2099, 1, 1)

    conn_params = _get_conn_params()
    with psycopg.connect(**conn_params) as conn:
        before = _count_rows(conn, tickers, run_date)
        try:
            run_jobs(jobs, as_of_date=run_date)
            after = _count_rows(conn, tickers, run_date)
            assert after >= before + len(jobs)
        finally:
            _delete_rows(conn, tickers, run_date)
            conn.commit()
            cleaned = _count_rows(conn, tickers, run_date)
            assert cleaned == 0
