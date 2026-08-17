import math
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
    fetch_recent_dates,
    fetch_rows_for_date,
    fetch_table,
    get_date_column_name,
    get_table_columns,
)

_DASHBOARD_PRIORITY_COLUMNS = (
    "ticker",
    "yahoo_finance_ticker",
    "combined_score",
    "ltm",
    "stm",
    "beta",
    "analyst_rating",
    "rating",
    "analyst_price_target_deviation",
    "representative_index_ticker",
)

_TREEMAP_DIMENSIONS = {
    "country": "Country Exposure",
    "currency": "Currency Exposure",
}

_TABLE_TITLES = {
    "us_largecap_metrics": "US Large Cap Highlights",
    "us_midcap_metrics": "US Midcap Highlights",
}

_COLUMN_LABELS = {
    "momentum_rank": "Momentum Rank",
    "combined_score": "Combined Score",
    "analyst_rating": "Analyst Rating",
    "analyst_price_target_deviation": "Price Target Dev",
}

_MOMENTUM_RANK_COLUMN = "momentum_rank"
_DEFAULT_MOMENTUM_STRATEGY = "momentum_first"
_MOMENTUM_STRATEGIES = (
    {
        "key": "momentum_first",
        "label": "Momentum First",
        "description": "LTM quintile first; within each quintile: 50% LTM, 35% STM, 15% analyst rating.",
        "weights": {"ltm": 0.50, "stm": 0.35, "analyst_rating": 0.15},
    },
    {
        "key": "momentum_balance",
        "label": "Momentum Balance",
        "description": "LTM quintile first; within each quintile: 45% LTM, 45% STM, 10% analyst rating.",
        "weights": {"ltm": 0.45, "stm": 0.45, "analyst_rating": 0.10},
    },
    {
        "key": "analyst_confirmed",
        "label": "Analyst Confirmed",
        "description": "LTM quintile first; within each quintile: 45% LTM, 30% STM, 25% analyst rating.",
        "weights": {"ltm": 0.45, "stm": 0.30, "analyst_rating": 0.25},
    },
    {
        "key": "strict_momentum_sequence",
        "label": "Strict Momentum Sequence",
        "description": "Exact LTM first, then STM, analyst rating, and ticker.",
        "weights": None,
    },
)

def get_dashboard_context(table_name: str) -> dict:
    columns = get_dashboard_columns(table_name)
    available_dates = fetch_recent_dates(table_name, limit=7)
    selected_date = available_dates[0] if available_dates else None
    rows_df = fetch_rows_for_selected_date(table_name, selected_date, columns)
    columns = list(rows_df.columns)
    rows = rows_df.to_dicts()
    ranking_enabled = add_momentum_rankings(rows, columns)
    if ranking_enabled:
        rows.sort(
            key=lambda row: (
                row[_MOMENTUM_RANK_COLUMN] is None,
                row[_MOMENTUM_RANK_COLUMN] or 0,
            )
        )
        columns = _insert_momentum_rank_column(columns)
    mobile_primary_columns, mobile_detail_columns = split_mobile_columns(columns)
    return {
        "table_name": table_name,
        "display_title": _TABLE_TITLES.get(
            table_name, table_name.replace("_", " ").title()
        ),
        "columns": columns,
        "column_labels": {
            column: _COLUMN_LABELS.get(column, column.replace("_", " ").upper())
            for column in columns
        },
        "rows": rows,
        "mobile_primary_columns": mobile_primary_columns,
        "mobile_detail_columns": mobile_detail_columns,
        "as_of_date": selected_date,
        "updated_at": datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),
        "ranking_enabled": ranking_enabled,
        "ranking_strategies": _MOMENTUM_STRATEGIES if ranking_enabled else (),
        "default_ranking_strategy": _DEFAULT_MOMENTUM_STRATEGY,
    }


def _find_column(columns: list[str], *candidates: str) -> str | None:
    by_lower = {column.lower(): column for column in columns}
    return next((by_lower[candidate] for candidate in candidates if candidate in by_lower), None)


def _as_finite_float(value) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    return numeric if math.isfinite(numeric) else None


def _percentile_ranks(values: dict[int, float]) -> dict[int, float]:
    """Return ascending average-rank percentiles, so the largest value is best."""
    if not values:
        return {}

    ordered = sorted(values.items(), key=lambda item: item[1])
    percentiles: dict[int, float] = {}
    cursor = 0
    total = len(ordered)
    while cursor < total:
        group_end = cursor + 1
        while group_end < total and ordered[group_end][1] == ordered[cursor][1]:
            group_end += 1
        average_rank = ((cursor + 1) + group_end) / 2
        percentile = average_rank / total
        for row_index, _ in ordered[cursor:group_end]:
            percentiles[row_index] = percentile
        cursor = group_end
    return percentiles


def _ticker_sort_value(row: dict, ticker_column: str | None) -> str:
    if ticker_column is None:
        return ""
    value = row.get(ticker_column)
    return "" if value is None else str(value).casefold()


