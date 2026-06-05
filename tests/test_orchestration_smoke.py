import math
import os

import polars as pl
import pytest
from dagster import build_op_context

from ats.orchestration import (
    build_factor_matrix,
    compute_equity_factor_metrics,
    compute_equity_factor_metric_row,
    source_ticker_symbols_from_database,
)


def test_first_two_us_largecap_tickers_generate_factor_matrix_without_database_write():
    if not os.getenv("SUPABASE_PASSWORD"):
        pytest.skip("SUPABASE_PASSWORD is required for live orchestration smoke test")

    tickers = source_ticker_symbols_from_database("us_largecap", limit=2)
    if len(tickers) < 2:
        pytest.skip("us_largecap returned fewer than two tickers")

    rows = [compute_equity_factor_metric_row(ticker, "^GSPC") for ticker in tickers]
    factor_matrix = build_factor_matrix(rows)

    assert factor_matrix.columns == [
        "ticker",
        "ltm",
        "stm",
        "beta",
        "as_of_date",
        "analyst_price_target_deviation",
        "analyst_rating",
        "combined_score",
    ]
    assert factor_matrix.height == 2
    assert factor_matrix["ticker"].to_list() == sorted(tickers)
    assert factor_matrix["as_of_date"].dtype == pl.Date
    for column in ["ltm", "stm", "beta", "analyst_rating", "combined_score"]:
        assert all(math.isfinite(value) for value in factor_matrix[column].drop_nulls())


def test_failed_ticker_returns_null_factor_row(monkeypatch):
    def fail_for_ticker(equity_ticker_symbol, market_index):
        raise ValueError("missing price data")

    monkeypatch.setattr(
        "ats.orchestration.compute_equity_factor_metric_row", fail_for_ticker
    )

    context = build_op_context(op_config={"market_index": "^GSPC"})

    assert compute_equity_factor_metrics(context, "CADE") == {
        "ticker": "CADE",
        "ltm": None,
        "stm": None,
        "beta": None,
        "cbs": None,
        "analyst_price_target_deviation": None,
    }
