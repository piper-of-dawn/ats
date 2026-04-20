from yfinance import Ticker
import numpy as np
from math import sqrt
from concurrent.futures import ThreadPoolExecutor, as_completed
from tqdm.auto import tqdm
import polars as pl
import time
import random
from ats.dataIO.supabase_integration import fetch_table, batch_insert_polars_df
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


def CBS(ticker, lam=0.8, k=10) -> float:
    time.sleep(random.uniform(0.2, 0.75))
    data = pl.DataFrame(Ticker(ticker).get_recommendations_summary()).to_dicts()
    mu_star = direction(data, lam)
    C_star = agreement(data, lam)
    T = stability(data, lam)
    S = sample_confidence(data, lam, k)
    return (mu_star / 2) * C_star * T * S


def run_cbs_parallel(tickers, max_workers=20):
    results = []
    errors = []

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_ticker = {
            executor.submit(CBS, ticker): ticker
            for ticker in tickers
        }

        for future in tqdm(
            as_completed(future_to_ticker),
            total=len(future_to_ticker),
            desc="Computing CBS",
            unit="ticker",
        ):
            ticker = future_to_ticker[future]
            try:
                score = future.result()
                results.append({"ticker": ticker, "cbs": score})
            except Exception as e:
                errors.append({"ticker": ticker, "error": str(e)})

    return pl.DataFrame(results), pl.DataFrame(errors)



def main ():
    import argparse
    parser = argparse.ArgumentParser(
        description="Get the latest analyst ratings"
    )
    parser.add_argument("table", nargs="?", help="Table name (positional or via --table)")
    parser.add_argument("--table", dest="table_named", help="Table name (named argument)")
    args = parser.parse_args()
    table_name = args.table_named or args.table
    df = fetch_table(table_name).drop_nulls()
    tickers = df['yahoo_finance_ticker'].to_list()
    create_consensus_quantile = ((pl.col("cbs").rank() / pl.col("cbs").count().cast(pl.Float64)).round(2)).alias("rating")
    df = run_cbs_parallel(tickers)[0].with_columns(create_consensus_quantile)
    batch_insert_polars_df(df, ["ticker", "rating"], f"{table_name}_ratings", overwrite_conflicts=True, conflict_columns=["ticker"])
    return 0


if __name__ == "__main__":
    main()