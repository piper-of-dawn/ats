"""Price preparation and volatility-adjusted momentum calculations."""

from dataclasses import dataclass, field
from math import isfinite
from typing import TYPE_CHECKING, Any, Callable, Mapping

import numpy as np
import polars as pl

from ats.helpers import compute_ema_signal, ema_volatility

if TYPE_CHECKING:
    from ats.ticker import EquityTicker


@dataclass(frozen=True)
class MomentumConfig:
    short_half_life: float = 20
    long_half_life: float = 112
    winsorize: bool = True
    use_idiosyncratic_returns: bool = True
    lookback_window: int | None = None
    volatility_model: Callable[..., np.ndarray] = ema_volatility
    volatility_model_args: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PreparedReturns:
    data: pl.DataFrame
    beta: float | None


@dataclass(frozen=True)
class MomentumMetrics:
    beta: float | None
    stm: float
    ltm: float
    prepared_data: pl.DataFrame
    stm_series: np.ndarray
    ltm_series: np.ndarray


def log_returns(data: pl.DataFrame) -> pl.DataFrame:
    return data.sort("date").with_columns(
        (pl.col("close") / pl.col("close").shift(1)).log().alias("log_return")
    )


def winsorize_log_returns(
    data: pl.DataFrame, *, return_col: str = "log_return", threshold: float = 5.0
) -> pl.DataFrame:
    if return_col not in data.columns:
        return data
    scale = data.select(pl.col(return_col).abs().median()).item()
    if not scale:
        return data
    cap = float(threshold) * float(scale)
    values = pl.col(return_col)
    return data.with_columns(
        pl.when(values.abs() > cap).then(values.sign() * cap).otherwise(values).alias(return_col)
    )


def _source_returns(ticker: "EquityTicker", config: MomentumConfig) -> pl.DataFrame:
    prices = ticker.price_data
    if prices is None or prices.is_empty():
        raise ValueError(f"Load price data for '{ticker.ticker}' before calculating returns")
    if not {"date", "close"}.issubset(prices.columns):
        raise ValueError(f"Price data for '{ticker.ticker}' must contain date and close")
    if config.lookback_window is not None:
        if (not isinstance(config.lookback_window, int)
                or isinstance(config.lookback_window, bool)
                or config.lookback_window <= 0):
            raise ValueError("lookback_window must be a positive integer")
    columns = [name for name in ("date", "close", "ticker") if name in prices.columns]
    raw = prices.select(columns).sort("date")
    if config.lookback_window is not None:
        raw = raw.tail(config.lookback_window + 1)
    returns = log_returns(raw)
    if config.lookback_window is not None:
        returns = returns.tail(config.lookback_window)
    if config.winsorize:
        returns = winsorize_log_returns(returns)
    return returns


def prepare_returns(
    ticker: "EquityTicker", *, config: MomentumConfig | None = None
) -> PreparedReturns:
    """Build fresh returns from loaded prices without changing either ticker."""
    config = config or MomentumConfig()
    stock = _source_returns(ticker, config)
    if config.use_idiosyncratic_returns:
        if ticker.mkt_index is None:
            raise ValueError("Load a market index for idiosyncratic momentum")
        market = _source_returns(ticker.mkt_index, config)
        if market.is_empty():
            raise ValueError("Market index has no usable price history")
        aligned = stock.join(
            market.select("date", pl.col("log_return").alias("mkt_log_return")),
            on="date", how="inner",
        ).drop_nulls(subset=["log_return", "mkt_log_return"])
        if aligned.height < 2:
            raise ValueError("At least two aligned returns are required to calculate beta")
        covariance = aligned.select(pl.cov("log_return", "mkt_log_return")).item()
        variance = aligned.select(pl.var("mkt_log_return")).item()
        if variance is None or not isfinite(variance) or variance <= 0:
            raise ValueError("Market return variance must be positive and finite")
        beta = covariance / variance
        if not isfinite(beta):
            raise ValueError("Beta must be finite")
        data = aligned.with_columns(
            (pl.col("log_return") - pl.col("mkt_log_return") * beta)
            .alias("idiosyncratic_returns")
        )
    else:
        beta = None
        data = stock.drop_nulls(subset=["log_return"]).with_columns(
            pl.col("log_return").alias("idiosyncratic_returns")
        )
    if data.height < 2:
        raise ValueError("At least two returns are required to calculate momentum")
    return PreparedReturns(data=data, beta=beta)


def _weighted_tail(values: np.ndarray, count: int = 5) -> float:
    tail_size = min(count, len(values))
    if tail_size == 0:
        raise ValueError("Momentum signal has no observations")
    weights = np.arange(1, tail_size + 1)
    result = float(np.dot(values[-tail_size:], weights) / weights.sum())
    if not isfinite(result):
        raise ValueError("Momentum signal must be finite")
    return result


def _signal(data: pl.DataFrame, half_life: float, config: MomentumConfig) -> np.ndarray:
    if not isfinite(half_life) or half_life <= 0:
        raise ValueError("Momentum half life must be positive and finite")
    return compute_ema_signal(
        price_data=data,
        volatility_model=config.volatility_model,
        volatility_model_args=config.volatility_model_args,
        eta=float(np.log(2) / half_life),
    )


def calculate_momentum(
    ticker: "EquityTicker", *, config: MomentumConfig | None = None
) -> MomentumMetrics:
    """Calculate both signals from the same price window and market beta."""
    config = config or MomentumConfig()
    prepared = prepare_returns(ticker, config=config)
    ltm_series = _signal(prepared.data, config.long_half_life, config)
    stm_series = _signal(prepared.data, config.short_half_life, config)
    return MomentumMetrics(
        beta=prepared.beta,
        stm=_weighted_tail(stm_series),
        ltm=_weighted_tail(ltm_series),
        prepared_data=prepared.data,
        stm_series=stm_series,
        ltm_series=ltm_series,
    )
