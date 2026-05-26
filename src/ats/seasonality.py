import yfinance as yf
import polars as pl
import numpy as np
import matplotlib.pyplot as plt
import math
from datetime import date, timedelta
from dataclasses import dataclass
from tabulate import tabulate
import plotille



MONTH_COLUMNS = [f"month_{i}" for i in range(1, 13)]

EXTRACT_MONTH = pl.col("Date").dt.month().alias("month")
EXTRACT_YEAR = pl.col("Date").dt.year().alias("year")
LOG_RETURN = (pl.col("AdjClose") / pl.col("AdjClose").shift(1)).log().alias("LogReturn")
NORMALIZE_BY_TRADING_DAYS = (pl.col("LogReturn") / pl.col("trading_days")).alias("LogReturn")
WINSORIZE = (
    pl.when(pl.col("LogReturn") > pl.col("LogReturn").quantile(0.95))
    .then(pl.col("LogReturn").quantile(0.95))
    .when(pl.col("LogReturn") < pl.col("LogReturn").quantile(0.05))
    .then(pl.col("LogReturn").quantile(0.05))
    .otherwise(pl.col("LogReturn"))
    .alias("LogReturn")
)
@dataclass
class SeasonalityResult:
    ticker: str
    n_observations: int
    coefficients: list[float]
    standard_errors: list[float]
    t_statistics: list[float]
    p_values: list[float]


@dataclass
class StabilityResult:
    ticker: str
    window_sizes: list[int]
    t_stats_by_month: dict[int, list[float]]


@dataclass
class RegressionResult:
    nobs: int
    params: np.ndarray
    bse: np.ndarray
    tvalues: np.ndarray
    pvalues: np.ndarray


def _two_sided_normal_p_values(t_values: np.ndarray) -> np.ndarray:
    return np.array(
        [
            math.erfc(abs(float(t)) / math.sqrt(2.0)) if np.isfinite(t) else np.nan
            for t in t_values
        ]
    )


def fit_ols_hc3_numpy(y, X) -> RegressionResult:
    y = np.asarray(y, dtype=float)
    X = np.asarray(X, dtype=float)
    beta = np.linalg.lstsq(X, y, rcond=None)[0]
    resid = y - X @ beta
    xtx_inv = np.linalg.pinv(X.T @ X)
    leverage = np.sum((X @ xtx_inv) * X, axis=1)
    adjusted_resid = resid / np.clip(1.0 - leverage, 1e-12, None)
    meat = X.T @ ((adjusted_resid**2)[:, None] * X)
    cov = xtx_inv @ meat @ xtx_inv
    se = np.sqrt(np.clip(np.diag(cov), 0.0, None))
    with np.errstate(divide="ignore", invalid="ignore"):
        t_stat = beta / se
    return RegressionResult(
        nobs=len(y),
        params=beta,
        bse=se,
        tvalues=t_stat,
        pvalues=_two_sided_normal_p_values(t_stat),
    )


def fit_quantile_regression_numpy(y, X, q=0.5, max_iter=1_000, tol=1e-8) -> RegressionResult:
    if not 0 < q < 1:
        raise ValueError("q must be between 0 and 1.")

    y = np.asarray(y, dtype=float)
    X = np.asarray(X, dtype=float)
    beta = np.linalg.lstsq(X, y, rcond=None)[0]
    eps = 1e-8

    for _ in range(max_iter):
        resid = y - X @ beta
        signed_resid = np.where(resid < 0, q * resid, (1.0 - q) * resid)
        weighted_resid = np.where(
            np.abs(signed_resid) < eps,
            np.sign(signed_resid + eps) * eps,
            signed_resid,
        )
        xstar = X / np.abs(weighted_resid)[:, None]
        next_beta = np.linalg.pinv(xstar.T @ X) @ xstar.T @ y
        if np.max(np.abs(next_beta - beta)) < tol:
            beta = next_beta
            break
        beta = next_beta

    resid = y - X @ beta
    resid_std = np.std(resid, ddof=X.shape[1])
    if not np.isfinite(resid_std) or resid_std <= 0:
        se = np.full(X.shape[1], np.nan)
    else:
        bandwidth = max(1.06 * resid_std * (len(y) ** -0.2), eps)
        kernel = np.exp(-0.5 * (resid / bandwidth) ** 2) / np.sqrt(2.0 * np.pi)
        density_at_zero = np.mean(kernel) / bandwidth
        xtx_inv = np.linalg.pinv(X.T @ X)
        cov = q * (1.0 - q) / (density_at_zero**2) * xtx_inv
        se = np.sqrt(np.clip(np.diag(cov), 0.0, None))

    with np.errstate(divide="ignore", invalid="ignore"):
        t_stat = beta / se
    return RegressionResult(
        nobs=len(y),
        params=beta,
        bse=se,
        tvalues=t_stat,
        pvalues=_two_sided_normal_p_values(t_stat),
    )


