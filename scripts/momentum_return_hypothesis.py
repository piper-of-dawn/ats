from __future__ import annotations

import argparse
import math
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from datetime import date, timedelta
from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import polars as pl
import requests


DEFAULT_INPUT = Path(
    "/tmp/us_largecap_midcap_metrics_with_momentum_percentiles.parquet"
)
DEFAULT_OUTPUT_PARQUET = Path("/tmp/momentum_return_hypothesis.parquet")
DEFAULT_OUTPUT_CHART = Path("/tmp/momentum_return_hypothesis.png")


@dataclass(frozen=True, slots=True)
class TickerReturn:
    ticker: str
    yahoo_ticker: str
    return_date: date | None
    previous_date: date | None
    close: float | None
    previous_close: float | None
    log_return: float | None
    error: str | None = None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Join t-minus momentum percentiles to same-day returns and plot "
            "whether higher momentum maps to higher return quantiles."
        )
    )
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output-parquet", type=Path, default=DEFAULT_OUTPUT_PARQUET)
    parser.add_argument("--output-chart", type=Path, default=DEFAULT_OUTPUT_CHART)
    parser.add_argument("--return-date", type=date.fromisoformat, default=date.today())
    parser.add_argument("--lag-days", type=int, default=3)
    parser.add_argument("--return-window-days", type=int, default=1)
    parser.add_argument("--workers", type=int, default=48)
    parser.add_argument("--period", default="7d")
    parser.add_argument("--returns-cache", type=Path)
    return parser.parse_args()


def yahoo_symbol(ticker: str) -> str:
    symbol = ticker.strip().upper()
    if "." in symbol and not symbol.endswith(
        (".L", ".DE", ".PA", ".AS", ".MI", ".MC", ".SW")
    ):
        return symbol.replace(".", "-")
    return symbol


def fetch_return(
    ticker: str,
    target_date: date,
    period: str,
    return_window_days: int,
    retries: int = 3,
) -> TickerReturn:
    symbol = yahoo_symbol(ticker)
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
    params = {
        "range": period,
        "interval": "1d",
        "includePrePost": "false",
        "events": "history",
    }
    headers = {"User-Agent": "Mozilla/5.0"}

    for attempt in range(retries + 1):
        try:
            response = requests.get(url, params=params, headers=headers, timeout=15)
            if response.status_code == 429:
                if attempt < retries:
                    time.sleep(2**attempt)
                    continue
                return TickerReturn(
                    ticker,
                    symbol,
                    None,
                    None,
                    None,
                    None,
                    None,
                    "HTTP 429 Too Many Requests",
                )
            if response.status_code != 200:
                return TickerReturn(
                    ticker,
                    symbol,
                    None,
                    None,
                    None,
                    None,
                    None,
                    f"HTTP {response.status_code}: {response.text[:120]}",
                )

            payload = response.json()
            result = (payload.get("chart", {}).get("result") or [None])[0]
            if not result:
                error = payload.get("chart", {}).get("error")
                return TickerReturn(
                    ticker,
                    symbol,
                    None,
                    None,
                    None,
                    None,
                    None,
                    f"empty chart: {error}",
                )

            timestamps = result.get("timestamp") or []
            quote = ((result.get("indicators") or {}).get("quote") or [{}])[0]
            closes = quote.get("close") or []
            rows = [
                {"date": date.fromtimestamp(timestamp), "close": close}
                for timestamp, close in zip(timestamps, closes, strict=False)
                if close is not None
            ]
            df = (
                pl.DataFrame(rows, schema={"date": pl.Date, "close": pl.Float64})
                .filter(pl.col("date") <= target_date)
                .sort("date")
            )
            start_target = target_date - timedelta(days=return_window_days)
            start_df = df.filter(pl.col("date") <= start_target)
            if df.is_empty() or start_df.is_empty():
                return TickerReturn(
                    ticker,
                    symbol,
                    None,
                    None,
                    None,
                    None,
                    None,
                    f"missing close for {target_date} or {start_target}",
                )

            latest = df.tail(1).row(0, named=True)
            previous = start_df.tail(1).row(0, named=True)
            if latest["date"] != target_date:
                return TickerReturn(
                    ticker,
                    symbol,
                    latest["date"],
                    previous["date"],
                    float(latest["close"]),
                    float(previous["close"]),
                    None,
                    f"missing exact target close for {target_date}",
                )
            close = float(latest["close"])
            previous_close = float(previous["close"])
            if previous_close <= 0:
                return TickerReturn(
                    ticker,
                    symbol,
                    latest["date"],
                    previous["date"],
                    close,
                    previous_close,
                    None,
                    "non-positive previous close",
                )

            return TickerReturn(
                ticker=ticker,
                yahoo_ticker=symbol,
                return_date=latest["date"],
                previous_date=previous["date"],
                close=close,
                previous_close=previous_close,
                log_return=math.log(close / previous_close),
            )
        except Exception as exc:
            if attempt < retries:
                time.sleep(2**attempt)
                continue
            return TickerReturn(
                ticker,
                symbol,
                None,
                None,
                None,
                None,
                None,
                f"{type(exc).__name__}: {exc}",
            )

    return TickerReturn(
        ticker, symbol, None, None, None, None, None, "unknown fetch failure"
    )


