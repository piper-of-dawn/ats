import argparse

from ats.jobs import build_jobs, run_jobs


def parse_args():
    parser = argparse.ArgumentParser(
        description="Process tickers from a Supabase table and compute factor outputs."
    )
    parser.add_argument(
        "table_name",
        help="Supabase table name containing yahoo_finance_ticker and representative_index_ticker columns.",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    jobs = build_jobs(args.table_name)
    if not jobs:
        print(
            f"No valid rows found in table '{args.table_name}'. Required columns: "
            "yahoo_finance_ticker, representative_index_ticker."
        )
        return

    run_jobs(jobs)
