import pickle 
from datetime import date as D
import os
import polars as pl
from yfinance_utils import parallelize_fetch, get_ticker_data
from functools import cached_property
from sklearn.linear_model import Ridge, LinearRegression
import numpy as np
import pandas as pd
import statsmodels.api as sm

def perform_regression(X, Y, strategy='ols'):
    X = sm.add_constant(X)  # Add constant for intercept
    if strategy == 'ols':
            model = sm.OLS(Y, X).fit()
    elif strategy == 'ridge':
        model = sm.regression.linear_model.RidgeResults.from_formula("Y ~ X", data=pd.DataFrame({"Y": Y, "X": X[:,1]}), alpha=1.5)
    else:
        raise ValueError(f"Strategy {strategy} not supported")   
    return model



def get_cached_data (filepath):
    if os.path.exists(filepath):
        with open(filepath, 'rb') as f:
            data=pickle.load(f)
        if data['AAPL'][0]['date'] == D.today():
            print(f"Data is up to date, no need to fetch")
        else:
            print(f"Data is outdated, fetch new data. Last updated date: {data['AAPL'][-1]['date']}")
        return data
    else:
        raise ValueError(f"File {filepath} does not exist")
  

class Ticker:
    DATA_DICT = get_cached_data("stock_data.pkl")

    def __init__(self, symbol):
        self.symbol = symbol
        if symbol not in Ticker.DATA_DICT:
            self.data = pl.DataFrame(get_ticker_data(symbol)[1])
        else:
            self.data = pl.DataFrame(Ticker.DATA_DICT[symbol])

    @cached_property
    def price(self):
        if 'close_price' not in self.data.columns:
            raise ValueError(f"close_price column not found in data for {self.symbol}")
        return self.data['close_price'].to_numpy()
    
    @cached_property
    def returns(self):
        return pl.DataFrame(self.data)['returns'][1:].to_numpy()
    
    @cached_property
    def market_returns (self):
        spy_data = pl.DataFrame(Ticker.DATA_DICT["^SPX"])
        return spy_data['returns'][1:].to_numpy()
    
    def get_momentum(self, strategy='ols', window=90):
        X = np.array(range(1, len(self.price[-window:])+1))
        Y = np.log(self.price[-window:])
        model = perform_regression(X, Y, strategy)
        score = model.rsquared
        self.momentum = model.params[1] * score
        return self
    
    def get_market_beta (self, strategy='ols', window=180):
        X = self.market_returns[-window:]
        Y = self.returns[-window:]
        model = perform_regression(X,Y, strategy)
        self.beta = {"coef": model.params[1], "pval": model.pvalues[1]}
        self.alpha = {"coef": model.params[0], "pval": model.pvalues[0]}
        return self
    