def add_log_returns(rows: list[TickerReturn]) -> pl.DataFrame:
    return pl.DataFrame(
        [asdict(row) for row in rows],
        schema={
            "ticker": pl.String,
            "yahoo_ticker": pl.String,
            "return_date": pl.Date,
            "previous_date": pl.Date,
            "close": pl.Float64,
            "previous_close": pl.Float64,
            "log_return": pl.Float64,
            "error": pl.String,
        },
    )


def empty_returns_frame() -> pl.DataFrame:
    return pl.DataFrame(
        schema={
            "ticker": pl.String,
            "yahoo_ticker": pl.String,
            "return_date": pl.Date,
            "previous_date": pl.Date,
            "close": pl.Float64,
            "previous_close": pl.Float64,
            "log_return": pl.Float64,
            "error": pl.String,
        }
    )


def fetch_returns_parallel(
    tickers: list[str],
    target_date: date,
    period: str,
    workers: int,
    return_window_days: int,
) -> pl.DataFrame:
    if not tickers:
        return empty_returns_frame()
    rows: list[TickerReturn] = []
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(
                fetch_return, ticker, target_date, period, return_window_days
            ): ticker
            for ticker in tickers
        }
        completed = 0
        for future in as_completed(futures):
            rows.append(future.result())
            completed += 1
            if completed % 100 == 0:
                print(f"fetched={completed}/{len(tickers)}", flush=True)
    return add_log_returns(rows)


def load_or_fetch_returns(
    tickers: list[str],
    target_date: date,
    period: str,
    workers: int,
    cache_path: Path,
    return_window_days: int,
) -> pl.DataFrame:
    cached = (
        pl.read_parquet(cache_path) if cache_path.exists() else empty_returns_frame()
    )
    good_cached = cached.filter(
        (pl.col("log_return").is_not_null()) & (pl.col("return_date") == target_date)
    )
    cached_tickers = set(good_cached["ticker"].to_list())
    missing_tickers = [ticker for ticker in tickers if ticker not in cached_tickers]
    print(f"cached_returns={len(cached_tickers)}", flush=True)
    print(f"returns_to_fetch={len(missing_tickers)}", flush=True)
    fetched = fetch_returns_parallel(
        missing_tickers, target_date, period, workers, return_window_days
    )
    returns = pl.concat([good_cached, fetched], how="diagonal_relaxed")
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    returns.write_parquet(cache_path)
    return returns


def build_analysis(
    momentum: pl.DataFrame,
    returns: pl.DataFrame,
    return_date: date,
    lag_days: int,
) -> tuple[pl.DataFrame, date]:
    requested_momentum_date = return_date - timedelta(days=lag_days)
    available_dates = (
        momentum.select("as_of_date")
        .unique()
        .sort("as_of_date")["as_of_date"]
        .to_list()
    )
    if requested_momentum_date not in available_dates:
        candidates = [
            value for value in available_dates if value <= requested_momentum_date
        ]
        if not candidates:
            raise ValueError(
                f"No momentum date on or before {requested_momentum_date}."
            )
        momentum_date = candidates[-1]
    else:
        momentum_date = requested_momentum_date

    lagged_momentum = (
        momentum.filter(pl.col("as_of_date") == momentum_date)
        .select(
            "ticker",
            "source_table",
            pl.col("as_of_date").alias("momentum_date"),
            "ltm",
            "stm",
            "ltm_percentile",
            "stm_percentile",
        )
        .with_columns(
            ((pl.col("ltm_percentile") + pl.col("stm_percentile")) / 2.0).alias(
                "momentum_percentile_lagged"
            )
        )
    )

    return lagged_momentum.join(returns, on="ticker", how="left").with_columns(
        pl.col("log_return")
        .rank()
        .truediv(pl.col("log_return").count())
        .alias("return_percentile_t")
    ), momentum_date


