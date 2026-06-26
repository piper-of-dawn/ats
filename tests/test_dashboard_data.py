import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import dashboard.data as data
import polars as pl


def test_dashboard_columns_include_option_implied_risk_premium(monkeypatch):
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
        "option_implied_risk_premium",
        "as_of_date",
    ]


def test_option_implied_risk_premium_has_dashboard_label(monkeypatch):
    monkeypatch.setattr(
        data,
        "get_dashboard_columns",
        lambda table_name: ["ticker", "option_implied_risk_premium"],
    )
    monkeypatch.setattr(data, "fetch_recent_dates", lambda table_name, limit=7: [])
    monkeypatch.setattr(
        data,
        "fetch_rows_for_selected_date",
        lambda table_name, selected_date, columns: pl.DataFrame(schema=columns),
    )

    context = data.get_dashboard_context("us_largecap_metrics")

    assert context["column_labels"]["option_implied_risk_premium"] == "Option IV Premium"
