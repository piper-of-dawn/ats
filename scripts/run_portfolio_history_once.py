from __future__ import annotations

import argparse
import sys
from contextlib import redirect_stderr, redirect_stdout
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ats.dataIO.supabase_integration import batch_insert, fetch_table


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", required=True)
    parser.add_argument("--log-file", required=True)
    return parser.parse_args()


def run(selected_date: date) -> int:
    positions_df = fetch_table("positions")

    import polars_positions_weights as helper

    print(f"date={selected_date}", flush=True)
    print(f"loaded_positions={positions_df.height}", flush=True)

    position_returns = helper.build_position_returns(positions_df, selected_date)
    rho = helper.snapshot_realized_correlation(position_returns)
    portfolio_gini = helper.portfolio_gini_coefficient(positions_df)
    portfolio_return = sum(
        position.weight * position.position_return for position in position_returns
    )
    portfolio_variance = portfolio_return**2
    number_of_assets = len(position_returns)

    print(f"valid_assets={number_of_assets}", flush=True)
    print(f"portfolio_return={portfolio_return}", flush=True)
    print(f"portfolio_variance={portfolio_variance}", flush=True)
    print(f"realized_correlation={rho}", flush=True)
    print(f"portfolio_gini={portfolio_gini}", flush=True)

    returns_by_ticker = {position.ticker: position for position in position_returns}
    rows = []
    for row in positions_df.iter_rows(named=True):
        position = returns_by_ticker.get(row["ticker"])
        if position is None:
            print(f"skip_missing_return={row['ticker']}", flush=True)
            continue

        yahoo_symbol = helper.yahoo_ticker(row["ticker"], row.get("country"))
        rows.append(
            (
                selected_date,
                None,
                position.ticker,
                yahoo_symbol,
                position.weight,
                position.position_return,
                position.component_realized_variance,
                position.component_realized_volatility,
                portfolio_return,
                portfolio_variance,
                rho,
                None if rho is None else rho * 100.0,
                portfolio_gini,
                number_of_assets,
                "CBOE-style same-day snapshot realized correlation",
            )
        )
        print(f"prepared={position.ticker}|{yahoo_symbol}", flush=True)

    columns = [
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
    batch_insert(
        "portfolio_history",
        columns,
        rows,
        conflict_columns=["date", "ticker"],
        overwrite_conflicts=True,
    )
    print(f"inserted_rows={len(rows)}", flush=True)
    return 0


def main() -> int:
    args = parse_args()
    log_file = Path(args.log_file)
    log_file.parent.mkdir(parents=True, exist_ok=True)
    with log_file.open("w") as log_handle:
        with redirect_stdout(log_handle), redirect_stderr(log_handle):
            return run(date.fromisoformat(args.date))


if __name__ == "__main__":
    raise SystemExit(main())