def fetch_prices(ticker: str, years: int) -> pl.DataFrame:
    start = (date.today() - timedelta(days=years * 365)).isoformat()
    raw = yf.download(ticker, start=start, progress=False)
    pandas_df = raw[("Close", ticker)].reset_index()
    pandas_df.columns = ["Date", "AdjClose"]
    return pl.from_pandas(pandas_df).with_columns(pl.col("Date").cast(pl.Date))


def aggregate_monthly_returns(daily_prices: pl.DataFrame) -> pl.DataFrame:
    return (
        daily_prices
        .with_columns(EXTRACT_MONTH, EXTRACT_YEAR, LOG_RETURN)
        .drop_nulls()
        .group_by("year", "month")
      .agg(
    pl.col("LogReturn").sum().alias("LogReturn"),
    pl.col("LogReturn").count().alias("trading_days"),
)
.with_columns(NORMALIZE_BY_TRADING_DAYS)
        .with_columns(WINSORIZE)
        .sort("year", "month")
        .to_dummies(columns=["month"], separator="_")
    )


def fit_seasonal_ols(monthly_returns: pl.DataFrame) -> RegressionResult:
    target = monthly_returns["LogReturn"].to_numpy()
    features = monthly_returns.select(MONTH_COLUMNS).to_numpy().astype(float)
    return fit_ols_hc3_numpy(target, features)


def extract_result(ticker: str, model: RegressionResult) -> SeasonalityResult:
    return SeasonalityResult(
        ticker=ticker,
        n_observations=int(model.nobs),
        coefficients=model.params.tolist(),
        standard_errors=model.bse.tolist(),
        t_statistics=model.tvalues.tolist(),
        p_values=model.pvalues.tolist(),
    )

def fit_seasonal_quantile(monthly_returns: pl.DataFrame) -> RegressionResult:
    target = monthly_returns["LogReturn"].to_numpy()
    features = monthly_returns.select(MONTH_COLUMNS).to_numpy().astype(float)
    return fit_quantile_regression_numpy(target, features, q=0.5)

def format_table(result: SeasonalityResult, significance: float = 0.05) -> str:
    rows = [
        [
            month,
            result.coefficients[i],
            result.standard_errors[i],
            result.t_statistics[i],
            result.p_values[i],
            "*" if result.p_values[i] < significance else "",
        ]
        for i, month in enumerate(range(1, 13))
    ]
    header = f"Ticker: {result.ticker} | N observations: {result.n_observations} | Significance level: {significance}"
    table = tabulate(rows, headers=["Month", "Coeff", "SE(HC3)", "t-stat", "p(two)", "Sig"],
                     floatfmt=(".0f", ".5f", ".5f", ".3f", ".4f", ""))
    return f"{header}\n{table}"


def seasonal_regression(ticker: str, years: int, significance: float = 0.05) -> SeasonalityResult:
    prices = fetch_prices(ticker, years)
    monthly = aggregate_monthly_returns(prices)
    model = fit_seasonal_quantile(monthly)
    result = extract_result(ticker, model)
    print(format_table(result, significance))
    return result


