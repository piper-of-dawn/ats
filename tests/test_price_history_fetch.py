from datetime import date

import pandas as pd
import polars as pl

import ats.yahoo_finance as yahoo_finance
from ats.ticker import EquityTicker


def _price_frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "Date": pd.to_datetime(["2000-01-03"]),
            "Adj Close": [100.0],
        }
    )


def test_yahoo_fetch_price_data_uses_max_period_for_all_history(monkeypatch):
    captured = {}

    def fake_download(ticker, **kwargs):
        captured["ticker"] = ticker
        captured["kwargs"] = kwargs
        return _price_frame()

    monkeypatch.setattr(yahoo_finance.yf, "download", fake_download)

    result = yahoo_finance.fetch_price_data(
        "AAPL",
        cooldown_range=None,
        all_available_price_history=True,
    )

    assert captured["ticker"] == "AAPL"
    assert captured["kwargs"]["period"] == "max"
    assert "start" not in captured["kwargs"]
    assert "end" not in captured["kwargs"]
    assert result.to_dicts() == [
        {"date": date(2000, 1, 3), "close": 100.0, "ticker": "AAPL"}
    ]


def test_equity_ticker_forwards_all_history_flag(monkeypatch):
    captured = {}

    def fake_fetch_price_data(ticker, **kwargs):
        captured["ticker"] = ticker
        captured["kwargs"] = kwargs
        return pl.DataFrame(
            {
                "date": [date(2000, 1, 3)],
                "close": [100.0],
                "ticker": [ticker],
            }
        )

    monkeypatch.setattr("ats.ticker.fetch_price_data", fake_fetch_price_data)

    equity_ticker = EquityTicker("AAPL").fetch_price_data(
        all_available_price_history=True
    )

    assert captured == {
        "ticker": "AAPL",
        "kwargs": {"all_available_price_history": True},
    }
    assert equity_ticker.price_data["ticker"].to_list() == ["AAPL"]
