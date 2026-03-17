import argparse

import polars as pl
from psycopg import sql

from ats.dataIO.supabase_integration import _connect, fetch_recent_dates, get_table_columns
from ats.fund_nav import FundState, compute_nav_incremental


def parse_args():
    parser = argparse.ArgumentParser(
        description=(
            "Compute and upsert missing fund_nav rows from trading212_daily_account."
        )
    )
    return parser.parse_args()


def get_latest_fund_nav_date() -> str | None:
    dates = fetch_recent_dates("fund_nav", limit=1)
    return dates[0] if dates else None


def fetch_incremental_source_rows(after_date: str | None) -> pl.DataFrame:
    account_columns = get_table_columns("trading212_daily_account")
    by_lower = {column.lower(): column for column in account_columns}
    date_column = by_lower.get("date")
    account_value_column = by_lower.get("account_value")
    deposits_column = by_lower.get("deposits")
    withdrawals_column = by_lower.get("withdrawals")

    if date_column is None or account_value_column is None:
        raise ValueError(
            "trading212_daily_account must contain at least 'date' and 'account_value' columns."
        )

    query = sql.SQL(
        """
        select
            cast({date_column} as date) as date,
            {account_value_column} as account_value,
            {deposits_column} as deposits,
            {withdrawals_column} as withdrawals
        from {table_name}
        where {account_value_column} is not null
        """
    ).format(
        date_column=sql.Identifier(date_column),
        account_value_column=sql.Identifier(account_value_column),
        deposits_column=sql.Identifier(deposits_column) if deposits_column else sql.SQL("NULL"),
        withdrawals_column=sql.Identifier(withdrawals_column)
        if withdrawals_column
        else sql.SQL("NULL"),
        table_name=sql.Identifier("trading212_daily_account"),
    )

    params: tuple[object, ...] = ()
    if after_date:
        query += sql.SQL(" and cast({} as date) > %s").format(sql.Identifier(date_column))
        params = (after_date,)
    query += sql.SQL(" order by date asc")

    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(query, params)
            rows = cur.fetchall()

    return normalize_source_rows(
        pl.DataFrame(
            rows,
            schema=["date", "account_value", "deposits", "withdrawals"],
            orient="row",
        )
    )


def normalize_source_rows(df: pl.DataFrame) -> pl.DataFrame:
    if df.is_empty():
        return pl.DataFrame(
            schema={
                "date": pl.Date,
                "account_value": pl.Float64,
                "cashflow": pl.Float64,
            }
        )

    normalized = (
        df.with_columns(
            pl.col("account_value").cast(pl.Float64, strict=False),
            pl.col("deposits").cast(pl.Float64, strict=False).fill_null(0.0),
            pl.col("withdrawals").cast(pl.Float64, strict=False).fill_null(0.0),
        )
        .with_columns((pl.col("deposits") - pl.col("withdrawals")).alias("cashflow"))
        .sort("date")
        .group_by("date", maintain_order=True)
        .agg(
            pl.col("account_value").last().alias("account_value"),
            pl.col("cashflow").last().alias("cashflow"),
        )
        .sort("date")
    )
    return normalized.select("date", "account_value", "cashflow")


def fetch_latest_fund_nav_seed() -> tuple[FundState, float]:
    query = """
        select
            f.date,
            f.nav,
            t.account_value
        from fund_nav f
        left join (
            select distinct on (cast(date as date))
                cast(date as date) as date,
                account_value
            from trading212_daily_account
            where account_value is not null
            order by cast(date as date) desc
        ) t on t.date = f.date
        order by f.date desc
        limit 1
    """
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(query)
            row = cur.fetchone()

    if row is None:
        return FundState(), 10.0

    _, nav, account_value = row
    if nav is None:
        return FundState(), 10.0
    if account_value is None:
        raise ValueError(
            "Latest fund_nav row has no matching trading212_daily_account.account_value to seed units."
        )

    nav_value = float(nav)
    account_value_float = float(account_value)
    if nav_value == 0:
        raise ValueError("Latest fund_nav row has nav=0, cannot seed incremental units.")

    return FundState(last_units=account_value_float / nav_value, last_nav=nav_value), nav_value


def compute_missing_fund_nav_rows(after_date: str | None) -> pl.DataFrame:
    source_df = fetch_incremental_source_rows(after_date)
    if source_df.is_empty():
        return pl.DataFrame(schema={"date": pl.Date, "nav": pl.Float64, "units": pl.Float64})

    state, nav0 = fetch_latest_fund_nav_seed()
    result = compute_nav_incremental(source_df, nav0=nav0, state=state)
    return result.rename({"NAV": "nav"}).select("date", "nav", "units", "account_value", "cashflow")


def build_insert_rows(df: pl.DataFrame, fund_nav_columns: list[str]) -> tuple[list[str], list[tuple]]:
    by_lower = {column.lower(): column for column in fund_nav_columns}
    selected_columns: list[str] = []
    for candidate in ("date", "nav", "units", "account_value", "cashflow"):
        resolved = by_lower.get(candidate)
        if resolved:
            selected_columns.append(resolved)

    required = {by_lower.get("date"), by_lower.get("nav")}
    if None in required:
        raise ValueError("fund_nav must contain at least 'date' and 'nav' columns.")

    rows: list[tuple] = []
    for row in df.iter_rows(named=True):
        row_values = []
        for column in selected_columns:
            row_values.append(row[column.lower()])
        rows.append(tuple(row_values))
    return selected_columns, rows


def upsert_fund_nav_rows(df: pl.DataFrame) -> int:
    if df.is_empty():
        return 0

    columns = get_table_columns("fund_nav")
    insert_columns, rows = build_insert_rows(df, columns)
    if not rows:
        return 0

    conflict_columns = ["date"]
    update_columns = [column for column in insert_columns if column.lower() not in {"date"}]
    query = sql.SQL(
        "INSERT INTO {} ({}) VALUES ({}) ON CONFLICT ({}) DO UPDATE SET {}"
    ).format(
        sql.Identifier("fund_nav"),
        sql.SQL(", ").join(sql.Identifier(column) for column in insert_columns),
        sql.SQL(", ").join(sql.Placeholder() for _ in insert_columns),
        sql.SQL(", ").join(sql.Identifier(column) for column in conflict_columns),
        sql.SQL(", ").join(
            sql.SQL("{} = EXCLUDED.{}").format(
                sql.Identifier(column),
                sql.Identifier(column),
            )
            for column in update_columns
        ),
    )

    with _connect() as conn:
        with conn.cursor() as cur:
            cur.executemany(query, rows)
        conn.commit()
    return len(rows)


def run() -> int:
    parse_args()
    latest_date = get_latest_fund_nav_date()
    print(f"Latest fund_nav date: {latest_date or 'none'}")

    computed_df = compute_missing_fund_nav_rows(latest_date)
    if computed_df.is_empty():
        print("No newer trading212_daily_account rows found for fund_nav.")
        return 0

    inserted = upsert_fund_nav_rows(computed_df)
    print(f"Upserted {inserted} fund_nav rows.")
    return 0


def main() -> int:
    return run()


if __name__ == "__main__":
    raise SystemExit(main())
