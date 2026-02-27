from datetime import date
from multiprocessing import get_context

import polars as pl
from tqdm import tqdm

from ats.dataIO.supabase_integration import batch_insert_polars_df, fetch_table
from ats.processing import process_ticker


def build_jobs(table_name):
    rows = fetch_table(table_name)
    jobs = []
    for row in rows.iter_rows(named=True):
        ticker = row.get("yahoo_finance_ticker")
        mkt_index = row.get("representative_index_ticker")
        if ticker and mkt_index:
            jobs.append(
                {
                    "ticker": ticker,
                    "representative_index_ticker": mkt_index,
                }
            )
    return jobs


def run_jobs(jobs, as_of_date=None):
    if as_of_date is None:
        as_of_date = date.today()

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
        .sort("ltm", descending=True)
        .sort("stm", descending=True)
        .with_columns(pl.lit(as_of_date).alias("as_of_date"))
    )
    batch_insert_polars_df(df=df, columns=df.columns, table_name="factor_metrics")
    return df
