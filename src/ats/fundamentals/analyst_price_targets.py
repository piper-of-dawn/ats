from yfinance import Ticker
from datetime import date
import time
import random
import polars as pl
from ats.dataIO.supabase_integration import fetch_table, batch_insert_polars_df
from ats.dataIO.utils import with_parallel_runner

def median_centered_score(d: dict) -> float:
    current = float(d["current"])
    high = float(d["high"])
    low = float(d["low"])
    median = float(d["median"])

    denom = max(high - median, median - low)
    if denom == 0:
        return 0.0
    return (current - median)/ denom


@with_parallel_runner(
    item_name="ticker",
    desc="Computing analyst price targets",
    unit="ticker",
)
def analyst_price_target(ticker: str) -> dict:
    time.sleep(random.uniform(0.2, 0.75))
    price_targets = Ticker(ticker).get_analyst_price_targets()
    if not isinstance(price_targets, dict) or not price_targets:
        raise ValueError("No analyst price targets returned")
    return {"analyst_price_target_deviation": round(median_centered_score(price_targets), 2)}


def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="Get the latest analyst price targets"
    )
    parser.add_argument("table", nargs="?", help="Table name (positional or via --table)")
    parser.add_argument("--table", dest="table_named", help="Table name (named argument)")
    args = parser.parse_args()

    table_name = args.table_named or args.table
    df = fetch_table(table_name).drop_nulls()
    tickers = df["yahoo_finance_ticker"].to_list()
    df = analyst_price_target.parallel(tickers)[0].with_columns(
        pl.lit(date.today()).alias("as_of_date"),
    )
    batch_insert_polars_df(
        df,
        ["ticker", "as_of_date", "analyst_price_target_deviation"],
        f"{table_name}_metrics",
        overwrite_conflicts=True,
        conflict_columns=["ticker", "as_of_date"],
    )
    return 0


if __name__ == "__main__":
    main()
