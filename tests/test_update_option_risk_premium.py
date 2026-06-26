from datetime import date
from types import SimpleNamespace

import polars as pl
import pytest

from ats.derivatives import update_option_risk_premium as updater


def test_resolve_source_and_target_tables():
    assert updater.resolve_source_table("largecap") == "us_largecap"
    assert updater.resolve_source_table("us_midcap") == "us_midcap"
    assert updater.resolve_target_table("us_largecap") == "us_largecap_metrics"
    assert updater.resolve_target_table("us_midcap") == "us_midcap_metrics"

    with pytest.raises(ValueError, match="table must be one of"):
        updater.resolve_source_table("smallcap")


def test_source_tickers_reads_yahoo_finance_ticker_column(monkeypatch):
    monkeypatch.setattr(
        updater,
        "fetch_table",
        lambda table, columns=None: pl.DataFrame(
            {"yahoo_finance_ticker": [" amat ", None, "glw"]}
        ),
    )

    assert updater.source_tickers("us_largecap") == ["AMAT", "GLW"]
    assert updater.source_tickers("us_largecap", limit=1) == ["AMAT"]


def test_build_option_risk_premium_frame_sorts_and_selects_output_columns():
    df = updater.build_option_risk_premium_frame(
        [
            {
                "ticker": "GLW",
                "as_of_date": date(2026, 6, 26),
                "option_implied_risk_premium": 0.2,
                "ignored": "x",
            },
            {
                "ticker": "AMAT",
                "as_of_date": date(2026, 6, 26),
                "option_implied_risk_premium": None,
            },
        ]
    )

    assert df.columns == updater.OUTPUT_COLUMNS
    assert df["ticker"].to_list() == ["AMAT", "GLW"]
    assert df["option_implied_risk_premium"].dtype == pl.Float64


def test_update_option_risk_premiums_computes_rows_and_writes(monkeypatch):
    writes = {}

    monkeypatch.setattr(updater, "source_tickers", lambda source_table, limit=None: ["AMAT", "FAIL"])

    def fake_calculate(ticker, as_of_date=None):
        if ticker == "FAIL":
            raise ValueError("no option data")
        return SimpleNamespace(implied_risk_premium=0.1234)

    monkeypatch.setattr(updater, "calculate_option_risk_premium", fake_calculate)
    monkeypatch.setattr(
        updater,
        "add_columns_if_missing",
        lambda table, columns: writes.setdefault("add_columns", (table, columns)),
    )
    monkeypatch.setattr(
        updater,
        "batch_insert_polars_df",
        lambda df, columns, table, conflict_columns=None, overwrite_conflicts=False: writes.setdefault(
            "insert",
            (df, columns, table, conflict_columns, overwrite_conflicts),
        ),
    )

    df = updater.update_option_risk_premiums(
        "largecap",
        limit=2,
        as_of_date=date(2026, 6, 26),
    )

    assert df["ticker"].to_list() == ["AMAT", "FAIL"]
    assert df["option_implied_risk_premium"].to_list()[0] == pytest.approx(0.1234)
    assert df["option_implied_risk_premium"].to_list()[1] is None
    assert writes["add_columns"] == (
        "us_largecap_metrics",
        {"option_implied_risk_premium": "double precision"},
    )
    _, columns, table, conflict_columns, overwrite_conflicts = writes["insert"]
    assert columns == updater.OUTPUT_COLUMNS
    assert table == "us_largecap_metrics"
    assert conflict_columns == ["ticker", "as_of_date"]
    assert overwrite_conflicts is True
