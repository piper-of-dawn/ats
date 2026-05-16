import random
import time
from datetime import date
from math import sqrt

import numpy as np
import polars as pl
from yfinance import Ticker

from ats.dataIO.supabase_integration import batch_insert_polars_df, fetch_table
from ats.dataIO.utils import with_parallel_runner

LABELS = ["strongBuy", "buy", "hold", "sell", "strongSell"]
R = np.array([2, 1, 0, -1, -2])


R = {"strongBuy": 2, "buy": 1, "hold": 0, "sell": -1, "strongSell": -2}


def weights(data, lam=0.8):
    raw = [lam**i for i in range(len(data))]
    return list(np.array(raw) / np.sum(raw))


def mu_t(period):
    N = np.sum([period[k] for k in R])
    return np.sum([R[k] * period[k] for k in R]) / N


def C_t(period):
    N = np.sum([period[k] for k in R])
    m = mu_t(period)
    var = np.sum([((R[k] - m) ** 2) * period[k] for k in R]) / N
    return 1 - sqrt(var) / 2


def direction(data, lam=0.8):
    w = weights(data, lam)
    mus = [mu_t(p) for p in data]
    return np.sum(np.array(w) * np.array(mus))


def agreement(data, lam=0.8):
    w = weights(data, lam)
    Cs = [C_t(p) for p in data]
    return np.sum(np.array(w) * np.array(Cs))


def stability(data, lam=0.8):
    w = weights(data, lam)
    mus = [mu_t(p) for p in data]
    mu_star = np.sum(np.array(w) * np.array(mus))
    var = np.sum(np.array(w) * (np.array(mus) - mu_star) ** 2)
    return 1 - 0.5 * sqrt(var)


def sample_confidence(data, lam=0.8, k=10):
    w = weights(data, lam)
    Ns = [np.sum([p[k] for k in R]) for p in data]
    N_bar = np.sum(np.array(w) * np.array(Ns))
    return N_bar / (N_bar + k)


@with_parallel_runner(
    item_name="ticker",
    result_name="cbs",
    desc="Computing CBS",
    unit="ticker",
)
def CBS(ticker, lam=0.8, k=10) -> float:
    time.sleep(random.uniform(0.2, 0.75))
    data = pl.DataFrame(Ticker(ticker).get_recommendations_summary()).to_dicts()
    mu_star = direction(data, lam)
    C_star = agreement(data, lam)
    T = stability(data, lam)
    S = sample_confidence(data, lam, k)
    return (mu_star / 2) * C_star * T * S


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Get the latest analyst ratings")
    parser.add_argument(
        "table", nargs="?", help="Table name (positional or via --table)"
    )
    parser.add_argument(
        "--table", dest="table_named", help="Table name (named argument)"
    )
    args = parser.parse_args()
    table_name = args.table_named or args.table
    df = fetch_table(table_name).drop_nulls()
    tickers = df["yahoo_finance_ticker"].to_list()
    create_consensus_quantile = (
        (pl.col("cbs").rank() / pl.col("cbs").count().cast(pl.Float64)).round(2)
    ).alias("analyst_rating")
    df = CBS.parallel(tickers)[0].with_columns(
        create_consensus_quantile,
        pl.lit(date.today()).alias("as_of_date"),
    )
    batch_insert_polars_df(
        df,
        ["ticker", "as_of_date", "analyst_rating"],
        f"{table_name}_metrics",
        overwrite_conflicts=True,
        conflict_columns=["ticker", "as_of_date"],
    )
    return 0


if __name__ == "__main__":
    main()
