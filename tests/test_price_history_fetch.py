from datetime import date
from datetime import timedelta
import math
import os

import numpy as np
import pandas as pd
import polars as pl
import pytest

from ats.ticker import EquityTicker, YfTicker
from ats.momentum import MomentumConfig, calculate_momentum
from ats.fundamentals.analyst_metrics import calculate_analyst_metrics


def test_equity_ticker_uses_superclass_history_for_price_data(monkeypatch):
    captured = {}

    def fake_history(self, **kwargs):
        captured["kwargs"] = kwargs
        return pd.DataFrame(
            {"Adj Close": [100.0]},
            index=pd.DatetimeIndex(["2000-01-03"], name="Date"),
        )

    monkeypatch.setattr(YfTicker, "history", fake_history)

    equity_ticker = EquityTicker("AAPL")

    assert captured == {}
    assert equity_ticker.price_data is None
    equity_ticker.fetch_price_data()
    assert equity_ticker.price_data.to_dicts() == [
        {"date": date(2000, 1, 3), "close": 100.0, "ticker": "AAPL"}
    ]
    assert captured["kwargs"]["period"] == "1y"


def test_equity_ticker_fetch_price_data_can_override_period(monkeypatch):
    captured_periods = []

    def fake_history(self, **kwargs):
        captured_periods.append(kwargs["period"])
        close = 100.0 if kwargs["period"] == "1y" else 500.0
        return pd.DataFrame(
            {"Adj Close": [close]},
            index=pd.DatetimeIndex(["2000-01-03"], name="Date"),
        )

    monkeypatch.setattr(YfTicker, "history", fake_history)

    equity_ticker = EquityTicker("AAPL")

    assert equity_ticker.price_data is None
    equity_ticker.fetch_price_data()
    assert equity_ticker.price_data["close"].to_list() == [100.0]

    equity_ticker.fetch_price_data(period="5y")

    assert captured_periods == ["1y", "5y"]
    assert equity_ticker.price_data["close"].to_list() == [500.0]


def test_equity_ticker_fetch_price_data_can_fetch_all_available_history(monkeypatch):
    captured = {}

    def fake_history(self, **kwargs):
        captured["kwargs"] = kwargs
        return pd.DataFrame(
            {"Adj Close": [100.0]},
            index=pd.DatetimeIndex(["2000-01-03"], name="Date"),
        )

    monkeypatch.setattr(YfTicker, "history", fake_history)

    EquityTicker("AAPL").fetch_price_data(all_available_price_history=True)

    assert captured["kwargs"]["period"] == "max"


def test_momentum_lookback_window_controls_beta_and_idiosyncratic_returns():
    start = date(2024, 1, 1)
    dates = [start + timedelta(days=day) for day in range(7)]
    market_returns = np.array([0.05, -0.03, 0.02, 0.01, -0.02, 0.03])
    ticker_returns = np.array([-0.02, 0.04, -0.01, 0.02, -0.04, 0.06])

    def closes_from_returns(returns):
        closes = [100.0]
        for return_ in returns:
            closes.append(closes[-1] * np.exp(return_))
        return closes

    market = EquityTicker(
        "^GSPC",
        price_data=pl.DataFrame(
            {"date": dates, "close": closes_from_returns(market_returns), "ticker": ["^GSPC"] * 7}
        ),
    )
    ticker = EquityTicker(
        "AAPL",
        mkt_index=market,
        price_data=pl.DataFrame(
            {"date": dates, "close": closes_from_returns(ticker_returns), "ticker": ["AAPL"] * 7}
        ),
    )

    result = calculate_momentum(
        ticker, config=MomentumConfig(winsorize=False, lookback_window=3)
    )

    assert ticker.price_data.height == 7
    assert result.prepared_data.height == 3
    assert result.prepared_data["date"].to_list() == dates[-3:]
    assert result.beta == pytest.approx(2.0)
    assert result.prepared_data["idiosyncratic_returns"].to_list() == pytest.approx([0.0, 0.0, 0.0])


