"""Internal helpers shared by dated ticker analyses."""

from datetime import date, datetime, timezone
from math import isfinite
from statistics import mean, stdev

import polars as pl


def _date(value):
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).date() if value.tzinfo else value.date()
    if isinstance(value, date):
        return value
    return datetime.fromisoformat(value.replace("Z", "+00:00")).date()


def _number(value):
    try:
        number = float(value)
        return number if isfinite(number) and number >= 0 else None
    except (TypeError, ValueError):
        return None


def _dated(records, date_field):
    if records is None:
        raise ValueError("Fetch the source records before calculating this analysis")
    dated = {}
    for record in records:
        day = _date(record[date_field])
        if day in dated and dated[day] != record:
            raise ValueError(f"Conflicting records for {day}; supply one ticker and one record per date")
        dated[day] = record
    return sorted(dated.items())


def _anomaly(dated_values, recent_window, baseline_window):
    if not isinstance(recent_window, int) or recent_window < 1:
        raise ValueError("recent_window must be a positive integer")
    if not isinstance(baseline_window, int) or baseline_window < 2:
        raise ValueError("baseline_window must be an integer of at least two")
    values = [value for _, value in dated_values]
    rows = []
    for i, (day, value) in enumerate(dated_values):
        recent = values[max(0, i + 1 - recent_window):i + 1]
        stop = i + 1 - recent_window
        baseline = values[max(0, stop - baseline_window):max(0, stop)]
        recent_mean = mean(recent) if len(recent) == recent_window and None not in recent else None
        baseline_mean = deviation = None
        if len(baseline) == baseline_window and None not in baseline:
            baseline_mean, deviation = mean(baseline), stdev(baseline)
        change = recent_mean - baseline_mean if recent_mean is not None and baseline_mean is not None else None
        rows.append({
            "date": day, "value": value, "recent_mean": recent_mean,
            "baseline_mean": baseline_mean, "baseline_std": deviation,
            "difference": change,
            "anomaly_score": change / deviation if change is not None and deviation > 0 else None,
        })
    return pl.DataFrame(rows, schema={"date": pl.Date, **{key: pl.Float64 for key in (
        "value", "recent_mean", "baseline_mean", "baseline_std", "difference", "anomaly_score"
    )}})
