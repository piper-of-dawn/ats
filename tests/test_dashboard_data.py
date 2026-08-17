import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import dashboard.data as data
from dashboard.app import create_app
import polars as pl
from flask import render_template


def test_dashboard_columns_exclude_option_implied_risk_premium(monkeypatch):
    monkeypatch.setattr(
        data,
        "get_table_columns",
        lambda table_name: [
            "ticker",
            "combined_score",
            "option_implied_risk_premium",
            "as_of_date",
        ],
    )
    monkeypatch.setattr(data, "get_date_column_name", lambda table_name: "as_of_date")

    columns = data.get_dashboard_columns("us_largecap_metrics")

    assert columns == [
        "ticker",
        "combined_score",
        "as_of_date",
    ]


def test_dashboard_context_uses_default_labels_for_selected_columns(monkeypatch):
    monkeypatch.setattr(
        data,
        "get_dashboard_columns",
        lambda table_name: ["ticker", "combined_score"],
    )
    monkeypatch.setattr(data, "fetch_recent_dates", lambda table_name, limit=7: [])
    monkeypatch.setattr(
        data,
        "fetch_rows_for_selected_date",
        lambda table_name, selected_date, columns: pl.DataFrame(schema=columns),
    )

    context = data.get_dashboard_context("us_largecap_metrics")

    assert context["column_labels"]["ticker"] == "TICKER"
    assert context["column_labels"]["combined_score"] == "Combined Score"


def test_dashboard_context_adds_momentum_rank_after_ticker(monkeypatch):
    monkeypatch.setattr(
        data,
        "get_dashboard_columns",
        lambda table_name: ["ticker", "combined_score", "ltm", "stm", "rating"],
    )
    monkeypatch.setattr(
        data, "fetch_recent_dates", lambda table_name, limit=7: ["2026-08-17"]
    )
    monkeypatch.setattr(
        data,
        "fetch_rows_for_selected_date",
        lambda table_name, selected_date, columns: pl.DataFrame(
            {
                "ticker": ["LOW", "HIGH"],
                "combined_score": [0.4, 0.8],
                "ltm": [1.0, 2.0],
                "stm": [0.5, 1.5],
                "rating": [0.2, 0.9],
            }
        ),
    )

    context = data.get_dashboard_context("us_largecap_metrics")

    assert context["ranking_enabled"] is True
    assert context["columns"][:3] == ["ticker", "momentum_rank", "combined_score"]
    assert context["column_labels"]["momentum_rank"] == "Momentum Rank"
    assert context["mobile_primary_columns"][:2] == ["ticker", "momentum_rank"]
    assert context["rows"][0]["ticker"] == "HIGH"
    assert context["rows"][0]["momentum_rank"] == 1
    assert len(context["ranking_strategies"]) == 4


def test_momentum_rankings_keep_ltm_quintile_first_and_offer_distinct_strategies():
    rows = [
        {"ticker": "A", "ltm": 10.0, "stm": 1.0, "analyst_rating": 1.0},
        {"ticker": "B", "ltm": 9.0, "stm": 10.0, "analyst_rating": 0.0},
    ]
    rows.extend(
        {
            "ticker": f"T{index}",
            "ltm": float(index),
            "stm": float(index + 1),
            "analyst_rating": index / 10,
        }
        for index in range(8)
    )

    assert data.add_momentum_rankings(
        rows, ["ticker", "ltm", "stm", "analyst_rating"]
    )

    by_ticker = {row["ticker"]: row for row in rows}
    assert by_ticker["B"]["_momentum_ranks"]["momentum_first"] == 1
    assert by_ticker["B"]["_momentum_ranks"]["momentum_balance"] == 1
    assert by_ticker["A"]["_momentum_ranks"]["analyst_confirmed"] == 1
    assert by_ticker["A"]["_momentum_ranks"]["strict_momentum_sequence"] == 1
    assert by_ticker["A"]["_ltm_top_quintile"] is True
    assert by_ticker["B"]["_ltm_top_quintile"] is True
    assert all(
        by_ticker["A"]["_momentum_ranks"][strategy] < by_ticker["T7"]["_momentum_ranks"][strategy]
        for strategy in by_ticker["A"]["_momentum_ranks"]
    )


