from __future__ import annotations

import argparse

from ats.dataIO.supabase_integration import fetch_table
from ats.fundamental_data import get_dividends_parallel


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("table_name")
    parser.add_argument("output_path")
    parser.add_argument("secrets_toml_path")
    args = parser.parse_args()

    tickers = fetch_table(args.table_name)["yahoo_finance_ticker"].to_list()
    get_dividends_parallel(
        tickers,
        output_path=args.output_path,
        secrets_toml_path=args.secrets_toml_path,
    )


if __name__ == "__main__":
    main()
