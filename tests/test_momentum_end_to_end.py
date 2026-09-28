"""Deterministic ticker and Dagster coverage without Yahoo or database access."""

import math

import numpy as np
import pandas as pd
import polars as pl
import pytest
from yfinance import Ticker as YfTicker

from ats.momentum import calculate_momentum
from ats.orchestration import factor_metrics_job
from ats.ticker import EquityTicker


@pytest.fixture
def yahoo_history(monkeypatch):
    """Provide 160 known daily returns through the real Yahoo price adapter."""
    index = pd.bdate_range("2025-01-02", periods=161, name="Date")
    t = np.arange(1, 161, dtype=float)
    market_returns = 0.007 * np.sin(t * 0.41) + 0.003 * np.cos(t * 0.17)
    residual = 0.0025 * np.cos(t * 0.31) + 0.0008 * np.sin(t * 0.71)
    equity_returns = 1.4 * market_returns + residual
    calls = []

    def frame(returns):
        adjusted = 100 * np.exp(np.r_[0.0, returns].cumsum())
        return pd.DataFrame(
            {
                "Close": adjusted * (1 + np.arange(161) * 0.001),
                "Adj Close": adjusted,
            },
            index=index,
        )

    history = {"^GSPC": frame(market_returns), "E2E": frame(equity_returns)}

    def fake_history(self, **kwargs):
        calls.append((self.ticker, kwargs))
        return history[self.ticker].copy()

    monkeypatch.setattr(YfTicker, "history", fake_history)
    return history, calls


def test_ticker_fetch_through_both_momentum_signals(yahoo_history):
    history, calls = yahoo_history
    market = EquityTicker("^GSPC").fetch_price_data()
    equity = EquityTicker("E2E", mkt_index=market).fetch_price_data()

    result = calculate_momentum(equity)

    assert [symbol for symbol, _ in calls] == ["^GSPC", "E2E"]
    assert all(options == {"period": "1y", "auto_adjust": False, "actions": False}
               for _, options in calls)
    assert equity.price_data["close"][-1] == pytest.approx(history["E2E"]["Adj Close"].iloc[-1])
    assert equity.price_data["close"][-1] != pytest.approx(history["E2E"]["Close"].iloc[-1])
    assert result.prepared_data.height == 160
    assert result.prepared_data["date"].is_sorted()
    assert result.beta == pytest.approx(1.4389313782077018, abs=1e-10)
    assert result.stm == pytest.approx(-0.7481600620957148, abs=1e-9)
    assert result.ltm == pytest.approx(-0.3834019829580016, abs=1e-9)
    assert len(result.stm_series) == len(result.ltm_series) == 160
    assert all(math.isfinite(value) for value in (result.beta, result.stm, result.ltm))
    assert equity.price_data.columns == market.price_data.columns == ["date", "close", "ticker"]


def test_daily_job_writes_momentum_for_one_ticker(yahoo_history, monkeypatch):
    _, calls = yahoo_history
    monkeypatch.setattr(
        YfTicker,
        "get_recommendations_summary",
        lambda self: [
            {"strongBuy": 3, "buy": 2, "hold": 1, "sell": 0, "strongSell": 0},
            {"strongBuy": 2, "buy": 2, "hold": 2, "sell": 0, "strongSell": 0},
        ],
    )
    monkeypatch.setattr(
        YfTicker,
        "get_analyst_price_targets",
        lambda self: {"current": 110.0, "low": 80.0, "median": 100.0, "high": 130.0},
    )
    monkeypatch.setattr(
        "ats.orchestration.fetch_table",
        lambda table: pl.DataFrame({"yahoo_finance_ticker": ["E2E"]}),
    )
    writes = []
    monkeypatch.setattr(
        "ats.orchestration.batch_insert_polars_df",
        lambda *args, **kwargs: writes.append((args, kwargs)),
    )

    run = factor_metrics_job.execute_in_process(run_config={
        "ops": {
            "source_tickers_from_database": {"config": {"source_table": "source_universe"}},
            "compute_equity_factor_metrics": {"config": {"market_index": "^GSPC"}},
            "write_factor_metrics_to_database": {"config": {"target_table": "target_metrics"}},
        }
    })

    assert run.success
    assert [symbol for symbol, _ in calls] == ["^GSPC", "E2E"]
    assert len(writes) == 1
    (matrix, columns, table), options = writes[0]
    assert table == "target_metrics"
    assert columns == matrix.columns
    assert options == {"conflict_columns": ["ticker", "as_of_date"], "overwrite_conflicts": True}
    assert matrix.height == 1
    row = matrix.row(0, named=True)
    assert row["ticker"] == "E2E"
    assert row["beta"] == pytest.approx(1.44)
    assert row["stm"] == pytest.approx(-0.75)
    assert row["ltm"] == pytest.approx(-0.38)
    assert row["as_of_date"] is not None
