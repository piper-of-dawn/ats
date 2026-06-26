from __future__ import annotations

import argparse
from datetime import date

import polars as pl

from ats.dataIO.supabase_integration import (
    add_columns_if_missing,
    batch_insert_polars_df,
    fetch_table,
)
from ats.derivatives.options_risk_premium import calculate_option_risk_premium

SOURCE_TABLES = {
    "largecap": "us_largecap",
    "us_largecap": "us_largecap",
    "midcap": "us_midcap",
    "us_midcap": "us_midcap",
}
TARGET_TABLES = {
    "us_largecap": "us_largecap_metrics",
    "us_midcap": "us_midcap_metrics",
}
OPTION_PREMIUM_COLUMN = "option_implied_risk_premium"
OUTPUT_COLUMNS = ["ticker", "as_of_date", OPTION_PREMIUM_COLUMN]


def resolve_source_table(table: str) -> str:
    try:
        return SOURCE_TABLES[table.strip().lower()]
    except KeyError as exc:
        raise ValueError("table must be one of: largecap, midcap, us_largecap, us_midcap") from exc


def resolve_target_table(source_table: str) -> str:
    try:
        return TARGET_TABLES[source_table]
    except KeyError as exc:
        raise ValueError(f"No target metrics table configured for {source_table}") from exc


def source_tickers(source_table: str, limit: int | None = None) -> list[str]:
    df = fetch_table(source_table, columns=["yahoo_finance_ticker"])
    if df.is_empty() or "yahoo_finance_ticker" not in df.columns:
        return []
    tickers = [
        str(ticker).strip().upper()
        for ticker in df["yahoo_finance_ticker"].drop_nulls()
        if str(ticker).strip()
    ]
    return tickers[:limit] if limit else tickers


def empty_option_risk_premium_row(ticker: str, as_of_date: date) -> dict:
    return {
        "ticker": ticker,
        "as_of_date": as_of_date,
        OPTION_PREMIUM_COLUMN: None,
    }


def compute_option_risk_premium_row(ticker: str, as_of_date: date) -> dict:
    result = calculate_option_risk_premium(ticker, as_of_date=as_of_date)
    return {
        "ticker": ticker,
        "as_of_date": as_of_date,
        OPTION_PREMIUM_COLUMN: float(result.implied_risk_premium),
    }


def build_option_risk_premium_frame(rows: list[dict]) -> pl.DataFrame:
    if not rows:
        return pl.DataFrame(schema=OUTPUT_COLUMNS)
    return (
        pl.DataFrame(rows)
        .with_columns(pl.col(OPTION_PREMIUM_COLUMN).cast(pl.Float64, strict=False))
        .select(OUTPUT_COLUMNS)
        .sort("ticker")
    )


def write_option_risk_premiums_to_database(df: pl.DataFrame, target_table: str) -> None:
    add_columns_if_missing(target_table, {OPTION_PREMIUM_COLUMN: "double precision"})
    batch_insert_polars_df(
        df,
        OUTPUT_COLUMNS,
        target_table,
        conflict_columns=["ticker", "as_of_date"],
        overwrite_conflicts=True,
    )


def update_option_risk_premiums(
    table: str,
    *,
    limit: int | None = None,
    as_of_date: date | None = None,
) -> pl.DataFrame:
    source_table = resolve_source_table(table)
    target_table = resolve_target_table(source_table)
    run_date = as_of_date or date.today()

    rows = []
    for ticker in source_tickers(source_table, limit=limit):
        try:
            row = compute_option_risk_premium_row(ticker, run_date)
            print(f"{ticker}: {row[OPTION_PREMIUM_COLUMN]:.6f}")
        except Exception as exc:
            print(f"{ticker}: failed: {exc}")
            row = empty_option_risk_premium_row(ticker, run_date)
        rows.append(row)

    df = build_option_risk_premium_frame(rows)
    write_option_risk_premiums_to_database(df, target_table)
    print(f"inserted {df.height} rows into {target_table}")
    return df


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Compute option implied risk premium and write it to a metrics table."
    )
    parser.add_argument("table", help="largecap, midcap, us_largecap, or us_midcap")
    parser.add_argument("--limit", type=int, help="Optional ticker limit for smoke runs")
    args = parser.parse_args()
    update_option_risk_premiums(args.table, limit=args.limit)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
