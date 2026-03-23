import sys
from datetime import date, datetime
from pathlib import Path

import polars as pl
from psycopg.errors import UndefinedTable

ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from ats.dataIO.supabase_integration import (
    empty_table_frame,
    fetch_table,
    fetch_recent_dates,
    fetch_top_rows_for_date,
)


def get_dashboard_context(table_name: str) -> dict:
    available_dates = fetch_recent_dates(table_name, limit=7)
    selected_date = available_dates[0] if available_dates else None
    rows_df = fetch_rows_for_selected_date(table_name, selected_date)
    columns = list(rows_df.columns)
    mobile_primary_columns, mobile_detail_columns = split_mobile_columns(columns)
    return {
        "table_name": table_name,
        "columns": columns,
        "rows": rows_df.to_dicts(),
        "mobile_primary_columns": mobile_primary_columns,
        "mobile_detail_columns": mobile_detail_columns,
        "as_of_date": selected_date,
        "updated_at": datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),
    }


def get_nav_context() -> dict | None:
    nav_df = fetch_table("fund_nav")
    if nav_df.is_empty():
        return None

    date_column = _find_column_name(nav_df.columns, "date")
    nav_column = _find_column_name(nav_df.columns, "nav")
    normalized_df = (
        nav_df.select(
            pl.col(date_column).alias("date"),
            pl.col(nav_column).cast(pl.Float64, strict=False).alias("nav"),
        )
        .drop_nulls()
        .sort("date")
    )
    if normalized_df.is_empty():
        return None

    dates = normalized_df["date"].to_list()
    nav_values = normalized_df["nav"].to_list()
    latest_nav = nav_values[-1]
    previous_nav = nav_values[-2] if len(nav_values) > 1 else latest_nav
    day_change = latest_nav - previous_nav
    day_change_pct = (day_change / previous_nav) if previous_nav else 0.0

    max_drawdown = _compute_max_drawdown(nav_values)
    max_upside = _compute_max_upside(nav_values)
    chart_points = [
        {
            "label": _format_chart_label(value),
            "raw_date": _format_raw_date(value),
            "y": round(float(nav), 4),
        }
        for value, nav in zip(dates, nav_values, strict=False)
    ]

    return {
        "latest_nav": _format_currency(latest_nav),
        "previous_nav": _format_currency(previous_nav),
        "day_change": _format_signed_currency(day_change),
        "day_change_pct": _format_signed_percent(day_change_pct),
        "change_positive": day_change >= 0,
        "latest_date": _format_header_date(dates[-1]),
        "previous_date": _format_header_date(dates[-2] if len(dates) > 1 else dates[-1]),
        "reference_nav": _format_currency(previous_nav),
        "max_drawdown": _format_signed_percent(max_drawdown),
        "max_upside": _format_signed_percent(max_upside),
        "chart_points": chart_points,
    }


def fetch_rows_for_selected_date(table_name: str, selected_date: str | None):
    if selected_date is None:
        return empty_table_frame(table_name)
    return fetch_top_rows_for_date(table_name, selected_date, limit=10)


def split_mobile_columns(columns: list[str]) -> tuple[list[str], list[str]]:
    primary_columns = []
    for candidate in ("ticker", "yahoo_finance_ticker", "ltm", "stm"):
        if candidate in columns and candidate not in primary_columns:
            primary_columns.append(candidate)
    if not primary_columns:
        primary_columns = columns[:3]
    detail_columns = [column for column in columns if column not in primary_columns]
    return primary_columns, detail_columns


def _find_column_name(columns: list[str], target: str) -> str:
    by_lower = {column.lower(): column for column in columns}
    if target not in by_lower:
        raise KeyError(f"Expected column '{target}' in fund_nav table.")
    return by_lower[target]


def _compute_max_drawdown(nav_values: list[float]) -> float:
    peak = nav_values[0]
    max_drawdown = 0.0
    for nav in nav_values:
        peak = max(peak, nav)
        if peak:
            max_drawdown = min(max_drawdown, (nav / peak) - 1)
    return max_drawdown


def _compute_max_upside(nav_values: list[float]) -> float:
    trough = nav_values[0]
    max_upside = 0.0
    for nav in nav_values:
        trough = min(trough, nav)
        if trough:
            max_upside = max(max_upside, (nav / trough) - 1)
    return max_upside


def _format_chart_label(value: date | datetime | str) -> str:
    if isinstance(value, datetime):
        return value.strftime("%b %-d, %Y")
    if isinstance(value, date):
        return value.strftime("%b %-d, %Y")
    return str(value)


def _format_raw_date(value: date | datetime | str) -> str:
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d")
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def _format_header_date(value: date | datetime | str) -> str:
    if isinstance(value, datetime):
        return value.strftime("%b %-d, %Y")
    if isinstance(value, date):
        return value.strftime("%b %-d, %Y")
    return str(value)


def _format_currency(value: float) -> str:
    return f"${value:,.2f}"


def _format_signed_currency(value: float) -> str:
    sign = "+" if value >= 0 else "-"
    return f"{sign}${abs(value):,.2f}"


def _format_signed_percent(value: float) -> str:
    sign = "+" if value >= 0 else "-"
    return f"{sign}{abs(value) * 100:,.2f}%"

__all__ = ["UndefinedTable", "get_dashboard_context", "get_nav_context"]