def slice_by_window(monthly_returns: pl.DataFrame, cutoff_year: int) -> pl.DataFrame:
    return monthly_returns.filter(pl.col("year") >= cutoff_year)


def stability_test(ticker: str, min_years: int = 5, max_years: int = 25) -> StabilityResult:
    prices = fetch_prices(ticker, max_years)
    monthly = aggregate_monthly_returns(prices)

    earliest_year = monthly.select(pl.col("year").min()).item()
    latest_year = monthly.select(pl.col("year").max()).item()
    available_years = latest_year - earliest_year + 1

    effective_max = min(max_years, available_years)
    window_sizes = list(range(min_years, effective_max + 1))
    t_stats_by_month = {m: [] for m in range(1, 13)}

    for years in window_sizes:
        cutoff_year = latest_year - years + 1
        window = slice_by_window(monthly, cutoff_year)
        model = fit_seasonal_ols(window)
        for i in range(12):
            t_stats_by_month[i + 1].append(model.tvalues[i])

    return StabilityResult(
        ticker=ticker,
        window_sizes=window_sizes,
        t_stats_by_month=t_stats_by_month,
    )


def consistent_months(stability: StabilityResult, significance_t: float = 1.96) -> list[int]:
    return [
        month
        for month, t_stats in stability.t_stats_by_month.items()
        if all(t > significance_t for t in t_stats)
    ]


def verdict(stability: StabilityResult, significance_t: float = 1.96) -> str:
    winners = consistent_months(stability, significance_t)
    month_names = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                   "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    if not winners:
        return f"{stability.ticker}: No month consistently significant across all windows."
    labels = [f"{month_names[m - 1]} (M{m})" for m in winners]
    return f"{stability.ticker}: Consistently significant: {', '.join(labels)}"

def plot_terminal(stability: StabilityResult, significance_t: float = 1.96):
    fig = plotille.Figure()
    fig.width = 80
    fig.height = 20
    fig.set_x_limits(min_=min(stability.window_sizes), max_=max(stability.window_sizes))
    fig.x_label = "Sample window (years)"
    fig.y_label = "t-statistic"

    colors = [
        "red", "green", "yellow", "blue", "magenta", "cyan",
        "white", "bright_red", "bright_green", "bright_yellow",
        "bright_blue", "bright_magenta",
    ]

    for i, month in enumerate(range(1, 13)):
        fig.plot(
            stability.window_sizes,
            stability.t_stats_by_month[month],
            label=f"M{month}",
            lc=colors[i],
        )

    print(fig.show(legend=True))

def plot(stability: StabilityResult, significance_t: float = 1.96):
    fig, ax = plt.subplots(figsize=(16, 9), dpi=300)

    for month, t_stats in stability.t_stats_by_month.items():
        ax.plot(stability.window_sizes, t_stats, "o-", ms=3, lw=1.2, label=f"M{month}")

    ax.axhline(significance_t, ls="--", color="grey", lw=0.8)
    ax.axhline(-significance_t, ls="--", color="grey", lw=0.8)
    ax.axhline(0, ls="-", color="black", lw=0.5)
    ax.set_xlabel("Sample window (years)")
    ax.set_ylabel("t-statistic")
    ax.set_title(f"{stability.ticker} — Monthly seasonality t-stat stability")
    ax.legend(loc="lower center", ncol=6, frameon=False, fontsize=8)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    fig.tight_layout()
    return fig

import sys


def main():
    ticker = sys.argv[1].upper() if len(sys.argv) > 1 else input("Ticker: ").strip().upper()
    save = "--save" in sys.argv

    stability = stability_test(ticker)
    print(verdict(stability))
    plot_terminal(stability)

    if save:
        fig = plot(stability)
        path = f"{ticker}.png"
        fig.savefig(path, bbox_inches="tight")
        print(f"Saved: {path}")


if __name__ == "__main__":
    main()
