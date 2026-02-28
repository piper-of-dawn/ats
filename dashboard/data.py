import sys
from datetime import datetime
from pathlib import Path

from psycopg.errors import UndefinedTable

ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from ats.dataIO.supabase_integration import (
    empty_table_frame,
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

__all__ = ["UndefinedTable", "get_dashboard_context"]
