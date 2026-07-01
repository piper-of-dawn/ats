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
