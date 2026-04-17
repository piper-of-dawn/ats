from ats.fundamental_data import get_fundamentals_parallel
from ats.dataIO.supabase_integration import fetch_table
large_cap_tickers  = fetch_table("us_midcap")['yahoo_finance_ticker'].to_list()
get_fundamentals_parallel(large_cap_tickers, output_path="/home/karma/ats_python/notebooks/DATA/fundamental_data_midcap/")