from datetime import date
from multiprocessing import get_context

import polars as pl
from tqdm import tqdm

from ats.dataIO.supabase_integration import batch_insert_polars_df, fetch_table
from ats.processing import process_ticker
from ats.ticker import EquityTicker


def build_jobs(table_name):
    rows = fetch_table(table_name)
    jobs = []
    for row in rows.iter_rows(named=True):
        ticker = _normalize_ticker(row.get("yahoo_finance_ticker"))
        mkt_index = _normalize_ticker(row.get("representative_index_ticker"))
        if ticker and mkt_index:
            jobs.append(
                {
                    "ticker": ticker,
                    "representative_index_ticker": mkt_index,
                }
            )
    return jobs


def _normalize_ticker(value):
    if value is None:
        return None
    ticker = str(value).strip()
    return ticker or None


def _prepare_market_index_data(jobs):
    market_index_data = {}
    market_indexes = sorted({job["representative_index_ticker"] for job in jobs})
    for mkt_index in market_indexes:
        try:
            mkt = (
                EquityTicker(mkt_index)
                .fetch_price_data()
                .make_log_returns()
                .winsorize_log_returns()
            )
            market_index_data[mkt_index] = {"market_price_data": mkt.price_data}
        except Exception as exc:
            market_index_data[mkt_index] = {
                "market_error": f"market index {mkt_index} error={exc}"
            }
    return market_index_data


def _attach_market_index_data(jobs, market_index_data):
    prepared_jobs = []
    for job in jobs:
        prepared_job = dict(job)
        prepared_job.update(market_index_data[job["representative_index_ticker"]])
        prepared_jobs.append(prepared_job)
    return prepared_jobs


def run_jobs(jobs, table_name, as_of_date=None):
    if as_of_date is None:
        as_of_date = date.today()

    jobs = _normalize_jobs(jobs)
    if not jobs:
        return pl.DataFrame(
            schema={
                "ticker": pl.String,
                "stm": pl.Float64,
                "ltm": pl.Float64,
                "beta": pl.Float64,
                "as_of_date": pl.Date,
            }
        )

    market_index_data = _prepare_market_index_data(jobs)
    jobs = _attach_market_index_data(jobs, market_index_data)

    results = []
    ctx = get_context("spawn")
    with ctx.Pool() as pool, tqdm(total=len(jobs), desc="Processing tickers") as pbar:
        for result in pool.imap_unordered(process_ticker, jobs):
            pbar.set_description(
                f"Processing {result['ticker']} with market index {result['mkt_index']}"
            )
            pbar.set_postfix_str(result["status"])
            result.pop("status", None)
            result.pop("mkt_index", None)
            results.append(result)
            pbar.update(1)

    df = (
        pl.DataFrame(results)
        .with_columns(
            pl.col(col).cast(pl.Float64, strict=False).round(2)
            for col in ["ltm", "stm", "beta"]
        )
        .sort(["ltm", "stm"], descending=True)
        .with_columns(pl.lit(as_of_date).alias("as_of_date"))
    )
    batch_insert_polars_df(
        df=df,
        columns=df.columns,
        table_name=f"{table_name}_metrics",
        conflict_columns=["ticker", "as_of_date"],
    )
    return df


def _normalize_jobs(jobs):
    normalized_jobs = []
    for job in jobs:
        ticker = _normalize_ticker(job.get("ticker"))
        mkt_index = _normalize_ticker(job.get("representative_index_ticker"))
        if ticker and mkt_index:
            normalized_jobs.append(
                {
                    "ticker": ticker,
                    "representative_index_ticker": mkt_index,
                }
            )
    return normalized_jobs
