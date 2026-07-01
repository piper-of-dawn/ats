from __future__ import annotations

import argparse
import re
import sys
from datetime import date
from pathlib import Path

import polars as pl

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ats.dataIO.open_positions import parse_open_positions
from ats.dataIO.supabase_integration import batch_insert
from ats.ticker import EquityTicker, log_returns
from polars_positions_weights import (
    PositionReturn,
    portfolio_gini_coefficient,
    snapshot_realized_correlation,
    yahoo_ticker,
)


OUTPUT_COLUMNS = [
    "date",
    "portfolio_id",
    "ticker",
    "yahoo_ticker",
    "weight",
    "position_return",
    "component_realized_variance",
    "component_realized_volatility",
    "portfolio_return",
    "portfolio_variance",
    "realized_correlation",
    "realized_correlation_percent",
    "portfolio_gini",
    "number_of_assets",
    "methodology",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pdf-dir", default="output")
    parser.add_argument("--limit", type=int, default=None)
    return parser.parse_args()


def statement_date(pdf_path: Path) -> date | None:
    match = re.search(r"ActivityStatement\d+-(\d{4}-\d{2}-\d{2})", pdf_path.name)
    if not match:
        return None
    return date.fromisoformat(match.group(1))


def latest_pdf_per_statement_date(pdf_dir: Path) -> list[tuple[date, Path]]:
    selected: dict[date, Path] = {}
    for pdf_path in sorted(pdf_dir.glob("*.pdf")):
        parsed_date = statement_date(pdf_path)
        if parsed_date is None:
            continue
        current = selected.get(parsed_date)
        if current is None:
            selected[parsed_date] = pdf_path
            continue
        candidate_key = (pdf_path.stat().st_size, pdf_path.name)
        current_key = (current.stat().st_size, current.name)
        if candidate_key > current_key:
            selected[parsed_date] = pdf_path
    return sorted(selected.items())


def positions_frame(pdf_path: Path) -> pl.DataFrame:
    rows = [
        position.to_dict()
        for position in parse_open_positions(pdf_path)
    ]
    if not rows:
        return pl.DataFrame(schema=["ticker", "isin", "currency", "value", "country"])
    return pl.DataFrame(rows).rename(
        {
            "Ticker": "ticker",
            "ISIN": "isin",
            "Currency": "currency",
            "Value": "value",
            "Country": "country",
        }
    )


def load_return_cache(positions_by_date: dict[date, pl.DataFrame]) -> dict[str, dict[date, float]]:
    yahoo_symbols = sorted(
        {
            yahoo_ticker(row["ticker"], row.get("country"))
            for positions in positions_by_date.values()
            for row in positions.iter_rows(named=True)
        }
    )
    cache: dict[str, dict[date, float]] = {}
    for index, symbol in enumerate(yahoo_symbols, start=1):
        try:
            equity_ticker = EquityTicker(symbol)
            if equity_ticker.price_data.is_empty():
                cache[symbol] = {}
                print(f"{index}/{len(yahoo_symbols)} missing {symbol}", flush=True)
                continue
            returns_df = log_returns(equity_ticker.price_data)
            cache[symbol] = {
                row["date"]: float(row["log_return"])
                for row in returns_df.drop_nulls("log_return").iter_rows(named=True)
            }
            print(f"{index}/{len(yahoo_symbols)} cached {symbol}", flush=True)
        except Exception as exc:
            cache[symbol] = {}
            print(f"{index}/{len(yahoo_symbols)} failed {symbol}: {exc}", flush=True)
    return cache


def build_rows_for_date(
    selected_date: date,
    positions: pl.DataFrame,
    return_cache: dict[str, dict[date, float]],
) -> list[tuple]:
    weighted_positions = positions.with_columns(
        pl.col("value").cast(pl.Float64).alias("position_value"),
    ).with_columns(
        (pl.col("position_value") / pl.col("position_value").sum()).alias("weight"),
    )
    position_returns = []
    row_metadata = {}
    for row in weighted_positions.iter_rows(named=True):
        symbol = yahoo_ticker(row["ticker"], row.get("country"))
        position_return = return_cache.get(symbol, {}).get(selected_date)
        if position_return is None:
            continue
        position = PositionReturn(
            ticker=row["ticker"],
            weight=float(row["weight"]),
            position_return=float(position_return),
        )
        position_returns.append(position)
        row_metadata[position.ticker] = symbol

    portfolio_return = sum(
        position.weight * position.position_return
        for position in position_returns
    )
    PositionReturn.portfolio_variance = portfolio_return**2
    rho = snapshot_realized_correlation(position_returns)
    portfolio_gini = portfolio_gini_coefficient(positions)
    number_of_assets = len(position_returns)

    return [
        (
            selected_date,
            None,
            position.ticker,
            row_metadata[position.ticker],
            position.weight,
            position.position_return,
            position.component_realized_variance,
            position.component_realized_volatility,
            portfolio_return,
            PositionReturn.portfolio_variance,
            rho,
            None if rho is None else rho * 100.0,
            portfolio_gini,
            number_of_assets,
            "CBOE-style same-day snapshot realized correlation",
        )
        for position in position_returns
    ]


def main() -> int:
    args = parse_args()
    pdfs = latest_pdf_per_statement_date(Path(args.pdf_dir))
    if args.limit:
        pdfs = pdfs[-args.limit:]

    positions_by_date = {
        selected_date: positions_frame(pdf_path)
        for selected_date, pdf_path in pdfs
    }
    positions_by_date = {
        selected_date: positions
        for selected_date, positions in positions_by_date.items()
        if not positions.is_empty()
    }
    print(f"statement_dates={len(positions_by_date)}", flush=True)

    return_cache = load_return_cache(positions_by_date)

    total_rows = 0
    for selected_date, positions in sorted(positions_by_date.items()):
        rows = build_rows_for_date(selected_date, positions, return_cache)
        batch_insert(
            "portfolio_history",
            OUTPUT_COLUMNS,
            rows,
            conflict_columns=["date", "ticker"],
            overwrite_conflicts=True,
        )
        total_rows += len(rows)
        print(f"{selected_date} inserted_rows={len(rows)}", flush=True)
    print(f"total_inserted_rows={total_rows}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
