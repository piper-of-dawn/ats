from datetime import date, timedelta

import pytest

from ats import positioning, shorts
from ats.ticker import EquityTicker


def test_short_trends_sort_reports_and_reset_on_missing_values():
    stock = EquityTicker("TEST")
    stock.short_interest = [
        {"settlement_date": "2026-03-15", "short_interest": 150, "days_to_cover": 4},
        {"settlement_date": "2026-02-15", "short_interest": 120, "days_to_cover": 2},
        {"settlement_date": "2026-01-15", "short_interest": 100, "days_to_cover": 3},
        {"settlement_date": "2026-04-15", "days_to_cover": 0},
        {"settlement_date": "2026-05-15", "short_interest": 200, "days_to_cover": 1},
    ]
    frame = shorts.short_interest_trend(stock)
    assert frame["change_shares"].to_list() == [None, 20, 30, None, None]
    assert frame["change_pct"].to_list() == [None, 20, 25, None, None]
    assert frame["consecutive_increases"].to_list() == [0, 1, 2, 0, 0]
    days = shorts.days_to_cover_trend(stock)
    assert days["change_days"].to_list() == [None, -1, 2, -4, 1]
    assert days["change_pct"][-1] is None


def test_unfetched_and_empty_are_distinct():
    stock = EquityTicker("TEST")
    with pytest.raises(ValueError, match="Fetch"):
        shorts.short_interest_trend(stock)
    stock.short_interest = []
    assert shorts.short_interest_trend(stock).is_empty()
    assert shorts.days_to_cover_trend(stock).is_empty()
    stock.short_volume = []
    assert shorts.short_volume_anomaly(stock).is_empty()


def volumes(values):
    return [{"date": (date(2026, 1, 1) + timedelta(days=i)).isoformat(),
             "short_volume": value, "total_volume": 100} for i, value in enumerate(values)]


def test_anomaly_uses_nonoverlapping_baseline_and_known_arithmetic():
    stock = EquityTicker("TEST")
    stock.short_volume = volumes([10, 20, 30, 40, 60])
    frame = shorts.short_volume_anomaly(stock, recent_window=2, baseline_window=3)
    row = frame.row(-1, named=True)
    assert row["recent_mean"] == 50
    assert row["baseline_mean"] == 20
    assert row["baseline_std"] == 10
    assert row["difference_pp"] == 30
    assert row["anomaly_score"] == 3
    assert frame["anomaly_score"][:-1].null_count() == 4


def test_zero_variance_and_invalid_volume_do_not_produce_infinity():
    stock = EquityTicker("TEST")
    stock.short_volume = volumes([20, 20, 20, 40])
    frame = shorts.short_volume_anomaly(stock, 1, 3)
    assert frame["anomaly_score"][-1] is None
    assert frame["difference_pp"][-1] == 20
    records = volumes([10, 20, 30, 40])
    records[1]["total_volume"] = 0
    stock.short_volume = records
    frame = shorts.short_volume_anomaly(stock, 1, 3)
    assert frame["short_volume_pct"][1] is None
    assert frame["baseline_mean"][-1] is None


@pytest.mark.parametrize("recent,baseline", [(0, 3), (2, 1), (-1, 5)])
def test_invalid_windows(recent, baseline):
    with pytest.raises(ValueError):
        stock = EquityTicker("TEST")
        stock.short_volume = []
        shorts.short_volume_anomaly(stock, recent, baseline)


def test_float_alignment_never_uses_future_measurements():
    stock = EquityTicker("TEST")
    stock.short_interest = [
        {"settlement_date": f"2026-01-{day:02}", "short_interest": 100}
        for day in [1, 10, 15, 20]
    ]
    stock.float_data = [{"effective_date": "2026-01-10", "free_float": 1000}]
    assert shorts.short_interest_as_a_percentage_of_float(stock)["short_interest_pct_of_float"].to_list() == [10, 10, 10, 10]
    result = shorts.short_interest_as_a_percentage_of_float(stock, max_float_age_days=5)
    assert result["short_interest_pct_of_float"].to_list() == [None, 10, 10, None]
    assert result["float_age_days"].to_list() == [None, 0, 5, None]


def article(day, article_id, sentiment=None, ticker="TEST", title=None):
    return {"id": article_id, "published_utc": f"2026-01-{day:02}T12:00:00Z",
            "title": title or article_id,
            "insights": [{"ticker": ticker, "sentiment": sentiment}] if sentiment else []}


def test_news_deduplicates_counts_unknown_labels_and_fills_days():
    rows = [article(1, "one", "positive"), article(1, "one", "positive"),
            article(1, "syndicated", "positive", title="one"),
            article(1, "two", "negative"), article(1, "three", "positive", ticker="OTHER"),
            article(3, "four", "neutral")]
    stock = EquityTicker("TEST")
    stock.news_sentiment = rows
    frame = positioning.news_and_sentiment_trend(stock, start_date="2026-01-01", end_date="2026-01-03")
    assert frame["article_count"].to_list() == [3, 0, 1]
    assert frame["labeled_count"].to_list() == [2, 0, 1]
    assert frame["unlabeled"].to_list() == [1, 0, 0]
    assert frame["sentiment_score"].to_list() == [0, None, 0]


def test_attention_includes_zero_days_in_baseline():
    stock = EquityTicker("TEST")
    stock.news_sentiment = [article(1, "a"), article(3, "b"), article(3, "c"),
                            article(4, "d"), article(4, "e"), article(4, "f")]
    frame = positioning.news_attention_anomaly(stock, 1, 3, start_date="2026-01-01", end_date="2026-01-04")
    row = frame.row(-1, named=True)
    assert row["baseline_mean"] == 1
    assert row["baseline_std"] == 1
    assert row["anomaly_score"] == 2


def test_news_analysis_requires_known_coverage_and_rejects_unfetched_dates(monkeypatch):
    stock = EquityTicker("TEST")
    stock.news_sentiment = []
    with pytest.raises(ValueError, match="both"):
        positioning.news_and_sentiment_trend(stock)
    monkeypatch.setattr("ats.ticker.massive.fetch_news_sentiment", lambda *a, **k: [])
    assert stock.get_news_sentiment("2026-01-01", "2026-01-03") is stock
    assert positioning.news_and_sentiment_trend(stock)["article_count"].to_list() == [0, 0, 0]
    with pytest.raises(ValueError, match="beyond"):
        positioning.news_attention_anomaly(stock, start_date="2025-12-01")


def test_conflicting_report_dates_raise():
    with pytest.raises(ValueError, match="Conflicting"):
        stock = EquityTicker("TEST")
        stock.short_interest = [
            {"settlement_date": "2026-01-01", "short_interest": 10},
            {"settlement_date": "2026-01-01", "short_interest": 20},
        ]
        shorts.short_interest_trend(stock)