def plot_analysis(
    analysis: pl.DataFrame,
    output_chart: Path,
    return_date: date,
    momentum_date: date,
    lag_days: int,
    return_window_days: int,
) -> None:
    clean = analysis.drop_nulls(["return_percentile_t"])
    if clean.is_empty():
        raise ValueError("No complete rows available for plotting.")

    def decile_frame(momentum_col: str) -> tuple[pl.DataFrame, float]:
        metric_clean = clean.drop_nulls([momentum_col])
        metric_clean = metric_clean.with_columns(
            ((pl.col(momentum_col) * 10).ceil().clip(1, 10).cast(pl.Int64)).alias(
                "momentum_decile"
            )
        )
        deciles = (
            metric_clean.group_by("momentum_decile")
            .agg(
                pl.len().alias("n"),
                pl.col("return_percentile_t").mean().alias("mean_return_percentile"),
                pl.col("log_return").mean().alias("mean_log_return"),
            )
            .sort("momentum_decile")
        )
        corr = metric_clean.select(
            pl.corr(momentum_col, "return_percentile_t").alias("pearson_corr")
        ).item()
        return deciles, corr

    fig, axes = plt.subplots(2, 1, figsize=(11, 9), constrained_layout=True)
    for axis, momentum_col, title in (
        (axes[0], "ltm_percentile", "LTM"),
        (axes[1], "stm_percentile", "STM"),
    ):
        deciles, corr = decile_frame(momentum_col)
        axis.plot(
            deciles["momentum_decile"].to_list(),
            deciles["mean_return_percentile"].to_list(),
            marker="o",
            linewidth=2,
        )
        axis.axhline(0.5, color="#777777", linewidth=1, linestyle="--")
        axis.set_title(
            f"{title}: {return_window_days}d return percentile by t-{lag_days} momentum decile "
            f"| corr={corr:.3f}"
        )
        axis.set_xlabel(f"{title} momentum decile at t-{lag_days}")
        axis.set_ylabel("Mean return percentile at t")
        axis.set_xticks(range(1, 11))
        axis.grid(True, alpha=0.25)

    fig.suptitle(f"Returns: {return_date} | momentum: {momentum_date}", fontsize=14)

    output_chart.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_chart, dpi=160)
    plt.close(fig)


def main() -> int:
    args = parse_args()
    if args.returns_cache is None:
        args.returns_cache = Path(
            f"/tmp/yahoo_returns_{args.return_date.isoformat()}_{args.return_window_days}d.parquet"
        )
    momentum = pl.read_parquet(args.input)
    target_momentum_date = args.return_date - timedelta(days=args.lag_days)
    available_dates = (
        momentum.select("as_of_date")
        .unique()
        .sort("as_of_date")["as_of_date"]
        .to_list()
    )
    momentum_date = (
        target_momentum_date
        if target_momentum_date in available_dates
        else max(value for value in available_dates if value <= target_momentum_date)
    )
    tickers = (
        momentum.filter(pl.col("as_of_date") == momentum_date)
        .select("ticker")
        .unique()
        .sort("ticker")["ticker"]
        .to_list()
    )
    print(f"momentum_date={momentum_date}", flush=True)
    print(f"tickers={len(tickers)}", flush=True)
    returns = load_or_fetch_returns(
        tickers,
        args.return_date,
        args.period,
        args.workers,
        args.returns_cache,
        args.return_window_days,
    )
    print(
        "returns="
        f"{returns.filter(pl.col('log_return').is_not_null()).height}/"
        f"{returns.height}",
        flush=True,
    )
    analysis, momentum_date = build_analysis(
        momentum, returns, args.return_date, args.lag_days
    )
    args.output_parquet.parent.mkdir(parents=True, exist_ok=True)
    analysis.write_parquet(args.output_parquet)
    plot_analysis(
        analysis,
        args.output_chart,
        args.return_date,
        momentum_date,
        args.lag_days,
        args.return_window_days,
    )
    print(f"saved_parquet={args.output_parquet}", flush=True)
    print(f"saved_chart={args.output_chart}", flush=True)
    print("summary:")
    print(
        analysis.drop_nulls(["log_return", "return_percentile_t"]).select(
            pl.len().alias("complete_rows"),
            pl.col("log_return").mean().alias("mean_log_return"),
            pl.corr("momentum_percentile_lagged", "return_percentile_t").alias(
                "pearson_corr"
            ),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
