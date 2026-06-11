from datetime import date
from datetime import timedelta
import math

import numpy as np
import pandas as pd
import polars as pl
import pytest

from ats.ticker import EquityTicker, YfTicker


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

    ticker.get_short_term_momentum_signal(winsorize=False, lookback_window=3)

    assert ticker.price_data.height == 3
    assert ticker.price_data["date"].to_list() == dates[-3:]
    assert ticker.beta == pytest.approx(2.0)
    assert ticker.price_data["idiosyncratic_returns"].to_list() == pytest.approx([0.0, 0.0, 0.0])


def test_equity_ticker_get_combined_rating_uses_cbs(monkeypatch):
    recommendations = [
        {"strongBuy": 2, "buy": 1, "hold": 1, "sell": 0, "strongSell": 0},
        {"strongBuy": 1, "buy": 2, "hold": 1, "sell": 0, "strongSell": 0},
    ]
    monkeypatch.setattr(EquityTicker, "get_recommendations_summary", lambda self: recommendations)

    equity_ticker = EquityTicker("AAPL").getCombinedRating()

    assert isinstance(equity_ticker.combined_rating, float)
    assert equity_ticker.combined_rating > 0


def test_equity_ticker_get_analyst_price_target_deviation(monkeypatch):
    price_targets = {"current": 90.0, "low": 80.0, "median": 100.0, "high": 130.0}
    monkeypatch.setattr(EquityTicker, "get_analyst_price_targets", lambda self: price_targets)

    equity_ticker = EquityTicker("AAPL").getAnalystPriceTargetDeviation()

    assert equity_ticker.analyst_price_target_deviation == -0.33


def test_aapl_momentum_calculations_use_fetched_price_data():
    market_ticker = EquityTicker("^GSPC")
    equity_ticker = EquityTicker("AAPL", market_ticker)

    if equity_ticker.price_data.height < 120 or market_ticker.price_data.height < 120:
        pytest.skip("Yahoo Finance returned too little AAPL history for momentum smoke test")

    equity_ticker = (
        equity_ticker.get_long_term_momentum_signal()
        .get_short_term_momentum_signal()
    )

    assert {"date", "close", "ticker", "log_return", "mkt_log_return", "idiosyncratic_returns"}.issubset(
        equity_ticker.price_data.schema
    )
    assert math.isfinite(equity_ticker.beta)
    assert math.isfinite(equity_ticker.ltm)
    assert math.isfinite(equity_ticker.stm)