def add_momentum_rankings(rows: list[dict], columns: list[str]) -> bool:
    """Attach presentation-only rank metadata when all required factor columns exist."""
    ltm_column = _find_column(columns, "ltm")
    stm_column = _find_column(columns, "stm")
    analyst_column = _find_column(columns, "analyst_rating", "rating")
    if not ltm_column or not stm_column or not analyst_column:
        return False

    ticker_column = _find_column(columns, "ticker", "yahoo_finance_ticker")
    metrics: dict[int, dict[str, float]] = {}
    for row_index, row in enumerate(rows):
        values = {
            "ltm": _as_finite_float(row.get(ltm_column)),
            "stm": _as_finite_float(row.get(stm_column)),
            "analyst_rating": _as_finite_float(row.get(analyst_column)),
        }
        row["_momentum_ranks"] = {
            strategy["key"]: None for strategy in _MOMENTUM_STRATEGIES
        }
        row["_ltm_top_quintile"] = False
        row[_MOMENTUM_RANK_COLUMN] = None
        if all(value is not None for value in values.values()):
            metrics[row_index] = {
                metric: float(value) for metric, value in values.items()
            }

    percentiles = {
        metric: _percentile_ranks(
            {row_index: values[metric] for row_index, values in metrics.items()}
        )
        for metric in ("ltm", "stm", "analyst_rating")
    }
    quintiles = {
        row_index: min(5, max(1, math.ceil(percentiles["ltm"][row_index] * 5)))
        for row_index in metrics
    }
    for row_index, quintile in quintiles.items():
        rows[row_index]["_ltm_top_quintile"] = quintile == 5

    def exact_tiebreaker(row_index: int):
        values = metrics[row_index]
        return (
            -values["ltm"],
            -values["stm"],
            -values["analyst_rating"],
            _ticker_sort_value(rows[row_index], ticker_column),
            row_index,
        )

    for strategy in _MOMENTUM_STRATEGIES:
        if strategy["weights"] is None:
            ordered_indices = sorted(metrics, key=exact_tiebreaker)
        else:
            weights = strategy["weights"]

            def strategy_key(row_index: int):
                score = sum(
                    weights[metric] * percentiles[metric][row_index]
                    for metric in weights
                )
                return (-quintiles[row_index], -score, *exact_tiebreaker(row_index))

            ordered_indices = sorted(metrics, key=strategy_key)

        for rank, row_index in enumerate(ordered_indices, start=1):
            rows[row_index]["_momentum_ranks"][strategy["key"]] = rank

    for row in rows:
        row[_MOMENTUM_RANK_COLUMN] = row["_momentum_ranks"][_DEFAULT_MOMENTUM_STRATEGY]
    return True


def _insert_momentum_rank_column(columns: list[str]) -> list[str]:
    ranked_columns = list(columns)
    ticker_column = _find_column(ranked_columns, "ticker", "yahoo_finance_ticker")
    insert_at = ranked_columns.index(ticker_column) + 1 if ticker_column else 0
    ranked_columns.insert(insert_at, _MOMENTUM_RANK_COLUMN)
    return ranked_columns


def get_positions_treemap_context(group_by: str) -> dict:
    normalized_group = group_by.lower()
    if normalized_group not in _TREEMAP_DIMENSIONS:
        raise KeyError(f"Unsupported positions treemap dimension: {group_by}")

    positions_df = fetch_table("positions", columns=[normalized_group, "value"])
    if positions_df.is_empty():
        return {
            "group_by": normalized_group,
            "title": _TREEMAP_DIMENSIONS[normalized_group],
            "items": [],
            "total_value": _format_currency(0.0),
            "position_count": 0,
            "updated_at": datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),
        }

    label_column = _find_column_name(positions_df.columns, normalized_group)
    value_column = _find_column_name(positions_df.columns, "value")

    exposure_df = (
        positions_df.select(
            pl.col(label_column).cast(pl.Utf8, strict=False).alias("label"),
            pl.col(value_column).cast(pl.Float64, strict=False).alias("value"),
        )
        .with_columns(pl.col("label").str.strip_chars().alias("label"))
        .filter(
            pl.col("label").is_not_null()
            & (pl.col("label") != "")
            & pl.col("value").is_not_null()
        )
        .group_by("label")
        .agg(pl.col("value").sum().alias("value"))
        .sort("value", descending=True)
    )

    total_value = (
        float(exposure_df["value"].sum()) if not exposure_df.is_empty() else 0.0
    )
    items = [
        {
            "label": row["label"],
            "value": round(float(row["value"]), 2),
            "formatted_value": _format_currency(float(row["value"])),
            "share_pct": _format_percent(
                (float(row["value"]) / total_value) if total_value else 0.0
            ),
        }
        for row in exposure_df.to_dicts()
    ]

    return {
        "group_by": normalized_group,
        "title": _TREEMAP_DIMENSIONS[normalized_group],
        "items": items,
        "total_value": _format_currency(total_value),
        "position_count": positions_df.height,
        "updated_at": datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),
    }


