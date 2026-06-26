from datetime import date

import pytest

from ats.derivatives import options_risk_premium as orp


def test_construct_option_ticker_matches_occ_style_symbol():
    assert (
        orp.construct_option_ticker("SPY", date(2025, 12, 19), "call", 650)
        == "O:SPY251219C00650000"
    )


def test_one_calendar_month_later_clamps_month_end():
    assert orp.one_calendar_month_later(date(2025, 1, 31)) == date(2025, 2, 28)
    assert orp.one_calendar_month_later(date(2024, 1, 31)) == date(2024, 2, 29)
    assert orp.one_calendar_month_later(date(2025, 12, 31)) == date(2026, 1, 31)


def test_choose_nearest_expiration_uses_first_listed_date_on_or_after_target():
    contracts = [
        {"expiration_date": "2025-07-18"},
        {"expiration_date": "2025-08-15"},
        {"expiration_date": "2025-07-25"},
    ]

    assert orp.choose_nearest_expiration(contracts, date(2025, 7, 20)) == date(2025, 7, 25)


def test_choose_atm_contracts_returns_call_and_put_at_nearest_shared_strike():
    chain = [
        {"details": {"contract_type": "call", "strike_price": 95, "ticker": "O:ABC"}},
        {"details": {"contract_type": "put", "strike_price": 95, "ticker": "O:ABP"}},
        {"details": {"contract_type": "call", "strike_price": 100, "ticker": "O:ACC"}},
        {"details": {"contract_type": "put", "strike_price": 100, "ticker": "O:ACP"}},
        {"details": {"contract_type": "call", "strike_price": 105, "ticker": "O:ADC"}},
    ]

    strike, call_row, put_row = orp.choose_atm_contracts(chain, 101.2)

    assert strike == 100
    assert call_row["details"]["ticker"] == "O:ACC"
    assert put_row["details"]["ticker"] == "O:ACP"


def test_black_scholes_iv_solver_round_trips_call_and_put_prices():
    call_price = orp.black_scholes_price(
        100,
        100,
        30 / 365,
        0.30,
        contract_type="call",
    )
    put_price = orp.black_scholes_price(
        100,
        100,
        30 / 365,
        0.40,
        contract_type="put",
    )

    call_iv = orp.implied_volatility_from_option_price(
        call_price,
        100,
        100,
        30 / 365,
        contract_type="call",
    )
    put_iv = orp.implied_volatility_from_option_price(
        put_price,
        100,
        100,
        30 / 365,
        contract_type="put",
    )

    assert call_iv == pytest.approx(0.30, abs=1e-4)
    assert put_iv == pytest.approx(0.40, abs=1e-4)
    assert orp.average_implied_vol_from_prices(
        call_price,
        put_price,
        100,
        100,
        30 / 365,
    ) == pytest.approx(0.35, abs=1e-4)


def test_realized_volatility_and_premium():
    prices = [100, 101, 102, 103, 104, 105, 104, 106, 107, 108, 109, 108]

    rv = orp.realized_volatility(prices, window=5)

    assert rv > 0
    assert orp.implied_risk_premium(0.40, 0.25) == pytest.approx(0.15)


def test_latest_close_from_bars_uses_latest_timestamp():
    assert orp.latest_close_from_bars(
        [
            {"t": 2, "c": 1.25},
            {"t": 1, "c": 1.10},
            {"t": 3, "c": 1.30},
        ]
    ) == 1.30


def test_rate_limited_waits_after_configured_number_of_calls():
    clock = {"now": 0.0}
    sleeps = []

    def now():
        return clock["now"]

    def sleep(seconds):
        sleeps.append(seconds)
        clock["now"] += seconds

    calls = []

    @orp.rate_limited(calls_per_minute=2, time_func=now, sleep_func=sleep)
    def record(value):
        calls.append(value)
        return value

    assert record(1) == 1
    assert record(2) == 2
    assert record(3) == 3
    assert sleeps == [60.0]
    assert calls == [1, 2, 3]


def test_calculate_option_risk_premium_uses_discovered_expiry_and_atm_average(monkeypatch):
    contracts = [
        {
            "contract_type": "call",
            "expiration_date": "2025-07-18",
            "strike_price": 100,
            "ticker": "O:TEST250718C00100000",
        },
        {
            "contract_type": "call",
            "expiration_date": "2025-07-25",
            "strike_price": 100,
            "ticker": "O:TEST250725C00100000",
        },
        {
            "contract_type": "put",
            "expiration_date": "2025-07-25",
            "strike_price": 100,
            "ticker": "O:TEST250725P00100000",
        },
    ]

    monkeypatch.setattr(orp, "fetch_current_price", lambda ticker: 101.0)
    monkeypatch.setattr(orp, "fetch_candidate_contracts", lambda ticker, target_date, api_key=None: contracts)
    monkeypatch.setattr(
        orp,
        "fetch_latest_option_close",
        lambda option_ticker, as_of_date, api_key=None: 4.0
        if option_ticker.endswith("C00100000")
        else 2.5,
    )
    monkeypatch.setattr(orp, "fetch_recent_closes", lambda ticker: list(range(100, 146)))

    result = orp.calculate_option_risk_premium("test", as_of_date=date(2025, 6, 20), api_key="key")

    assert result.ticker == "TEST"
    assert result.target_date == date(2025, 7, 20)
    assert result.expiration_date == date(2025, 7, 25)
    assert result.atm_strike == 100
    assert result.call_ticker == "O:TEST250725C00100000"
    assert result.put_ticker == "O:TEST250725P00100000"
    assert result.call_price == 4.0
    assert result.put_price == 2.5
    assert result.implied_vol > 0
    assert result.implied_risk_premium == pytest.approx(result.implied_vol - result.realized_vol)
