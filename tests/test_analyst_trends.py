import math

import polars as pl

from ats.fundamentals.analyst_trends import (
    analyst_grade_trend_score,
    analyst_grade_trend_signal,
)


def test_analyst_grade_trend_treats_market_perform_labels_as_neutral():
    analyst_grades = pl.DataFrame(
        {
            "GradeDate": ["2025-01-05", "2025-02-05", "2025-03-05"],
            "Firm": ["A", "A", "A"],
            "FromGrade": ["Market Perform", "Performer", "Hold"],
            "ToGrade": ["Performer", "Market Perform", "Buy"],
            "priorPriceTarget": [100.0, 100.0, 100.0],
            "currentPriceTarget": [100.0, 105.0, 115.0],
        }
    ).with_columns(pl.col("GradeDate").str.to_date())

    assert analyst_grade_trend_signal(analyst_grades) in {
        "improving",
        "massively improving",
    }


def test_analyst_grade_trend_drops_unmapped_labels_without_failing():
    analyst_grades = pl.DataFrame(
        {
            "GradeDate": ["2025-01-05", "2025-02-05"],
            "Firm": ["A", "A"],
            "FromGrade": ["Alien Grade", "Hold"],
            "ToGrade": ["Hold", "Buy"],
            "priorPriceTarget": [100.0, 100.0],
            "currentPriceTarget": [100.0, 115.0],
        }
    ).with_columns(pl.col("GradeDate").str.to_date())

    assert analyst_grade_trend_signal(analyst_grades) in {
        "improving",
        "massively improving",
    }


def test_analyst_grade_trend_detects_moderate_upgrade_trend():
    analyst_grades = pl.DataFrame(
        {
            "GradeDate": ["2025-01-05", "2025-02-05", "2025-03-05"],
            "Firm": ["A", "A", "A"],
            "FromGrade": ["Hold", "Hold", "Hold"],
            "ToGrade": ["Hold", "Hold", "Buy"],
            "priorPriceTarget": [100.0, 100.0, 100.0],
            "currentPriceTarget": [100.0, 105.0, 110.0],
        }
    ).with_columns(pl.col("GradeDate").str.to_date())

    assert analyst_grade_trend_signal(analyst_grades) in {
        "improving",
        "massively improving",
    }


def test_analyst_grade_trend_uses_latest_action_per_firm():
    analyst_grades = pl.DataFrame(
        {
            "GradeDate": ["2025-01-05", "2025-03-05"],
            "Firm": ["A", "A"],
            "FromGrade": ["Buy", "Hold"],
            "ToGrade": ["Hold", "Buy"],
            "priorPriceTarget": [100.0, 100.0],
            "currentPriceTarget": [80.0, 120.0],
        }
    ).with_columns(pl.col("GradeDate").str.to_date())

    assert analyst_grade_trend_signal(analyst_grades) in {
        "improving",
        "massively improving",
    }


def test_analyst_grade_trend_score_is_continuous():
    analyst_grades = pl.DataFrame(
        {
            "GradeDate": ["2025-03-05"],
            "Firm": ["A"],
            "FromGrade": ["Hold"],
            "ToGrade": ["Buy"],
            "priorPriceTarget": [100.0],
            "currentPriceTarget": [112.0],
        }
    ).with_columns(pl.col("GradeDate").str.to_date())

    score = analyst_grade_trend_score(analyst_grades)

    assert score is not None
    assert not math.isclose(score, round(score))
