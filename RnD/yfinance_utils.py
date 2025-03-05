import yfinance as yf
import pandas as pd
import numpy as np
from datetime import date, datetime, timedelta
from pandas.tseries.offsets import BDay
from collections import namedtuple
from functools import lru_cache

@lru_cache(maxsize=32)
def fetch_stock_data(ticker, start_date=None, end_date=None, extract_col='Close'):
    """
    Fetches stock data from Yahoo Finance.
    
    Args:
        ticker (str): The stock ticker symbol
        start_date (date, optional): Start date for data retrieval. Defaults to today.
        end_date (date, optional): End date for data retrieval. Defaults to 90 business days prior to today.
    
    Returns:
        namedtuple: A named tuple containing dates, close prices, and returns
    """
    # Define the named tuple for return data
    StockData = namedtuple('StockData', ['date', 'close_price', 'returns'])
    
    # Set default values for dates
    if start_date is None:
        start_date = date.today()
        
    if end_date is None:
        # Calculate 90 business days prior to today
        end_date = (datetime.now() - BDay(90)).date()
    
    # Ensure start_date is after end_date for yfinance
    if start_date < end_date:
        temp = start_date
        start_date = end_date
        end_date = temp
    
    # Download data from yfinance
    data = yf.download(ticker, start=end_date, end=start_date, progress=False)
    
    # Extract the close prices
    close_prices = data[extract_col][ticker]

    # Calculate daily returns
    returns = close_prices.pct_change().fillna(0)
    
    # Convert Series to lists for the named tuple
    dates = close_prices.index.date.tolist()
    prices = close_prices.values.tolist()
    returns_list = returns.values.tolist()
    
    # Return data as a named tuple
    return [{"date": d, "close_price": price, "returns": ret} for d, price, ret in zip(dates, prices, returns_list)]

@lru_cache(maxsize=1)
def get_sp500_data():
    table = pd.read_html('https://en.wikipedia.org/wiki/List_of_S%26P_500_companies')
    df = table[0]    
    return df

import pickle
from multiprocessing import Pool
from tqdm import tqdm

def get_ticker_data(ticker):
    return ticker, fetch_stock_data(ticker)

def parallelize_fetch(tickers, output_file='stock_data.pkl', read_cache=False):
    if read_cache:
        with open(output_file, 'rb') as f:
            return pickle.load(f)
    else:
        with Pool() as pool:
            results = dict(list(tqdm(
                pool.imap(get_ticker_data, tickers),
                total=len(tickers),
                desc="Fetching stock data"
            )))

        
        with open(output_file, 'wb') as f:
            pickle.dump(results, f)
    
    return results