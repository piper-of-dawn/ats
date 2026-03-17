from datetime import date

import polars as pl

from ats.fund_nav import FundState, compute_nav_incremental, nav_step


def test_nav_step_initializes_from_nav0():
    state = FundState()

    nav, units = nav_step(account_value=100.0, cashflow=20.0, state=state, nav0=10.0)

    assert nav == 10.0
    assert units == 10.0
    assert state.last_nav == 10.0
    assert state.last_units == 10.0


def test_nav_step_uses_previous_units_and_none_cashflow():
    state = FundState(last_units=10.0, last_nav=10.0)

    nav, units = nav_step(account_value=110.0, cashflow=None, state=state)

    assert nav == 11.0
    assert units == 10.0
    assert state.last_nav == 11.0
    assert state.last_units == 10.0


def test_compute_nav_incremental_sorts_by_date_and_appends_columns():
    df = pl.DataFrame(
        {
            "date": [date(2026, 3, 16), date(2026, 3, 15), date(2026, 3, 17)],
            "account_value": [110.0, 100.0, 132.0],
            "cashflow": [0.0, 20.0, 12.0],
        }
    )

    result = compute_nav_incremental(df, nav0=10.0)

    assert result["date"].to_list() == [
        date(2026, 3, 15),
        date(2026, 3, 16),
        date(2026, 3, 17),
    ]
    assert result["NAV"].to_list() == [10.0, 11.0, 12.0]
    assert result["units"].to_list() == [10.0, 10.0, 11.0]


def test_compute_nav_incremental_can_continue_from_existing_state():
    df = pl.DataFrame(
        {
            "date": [date(2026, 3, 16), date(2026, 3, 17)],
            "account_value": [110.0, 132.0],
            "cashflow": [0.0, 12.0],
        }
    )
    state = FundState(last_units=10.0, last_nav=10.0)

    result = compute_nav_incremental(df, nav0=10.0, state=state)

    assert result["NAV"].to_list() == [11.0, 12.0]
    assert result["units"].to_list() == [10.0, 11.0]
    assert state.last_nav == 12.0
    assert state.last_units == 11.0
