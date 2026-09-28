"""Short interest, short volume, and float analyses for loaded equity tickers."""

from typing import TYPE_CHECKING

import polars as pl

from ats.analysis_utils import _anomaly, _dated, _number

if TYPE_CHECKING:
    from ats.ticker import EquityTicker


def _trend(records, field):
    rows = []
    previous = None
    increases = 0
    for day, record in _dated(records, "settlement_date"):
        value = _number(record.get(field))
        change = value - previous if value is not None and previous is not None else None
        increases = increases + 1 if change is not None and change > 0 else 0
        rows.append({
            "settlement_date": day, field: value, "change": change,
            "change_pct": change / previous * 100 if change is not None and previous > 0 else None,
            "consecutive_increases": increases,
        })
        previous = value
    return pl.DataFrame(rows, schema={
        "settlement_date": pl.Date, field: pl.Float64, "change": pl.Float64,
        "change_pct": pl.Float64, "consecutive_increases": pl.Int64,
    })


def _short_interest_trend_records(records):
    """Report-to-report changes in shares short; percentages are on a 0–100 scale."""
    return _trend(records, "short_interest").rename({"change": "change_shares"})


def _days_to_cover_trend_records(records):
    """Changes in the provider's reported days-to-cover measure."""
    return _trend(records, "days_to_cover").rename({"change": "change_days"})


def _short_volume_anomaly_records(records, recent_window=5, baseline_window=60):
    """Recent mean short-sale percentage versus a preceding, disjoint baseline.

    Windows count returned trading observations. Ratios use the short dataset's
    own volume denominator. Score is difference / baseline sample standard
    deviation, not a significance test or a measure of new short positions.
    """
    values = []
    for day, record in _dated(records, "date"):
        short, total = _number(record.get("short_volume")), _number(record.get("total_volume"))
        ratio = short / total * 100 if short is not None and total is not None and 0 <= short <= total and total > 0 else None
        values.append((day, ratio))
    return _anomaly(values, recent_window, baseline_window).rename({
        "value": "short_volume_pct", "difference": "difference_pp",
    })


def _short_interest_pct_of_float_records(short_records, float_records, max_float_age_days=None):
    """Normalize every report by the latest available float by default.

    The denominator stays fixed across the series. This is normalization to
    latest float, not the historically prevailing short-float percentage.
    An explicit integer opts into matching earlier float within that age limit
    (zero requires an exact date). float_age_days is the signed difference
    between report and float dates, so latest-float normalization can be negative.
    """
    if max_float_age_days is not None and (not isinstance(max_float_age_days, int) or max_float_age_days < 0):
        raise ValueError("max_float_age_days must be a nonnegative integer")
    floats = _dated(float_records, "effective_date")
    rows = []
    for day, record in _dated(short_records, "settlement_date"):
        candidates = floats if max_float_age_days is None else [
            (d, r) for d, r in floats if 0 <= (day - d).days <= max_float_age_days
        ]
        float_day, float_record = candidates[-1] if candidates else (None, {})
        shares, free_float = _number(record.get("short_interest")), _number(float_record.get("free_float"))
        rows.append({
            "settlement_date": day, "short_interest": shares,
            "float_effective_date": float_day, "free_float": free_float,
            "float_age_days": (day - float_day).days if float_day else None,
            "short_interest_pct_of_float": shares / free_float * 100 if shares is not None and free_float is not None and free_float > 0 else None,
        })
    return pl.DataFrame(rows, schema={
        "settlement_date": pl.Date, "short_interest": pl.Float64,
        "float_effective_date": pl.Date, "free_float": pl.Float64,
        "float_age_days": pl.Int64, "short_interest_pct_of_float": pl.Float64,
    })


def short_interest_trend(ticker: "EquityTicker") -> pl.DataFrame:
    return _short_interest_trend_records(ticker.short_interest)


def days_to_cover_trend(ticker: "EquityTicker") -> pl.DataFrame:
    return _days_to_cover_trend_records(ticker.short_interest)


def short_volume_anomaly(
    ticker: "EquityTicker", recent_window: int = 5, baseline_window: int = 60
) -> pl.DataFrame:
    return _short_volume_anomaly_records(ticker.short_volume, recent_window, baseline_window)


def short_interest_as_a_percentage_of_float(
    ticker: "EquityTicker", max_float_age_days: int | None = None
) -> pl.DataFrame:
    return _short_interest_pct_of_float_records(
        ticker.short_interest, ticker.float_data, max_float_age_days
    )