def test_momentum_rankings_leave_any_incomplete_row_unranked():
    rows = [
        {"ticker": "A", "ltm": 2.0, "stm": 1.0, "rating": 0.8},
        {"ticker": "B", "ltm": 1.0, "stm": 2.0, "rating": None},
    ]

    assert data.add_momentum_rankings(rows, ["ticker", "ltm", "stm", "rating"])

    assert rows[0]["momentum_rank"] == 1
    assert rows[1]["momentum_rank"] is None
    assert all(rank is None for rank in rows[1]["_momentum_ranks"].values())


def test_momentum_rankings_break_exact_ties_by_ticker():
    rows = [
        {"ticker": "ZZZ", "ltm": -1.0, "stm": -2.0, "analyst_rating": 0.5},
        {"ticker": "AAA", "ltm": -1.0, "stm": -2.0, "analyst_rating": 0.5},
    ]

    data.add_momentum_rankings(rows, ["ticker", "ltm", "stm", "analyst_rating"])

    by_ticker = {row["ticker"]: row for row in rows}
    assert by_ticker["AAA"]["momentum_rank"] == 1
    assert by_ticker["ZZZ"]["momentum_rank"] == 2


def test_dashboard_fetches_all_rows_for_selected_date(monkeypatch):
    calls = []

    monkeypatch.setattr(data, "empty_table_frame", lambda table_name, columns: None)
    monkeypatch.setattr(
        data,
        "fetch_rows_for_date",
        lambda table_name, selected_date, columns: calls.append(
            (table_name, selected_date, columns)
        )
        or pl.DataFrame({"ticker": ["A", "B"], "stm": [1.0, -1.0]}),
    )

    result = data.fetch_rows_for_selected_date(
        "us_largecap_metrics",
        "2026-07-01",
        ["ticker", "stm"],
    )

    assert calls == [("us_largecap_metrics", "2026-07-01", ["ticker", "stm"])]
    assert result.height == 2


def test_table_template_renders_pagination_without_truncating_rows():
    app = create_app()
    rows = [{"ticker": f"T{i}", "stm": i} for i in range(12)]

    with app.app_context():
        html = render_template(
            "table.html",
            display_title="US Large Cap Highlights",
            columns=["ticker", "stm"],
            column_labels={"ticker": "TICKER", "stm": "STM"},
            rows=rows,
            mobile_primary_columns=["ticker", "stm"],
            mobile_detail_columns=[],
        )

    assert 'data-table-display-limit' not in html
    assert 'data-table-pagination' in html
    assert 'data-table-page-size' in html
    assert "hidden" not in html
    assert "T11" in html


def test_table_template_renders_momentum_strategy_controls_and_rank_metadata():
    app = create_app()
    rows = [
        {"ticker": "A", "ltm": 2.0, "stm": 1.0, "analyst_rating": 0.9},
        {"ticker": "B", "ltm": 1.0, "stm": 2.0, "analyst_rating": None},
    ]
    data.add_momentum_rankings(rows, ["ticker", "ltm", "stm", "analyst_rating"])

    with app.app_context():
        html = render_template(
            "table.html",
            display_title="US Large Cap Highlights",
            columns=["ticker", "momentum_rank", "ltm", "stm", "analyst_rating"],
            column_labels={
                "ticker": "TICKER",
                "momentum_rank": "Momentum Rank",
                "ltm": "LTM",
                "stm": "STM",
                "analyst_rating": "Analyst Rating",
            },
            rows=rows,
            mobile_primary_columns=[
                "ticker",
                "momentum_rank",
                "ltm",
                "stm",
                "analyst_rating",
            ],
            mobile_detail_columns=[],
            ranking_enabled=True,
            ranking_strategies=data._MOMENTUM_STRATEGIES,
            default_ranking_strategy=data._DEFAULT_MOMENTUM_STRATEGY,
        )

    assert 'data-default-ranking-strategy="momentum_first"' in html
    assert 'value="rank:momentum_first"' in html
    assert 'value="rank:strict_momentum_sequence"' in html
    assert "50% LTM, 35% STM, 15% analyst rating" in html
    assert 'data-rank-momentum-first="1"' in html
    assert "momentum-rank-badge-top" in html
    assert "—" in html