def get_nav_context() -> dict | None:
    nav_df = fetch_table("fund_nav", columns=["date", "nav"])
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
        "previous_date": _format_header_date(
            dates[-2] if len(dates) > 1 else dates[-1]
        ),
        "reference_nav": _format_currency(previous_nav),
        "max_drawdown": _format_signed_percent(max_drawdown),
        "max_upside": _format_signed_percent(max_upside),
        "chart_points": chart_points,
    }


def get_portfolio_history_context() -> dict | None:
    history_df = fetch_table(
        "portfolio_history",
        columns=[
            "date",
            "realized_correlation_percent",
            "portfolio_gini",
            "number_of_assets",
        ],
    )
    if history_df.is_empty():
        return None

    date_column = _find_column_name(history_df.columns, "date")
    correlation_column = _find_column_name(
        history_df.columns,
        "realized_correlation_percent",
    )
    gini_column = _find_column_name(history_df.columns, "portfolio_gini")
    assets_column = _find_column_name(history_df.columns, "number_of_assets")

    normalized_df = (
        history_df.select(
            pl.col(date_column).cast(pl.Date, strict=False).alias("date"),
            pl.col(correlation_column)
            .cast(pl.Float64, strict=False)
            .alias("correlation_percent"),
            pl.col(gini_column).cast(pl.Float64, strict=False).alias("gini"),
            pl.col(assets_column).cast(pl.Int64, strict=False).alias("number_of_assets"),
        )
        .drop_nulls(["date", "correlation_percent", "gini"])
        .group_by("date")
        .agg(
            pl.col("correlation_percent").first(),
            pl.col("gini").first(),
            pl.col("number_of_assets").max(),
        )
        .sort("date")
    )
    if normalized_df.is_empty():
        return None

    chart_points = [
        {
            "label": _format_chart_label(row["date"]),
            "raw_date": _format_raw_date(row["date"]),
            "correlation": round(float(row["correlation_percent"]), 6),
            "gini": round(float(row["gini"]), 6),
            "number_of_assets": int(row["number_of_assets"]),
        }
        for row in normalized_df.to_dicts()
    ]
    latest = normalized_df.row(-1, named=True)
    return {
        "latest_date": _format_header_date(latest["date"]),
        "latest_correlation": f"{float(latest['correlation_percent']):,.2f}%",
        "latest_gini": f"{float(latest['gini']):,.4f}",
        "number_of_assets": int(latest["number_of_assets"]),
        "chart_points": chart_points,
        "updated_at": datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),
    }


def get_dashboard_columns(table_name: str) -> list[str]:
    available_columns = get_table_columns(table_name)
    by_lower = {column.lower(): column for column in available_columns}

    selected_columns = [
        by_lower[column] for column in _DASHBOARD_PRIORITY_COLUMNS if column in by_lower
    ]

    date_column = by_lower.get(get_date_column_name(table_name).lower())
    if date_column and date_column not in selected_columns:
        selected_columns.append(date_column)

    if selected_columns:
        return selected_columns

    return available_columns[:6]


def fetch_rows_for_selected_date(
    table_name: str, selected_date: str | None, columns: list[str]
):
    if selected_date is None:
        return empty_table_frame(table_name, columns=columns)
    return fetch_rows_for_date(table_name, selected_date, columns=columns)


def split_mobile_columns(columns: list[str]) -> tuple[list[str], list[str]]:
    identifiers = (
        ("ticker", "momentum_rank", "yahoo_finance_ticker")
        if "ticker" in columns
        else ("yahoo_finance_ticker", "momentum_rank")
    )
    primary_order = (
        *identifiers,
        "combined_score",
        "ltm",
        "stm",
        "analyst_rating",
        "rating",
        "analyst_price_target_deviation",
        "beta",
    )
    primary_columns = [c for c in primary_order if c in columns]
    for column in columns:
        if column not in primary_columns:
            primary_columns.append(column)
    return primary_columns, []


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
        return _format_display_date(value)
    if isinstance(value, date):
        return _format_display_date(value)
    return str(value)


def _format_raw_date(value: date | datetime | str) -> str:
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d")
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def _format_header_date(value: date | datetime | str) -> str:
    if isinstance(value, datetime):
        return _format_display_date(value)
    if isinstance(value, date):
        return _format_display_date(value)
    return str(value)


def _format_display_date(value: date | datetime) -> str:
    return f"{value.strftime('%b')} {value.day}, {value.year}"


def _format_currency(value: float) -> str:
    return f"€{value:,.2f}"


def _format_signed_currency(value: float) -> str:
    sign = "+" if value >= 0 else "-"
    return f"{sign}€{abs(value):,.2f}"


def _format_signed_percent(value: float) -> str:
    sign = "+" if value >= 0 else "-"
    return f"{sign}{abs(value) * 100:,.2f}%"


def _format_percent(value: float) -> str:
    return f"{value * 100:,.2f}%"


__all__ = [
    "UndefinedTable",
    "get_dashboard_context",
    "get_nav_context",
    "get_portfolio_history_context",
    "get_positions_treemap_context",
]