def test_momentum_rebuilds_returns_after_price_change_and_ignores_old_columns():
    dates = [date(2024, 1, day) for day in range(1, 8)]

    def prices(changes, symbol):
        closes = [100.0]
        for change in changes:
            closes.append(closes[-1] * np.exp(change))
        return pl.DataFrame({"date": dates, "close": closes, "ticker": [symbol] * 7})

    market = EquityTicker("^GSPC", price_data=prices([.01, .02, -.01, .03, -.02, .04], "^GSPC"))
    ticker = EquityTicker("AAPL", market, prices([.03, .01, -.02, .05, -.01, .02], "AAPL"))
    original = ticker.price_data.clone()
    first = calculate_momentum(ticker)
    raw = calculate_momentum(ticker, config=MomentumConfig(use_idiosyncratic_returns=False))
    assert raw.beta is None
    assert raw.prepared_data["idiosyncratic_returns"].to_list() == pytest.approx(
        raw.prepared_data["log_return"].to_list()
    )
    assert ticker.price_data.equals(original)
    assert market.price_data.columns == ["date", "close", "ticker"]

    ticker.price_data = prices([-.02, .04, .01, -.01, .06, -.03], "AAPL").with_columns(
        pl.lit(999.0).alias("idiosyncratic_returns")
    )
    changed = calculate_momentum(ticker)
    assert changed.beta != pytest.approx(first.beta)
    assert changed.prepared_data["idiosyncratic_returns"].max() < 999
    assert calculate_momentum(ticker).stm == pytest.approx(changed.stm)


def test_momentum_requires_explicitly_loaded_prices():
    with pytest.raises(ValueError, match="Load price data"):
        calculate_momentum(EquityTicker("AAPL"))


def test_equity_ticker_get_combined_rating_uses_cbs(monkeypatch):
    recommendations = [
        {"strongBuy": 2, "buy": 1, "hold": 1, "sell": 0, "strongSell": 0},
        {"strongBuy": 1, "buy": 2, "hold": 1, "sell": 0, "strongSell": 0},
    ]
    monkeypatch.setattr(EquityTicker, "get_recommendations_summary", lambda self: recommendations)
    monkeypatch.setattr(EquityTicker, "get_analyst_price_targets", lambda self: {})

    equity_ticker = EquityTicker("AAPL").fetch_analyst_data()
    metrics = calculate_analyst_metrics(equity_ticker)

    assert isinstance(metrics.combined_rating, float)
    assert metrics.combined_rating > 0


def test_equity_ticker_get_analyst_price_target_deviation(monkeypatch):
    price_targets = {"current": 90.0, "low": 80.0, "median": 100.0, "high": 130.0}
    monkeypatch.setattr(EquityTicker, "get_analyst_price_targets", lambda self: price_targets)

    monkeypatch.setattr(EquityTicker, "get_recommendations_summary", lambda self: [
        {"strongBuy": 1, "buy": 1, "hold": 1, "sell": 0, "strongSell": 0}
    ])
    equity_ticker = EquityTicker("AAPL").fetch_analyst_data()
    metrics = calculate_analyst_metrics(equity_ticker)

    assert metrics.price_target_deviation == -0.33


def test_aapl_momentum_calculations_use_fetched_price_data():
    if os.getenv("RUN_LIVE_YAHOO_TESTS") != "1":
        pytest.skip("Set RUN_LIVE_YAHOO_TESTS=1 to run the live Yahoo smoke test")
    market_ticker = EquityTicker("^GSPC").fetch_price_data()
    equity_ticker = EquityTicker("AAPL", market_ticker).fetch_price_data()

    if equity_ticker.price_data.height < 120 or market_ticker.price_data.height < 120:
        pytest.skip("Yahoo Finance returned too little AAPL history for momentum smoke test")

    metrics = calculate_momentum(equity_ticker)

    assert {"date", "close", "ticker", "log_return", "mkt_log_return", "idiosyncratic_returns"}.issubset(
        metrics.prepared_data.schema
    )
    assert math.isfinite(metrics.beta)
    assert math.isfinite(metrics.ltm)
    assert math.isfinite(metrics.stm)
