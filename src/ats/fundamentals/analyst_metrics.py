"""Analyst factor results from source data loaded on an equity ticker."""

from dataclasses import dataclass
from typing import TYPE_CHECKING

import polars as pl

from ats.fundamentals.analyst_price_targets import median_centered_score
from ats.fundamentals.analyst_ratings import (
    agreement, direction, sample_confidence, stability,
)

if TYPE_CHECKING:
    from ats.ticker import EquityTicker


@dataclass(frozen=True)
class AnalystMetrics:
    combined_rating: float
    price_target_deviation: float | None


def calculate_analyst_metrics(
    ticker: "EquityTicker", *, lam: float = 0.8, k: int = 10
) -> AnalystMetrics:
    """Score loaded Yahoo recommendations and targets without fetching data."""
    if ticker.loaded_recommendations is None:
        raise ValueError("Load analyst data before calculating analyst metrics")
    data = pl.DataFrame(ticker.loaded_recommendations).to_dicts()
    if not data:
        raise ValueError("Analyst recommendation summary is empty")
    combined_rating = float(
        (direction(data, lam) / 2)
        * agreement(data, lam)
        * stability(data, lam)
        * sample_confidence(data, lam, k)
    )
    targets = ticker.loaded_price_targets
    if not isinstance(targets, dict) or not targets:
        deviation = None
    else:
        try:
            deviation = round(median_centered_score(targets), 2)
        except (KeyError, TypeError, ValueError):
            deviation = None
    return AnalystMetrics(combined_rating, deviation)
