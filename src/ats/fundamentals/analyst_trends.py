from datetime import timedelta

import polars as pl
from yfinance import Ticker as YfTicker


RATING_SCORE = {
    "Strong Buy": 1.0,
    "Buy": 0.75,
    "Overweight": 0.5,
    "Outperform": 0.5,
    "Hold": 0.0,
    "Neutral": 0.0,
    "Equal-Weight": 0.0,
    "Market Perform": 0.0,
    "Perform": 0.0,
    "Performer": 0.0,
    "Underweight": -0.5,
    "Underperform": -0.5,
    "Sell": -0.75,
    "Strong Sell": -1.0,
    "": None,
}
PRICE_TARGET_SCALE = 0.25
RECENT_LOOKBACK_DAYS = 180
STABLE_THRESHOLD = 0.02
MASSIVE_THRESHOLD = 0.25


def get_ticker_signal_for_analyst_grades(ticker: str) -> str | None:
    analyst_grades = YfTicker(ticker).get_upgrades_downgrades()
    if analyst_grades is None or analyst_grades.empty:
        return None
    return analyst_grade_trend_signal(
        pl.from_pandas(
            analyst_grades.rename_axis("GradeDate").reset_index()
        ).with_columns(pl.col("GradeDate").dt.date())
    )


def analyst_grade_trend_signal(analyst_grades: pl.DataFrame) -> str | None:
    if analyst_grades.is_empty():
        return None

    analyst_grades = _normalize_columns(analyst_grades)
    required_columns = {
        "GradeDate",
        "Firm",
        "FromGrade",
        "ToGrade",
        "priorPriceTarget",
        "currentPriceTarget",
    }
    if required_columns - set(analyst_grades.columns):
        return None

    scored_actions = _score_actions(analyst_grades)
    if scored_actions.is_empty():
        return None

    recent_revision_score = _recent_revision_consensus(scored_actions)
    if recent_revision_score is None:
        return "stable"
    return _classify_trend(recent_revision_score)


def _classify_trend(signal: float) -> str:
    if signal >= MASSIVE_THRESHOLD:
        return "massively improving"
    if signal > STABLE_THRESHOLD:
        return "improving"
    if signal <= -MASSIVE_THRESHOLD:
        return "massively deteriorating"
    if signal < -STABLE_THRESHOLD:
        return "deteriorating"
    return "stable"


def _normalize_columns(analyst_grades: pl.DataFrame) -> pl.DataFrame:
    column_map = {
        "firm": "Firm",
        "fromGrade": "FromGrade",
        "toGrade": "ToGrade",
    }
    return analyst_grades.rename(
        {
            old_name: new_name
            for old_name, new_name in column_map.items()
            if old_name in analyst_grades.columns
        }
    )


def _score_actions(analyst_grades: pl.DataFrame) -> pl.DataFrame:
    price_targets_exist = (pl.col("priorPriceTarget") > 0) & (
        pl.col("currentPriceTarget") > 0
    )
    encoded_ratings = [
        pl.col(column)
        .replace_strict(RATING_SCORE, default=None, return_dtype=pl.Float64)
        .alias(column)
        for column in ["FromGrade", "ToGrade"]
    ]
    price_target_delta = (
        (
            (pl.col("currentPriceTarget") / pl.col("priorPriceTarget")).log()
            / PRICE_TARGET_SCALE
        ).tanh()
    ).alias("priceTargetDelta")

    return (
        analyst_grades.filter(price_targets_exist)
        .with_columns(*encoded_ratings, price_target_delta)
        .drop_nulls(["GradeDate", "Firm", "FromGrade", "ToGrade", "priceTargetDelta"])
        .with_columns(
            (pl.col("ToGrade") - pl.col("FromGrade")).alias("gradeDelta"),
        )
        .with_columns((pl.col("gradeDelta") / 2).clip(-1, 1).alias("gradeDeltaScore"))
        .with_columns(
            (
                (pl.col("gradeDeltaScore") + pl.col("priceTargetDelta")) / 2
            ).alias("revisionScore")
        )
        .select(["GradeDate", "Firm", "revisionScore"])
    )


def _recent_revision_consensus(scored_actions: pl.DataFrame) -> float | None:
    latest_date = scored_actions.select(pl.col("GradeDate").max()).item()
    recent_cutoff = latest_date - timedelta(days=RECENT_LOOKBACK_DAYS)
    recent_actions = scored_actions.filter(pl.col("GradeDate") >= recent_cutoff)
    if recent_actions.is_empty():
        recent_actions = scored_actions

    firm_revision_scores = recent_actions.group_by("Firm").agg(
        pl.col("revisionScore").median().alias("firmRevisionScore")
    )
    if firm_revision_scores.is_empty():
        return None
    return firm_revision_scores.select(pl.col("firmRevisionScore").median()).item()
