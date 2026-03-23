from dataclasses import dataclass

import polars as pl


@dataclass
class FundState:
    last_units: float | None = None
    last_nav: float | None = None


def nav_step(
    account_value: float,
    cashflow: float | None,
    state: FundState,
    nav0: float = 10.0,
) -> tuple[float, float]:
    cashflow = 0.0 if cashflow is None else float(cashflow)
    account_value = float(account_value)

    if state.last_units is None:
        nav = nav0
        units_pre = (account_value - cashflow) / nav
        units = units_pre + cashflow / nav
    else:
        nav = (account_value - cashflow) / state.last_units
        units = state.last_units + cashflow / nav

    state.last_nav = nav
    state.last_units = units
    return nav, units


def compute_nav_incremental(
    df: pl.DataFrame,
    nav0: float = 10.0,
    state: FundState | None = None,
) -> pl.DataFrame:
    df = df.sort("date")

    state = state or FundState()
    navs: list[float] = []
    units_list: list[float] = []

    for row in df.iter_rows(named=True):
        nav, units = nav_step(
            account_value=row["account_value"],
            cashflow=row["cashflow"],
            state=state,
            nav0=nav0,
        )
        navs.append(nav)
        units_list.append(units)

    return df.with_columns(
        pl.Series("NAV", navs),
        pl.Series("units", units_list),
    )
