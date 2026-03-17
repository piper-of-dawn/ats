import argparse
import re
from collections.abc import Iterable
from datetime import date
import os
from pathlib import Path

from psycopg import sql

from ats.dataIO.open_positions import parse_open_positions
from ats.dataIO.statement_table import StatementTable, parse_statement_table
from ats.gmail_downloader import download_pdfs
from ats.dataIO.supabase_integration import (
    _connect,
    batch_insert,
    delete_all_rows,
    fetch_recent_dates,
    get_table_columns,
)


def parse_args():
    parser = argparse.ArgumentParser(
        description="Download Trading 212 Gmail statements and sync fund_nav and positions."
    )
    parser.add_argument(
        "--output-dir",
        default=None,
        help="Optional override for the directory that receives downloaded PDFs.",
    )
    return parser.parse_args()


def get_latest_fund_nav_date() -> str | None:
    dates = fetch_recent_dates("fund_nav", limit=1)
    return dates[0] if dates else None


def run_gmail_downloader(after_date: str | None) -> None:
    download_pdfs(after_date)


def get_output_dir(cli_output_dir: str | None = None) -> Path:
    if cli_output_dir:
        return Path(cli_output_dir).expanduser()
    output_dir = Path(os.getenv("OUTPUT_DIR", "./output"))
    return output_dir.expanduser()


def extract_pdf_date(pdf_path: Path) -> date | None:
    match = re.search(r"(\d{4}-\d{2}-\d{2})", pdf_path.name)
    if not match:
        return None
    return date.fromisoformat(match.group(1))


def get_candidate_pdfs(output_dir: Path, after_date: str | None) -> list[Path]:
    cutoff = date.fromisoformat(after_date) if after_date else None
    candidates = []
    for pdf_path in sorted(output_dir.glob("*.pdf")):
        pdf_date = extract_pdf_date(pdf_path)
        if pdf_date is None:
            continue
        if cutoff and pdf_date <= cutoff:
            continue
        candidates.append(pdf_path)
    return candidates


def build_account_status_rows(
    statements: Iterable[StatementTable],
) -> list[dict[str, date | str | float | None]]:
    rows = []
    for statement in statements:
        if statement.account_id is None:
            raise ValueError("Statement table row is missing account_id.")
        rows.append(statement.to_dict())
    rows.sort(key=lambda row: (row["date"], row["account_id"]))
    return rows


def insert_account_status_row(
    table_name: str, columns: list[str], row_data: dict[str, date | str | float | None]
) -> None:
    row = tuple(row_data[column] for column in columns)
    assignments = sql.SQL(", ").join(
        sql.SQL("{} = EXCLUDED.{}").format(
            sql.Identifier(column),
            sql.Identifier(column),
        )
        for column in columns
        if column not in {"date", "account_id"}
    )
    query = sql.SQL(
        "INSERT INTO {} ({}) VALUES ({}) "
        "ON CONFLICT ({}, {}) DO UPDATE SET {}"
    ).format(
        sql.Identifier(table_name),
        sql.SQL(", ").join(sql.Identifier(column) for column in columns),
        sql.SQL(", ").join(sql.Placeholder() for _ in columns),
        sql.Identifier("date"),
        sql.Identifier("account_id"),
        assignments,
    )
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(query, row)
        conn.commit()


def sync_account_status(statements: Iterable[StatementTable]) -> int:
    rows = build_account_status_rows(statements)
    if not rows:
        print("No trading212_daily_account rows parsed from statement PDFs.")
        return 0

    columns = get_table_columns("trading212_daily_account")
    row_columns = [column for column in columns if column in rows[0]]
    required_columns = {"date", "account_id"}
    if not required_columns.issubset(row_columns):
        raise ValueError(
            "trading212_daily_account must contain at least 'date' and 'account_id' columns."
        )

    for row_data in rows:
        insert_account_status_row("trading212_daily_account", row_columns, row_data)

    print(f"Inserted {len(rows)} trading212_daily_account rows.")
    return len(rows)


def build_positions_rows(latest_pdf: Path) -> tuple[list[str], list[tuple]]:
    positions = parse_open_positions(latest_pdf)
    if not positions:
        print(f"No open positions parsed from {latest_pdf.name}.")
        return [], []

    columns = get_table_columns("positions")
    by_lower = {column.lower(): column for column in columns}
    latest_pdf_date = extract_pdf_date(latest_pdf)

    selected_columns = []
    for candidate in ("ticker", "isin", "currency", "value", "country"):
        resolved = by_lower.get(candidate)
        if resolved:
            selected_columns.append(resolved)

    date_column = by_lower.get("date") or by_lower.get("as_of_date")
    if date_column:
        selected_columns.append(date_column)

    if not selected_columns:
        raise ValueError("positions table does not contain any supported target columns.")

    rows = []
    for position in positions:
        position_data = {key.lower(): value for key, value in position.to_dict().items()}
        row = []
        for column in selected_columns:
            lower_column = column.lower()
            if lower_column in {"date", "as_of_date"}:
                row.append(latest_pdf_date)
            else:
                row.append(position_data[lower_column])
        rows.append(tuple(row))
    return selected_columns, rows


def sync_positions(latest_pdf: Path) -> int:
    columns, rows = build_positions_rows(latest_pdf)
    if not rows:
        return 0

    delete_all_rows("positions")
    batch_insert("positions", columns, rows)
    print(f"Replaced positions table with {len(rows)} rows from {latest_pdf.name}.")
    return len(rows)


def run_sync(cli_output_dir: str | None = None) -> int:
    latest_date = get_latest_fund_nav_date()
    print(f"Latest fund_nav date: {latest_date or 'none'}")

    run_gmail_downloader(latest_date)

    output_dir = get_output_dir(cli_output_dir)
    candidate_pdfs = get_candidate_pdfs(output_dir, latest_date)
    if not candidate_pdfs:
        print("No new PDFs found after download.")
        return 0

    statement_tables = []
    for pdf_path in candidate_pdfs:
        table = parse_statement_table(pdf_path)
        if table is not None:
            statement_tables.append(table)

    sync_account_status(statement_tables)
    sync_positions(candidate_pdfs[-1])
    return 0


def main() -> int:
    args = parse_args()
    return run_sync(args.output_dir)


if __name__ == "__main__":
    raise SystemExit(main())
