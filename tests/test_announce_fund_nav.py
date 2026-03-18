from datetime import date

import polars as pl

from ats.announce_fund_nav import build_insert_rows, build_seed_state, normalize_source_rows
from ats.fund_nav import FundState, compute_nav_incremental


def test_normalize_source_rows_keeps_latest_row_per_date_and_derives_cashflow():
    df = pl.DataFrame(
        {
            "date": [date(2026, 3, 15), date(2026, 3, 15), date(2026, 3, 16)],
            "account_value": [100.0, 101.5, 110.0],
            "deposits": [20.0, 25.0, None],
            "withdrawals": [0.0, -5.0, None],
        }
    )

    result = normalize_source_rows(df)

    assert result.to_dicts() == [
        {"date": date(2026, 3, 15), "account_value": 101.5, "cashflow": 20.0},
        {"date": date(2026, 3, 16), "account_value": 110.0, "cashflow": 0.0},
    ]


def test_normalize_source_rows_keeps_negative_withdrawals_negative_in_cashflow():
    df = pl.DataFrame(
        {
            "date": [date(2026, 3, 15)],
            "account_value": [100.0],
            "deposits": [0.0],
            "withdrawals": [-9.61],
        }
    )

    result = normalize_source_rows(df)

    assert result.to_dicts() == [
        {"date": date(2026, 3, 15), "account_value": 100.0, "cashflow": -9.61},
    ]


def test_incremental_nav_continues_from_existing_state():
    source_df = pl.DataFrame(
        {
            "date": [date(2026, 3, 16), date(2026, 3, 17)],
            "account_value": [110.0, 132.0],
            "cashflow": [0.0, 12.0],
        }
    )

    state = FundState(last_units=10.0, last_nav=10.0)
    result = compute_nav_incremental(source_df, nav0=10.0, state=state)

    assert result["NAV"].to_list() == [11.0, 12.0]
    assert result["units"].to_list() == [10.0, 11.0]
    assert state.last_units == 11.0


def test_build_insert_rows_maps_optional_columns_by_name():
    df = pl.DataFrame(
        {
            "date": [date(2026, 3, 15)],
            "nav": [10.0],
            "units": [10.0],
            "account_value": [100.0],
            "cashflow": [20.0],
        }
    )

    columns, rows = build_insert_rows(
        df,
        ["date", "nav", "units", "account_value", "cashflow"],
    )

    assert columns == ["date", "nav", "units", "account_value", "cashflow"]
    assert rows == [(date(2026, 3, 15), 10.0, 10.0, 100.0, 20.0)]


def test_build_seed_state_prefers_persisted_units_over_account_value_divided_by_nav():
    state, nav0 = build_seed_state(nav=10.0, units=95.0, account_value=1000.0)

    assert nav0 == 10.0
    assert state.last_nav == 10.0
    assert state.last_units == 95.0


def test_build_seed_state_falls_back_to_account_value_when_units_missing():
    state, nav0 = build_seed_state(nav=10.0, units=None, account_value=1000.0)

    assert nav0 == 10.0
    assert state.last_nav == 10.0
    assert state.last_units == 100.0
