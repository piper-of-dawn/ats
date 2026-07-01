from datetime import date
from dataclasses import dataclass
from typing import ClassVar

import numpy as np
import polars as pl

from ats.ticker import EquityTicker, log_returns


YAHOO_COUNTRY_SUFFIXES = {
    "GB": ".L",
    "DE": ".DE",
    "FR": ".PA",
    "NL": ".AS",
    "ES": ".MC",
    "IT": ".MI",
    "CH": ".SW",
    "SE": ".ST",
    "DK": ".CO",
    "NO": ".OL",
    "FI": ".HE",
    "BE": ".BR",
    "AT": ".VI",
    "PT": ".LS",
    "IE": ".IR",
}


def positions_with_weights(positions_df: pl.DataFrame) -> pl.DataFrame:
    return positions_df.with_columns(pl.col("ticker").alias("tickers"), (pl.col("value").cast(pl.Float64) / pl.col("value").cast(pl.Float64).sum()).alias("weight"))


@dataclass(slots=True)
class PositionReturn:
    ticker: str
    weight: float
    position_return: float

    portfolio_variance: ClassVar[float | None] = None

    @property
    def component_realized_variance(self) -> float:
        return self.position_return**2

    @property
    def component_realized_volatility(self) -> float:
        return abs(self.position_return)


def yahoo_ticker(ticker: str, country: str | None = None) -> str:
    """Return a Yahoo Finance ticker, adding exchange suffixes for non-US listings."""
    symbol = ticker.strip().upper()
    if "." in symbol:
        return symbol
    if any(character.isdigit() for character in symbol):
        return f"{symbol}.L"
    suffix = YAHOO_COUNTRY_SUFFIXES.get((country or "US").strip().upper(), "")
    return f"{symbol}{suffix}"


def return_on_position(
    ticker: str,
    selected_date: date,
    country: str | None = None,
) -> float | None:
    """Return the same-day log return for a held position's ticker."""
    equity_ticker = EquityTicker(yahoo_ticker(ticker, country))
    if equity_ticker.price_data.is_empty():
        return None
    returns_df = log_returns(equity_ticker.price_data)
    if "date" not in returns_df.columns or "log_return" not in returns_df.columns:
        return None
    row = returns_df.filter(pl.col("date") == selected_date).select("log_return")
    if row.is_empty():
        return None
    return row.item()


def build_position_returns(
    positions: pl.DataFrame,
    selected_date: date,
) -> list[PositionReturn]:
    """Create per-position return objects from weighted positions for one date."""
    weighted_positions = positions.with_columns(
        pl.col("value").cast(pl.Float64).alias("position_value"),
    ).with_columns(
        (pl.col("position_value") / pl.col("position_value").sum()).alias("weight"),
    )

    position_returns = []
    for row in weighted_positions.iter_rows(named=True):
        try:
            position_return = return_on_position(
                row["ticker"],
                selected_date,
                row.get("country"),
            )
        except Exception:
            position_return = None
        if position_return is None:
            continue
        position_returns.append(
            PositionReturn(
                ticker=row["ticker"],
                weight=float(row["weight"]),
                position_return=float(position_return),
            )
        )
    portfolio_return = sum(
        position.weight * position.position_return
        for position in position_returns
    )
    PositionReturn.portfolio_variance = portfolio_return**2
    return position_returns


def snapshot_realized_correlation(
    position_returns: list[PositionReturn],
) -> float | None:
    """Compute CBOE-style snapshot realized correlation from same-day returns."""
    if len(position_returns) < 2 or PositionReturn.portfolio_variance is None:
        return None

    component_variance = sum(
        position.weight**2 * position.component_realized_variance
        for position in position_returns
    )
    denominator = sum(
        left.weight
        * right.weight
        * left.component_realized_volatility
        * right.component_realized_volatility
        for left in position_returns
        for right in position_returns
        if left.ticker != right.ticker
    )
    if denominator == 0:
        return None

    return (PositionReturn.portfolio_variance - component_variance) / denominator


def portfolio_gini_coefficient(positions: pl.DataFrame) -> float | None:
    """Return the Gini coefficient of portfolio concentration by market value."""
    values = (
        positions.select(pl.col("value").cast(pl.Float64).alias("value"))
        .drop_nulls()
        .filter(pl.col("value") > 0)["value"]
        .to_numpy()
    )
    if values.size == 0:
        return None

    sorted_values = np.sort(values)
    total_value = sorted_values.sum()
    if total_value == 0:
        return None

    n = sorted_values.size
    ranks = np.arange(1, n + 1)
    return float((2 * np.sum(ranks * sorted_values)) / (n * total_value) - (n + 1) / n)
