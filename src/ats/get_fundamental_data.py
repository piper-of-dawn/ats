from ats.fundamental_data import get_fundamentals_parallel
from ats.dataIO.supabase_integration import fetch_table


def main():
    tickers = fetch_table("us_midcap")["yahoo_finance_ticker"].to_list()
    get_fundamentals_parallel(
        tickers, output_path="/home/karma/ats_python/notebooks/DATA/fundamental_data_midcap/"
    )


if __name__ == "__main__":
    main()
