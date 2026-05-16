import polars as pl
from ats.yahoo_finance import fetch_price_data
from ats.helpers import compute_ema_signal, ema_volatility
from yfinance import Ticker as YfTicker
import numpy as np
def log_returns(df: pl.DataFrame) -> pl.DataFrame:
    return df.sort("date").with_columns((pl.col("close") / pl.col("close").shift(1)).log().alias("log_return"))

def winsorize_log_returns_inplace(df: pl.DataFrame, return_col="log_return", threshold=5.0) -> pl.DataFrame:
    if return_col not in df.columns: 
        return df
    scale = df.select(pl.col(return_col).abs().median()).item()
    if not scale: 
        return df
    cap = float(threshold) * float(scale)
    c = pl.col(return_col)
    return df.with_columns(pl.when(c.abs() > cap).then(c.sign() * cap).otherwise(c).alias(return_col))

class EquityTicker(YfTicker):
    def __init__(self, ticker: str, mkt_index: "EquityTicker|None" = None):
        super().__init__(ticker)
        self.ticker, self.mkt_index, self.price_data = ticker, mkt_index, None

    def _require_data(self, who="ticker"):
        if self.price_data is None or self.price_data.is_empty():
            raise ValueError(f"Price data is not available for {who} '{self.ticker}'.")

    def fetch_price_data(self):
        self.price_data = fetch_price_data(self.ticker)
        self._require_data()
        return self

    def make_log_returns(self):
        self._require_data()
        self.price_data = log_returns(self.price_data)
        return self

    def winsorize_log_returns(self, threshold=5.0):
        self._require_data()
        self.price_data = winsorize_log_returns_inplace(self.price_data, threshold=threshold)
        return self

    def join_with_market_index(self):
        self._require_data()
        if not self.mkt_index or self.mkt_index.price_data is None or self.mkt_index.price_data.is_empty(): 
            raise ValueError("Market index data is not available.")
        mkt = self.mkt_index.price_data.select("date", pl.col("log_return").alias("mkt_log_return"))
        self.price_data = self.price_data.join(mkt, on="date", how="left").drop_nulls()
        return self
    
    def compute_beta(self):
        self._require_data()
        if "mkt_log_return" not in self.price_data.columns:
            raise ValueError("Market log returns are not available. Please join with market index first.")
        cov = self.price_data.select(pl.cov("log_return", "mkt_log_return")).item()
        var = self.price_data.select(pl.var("mkt_log_return")).item()
        if var == 0:
            raise ValueError("Variance of market log returns is zero, cannot compute beta.")
        self.beta = cov / var
        return self
    
    def get_idiosyncratic_returns(self):
        self._require_data()
        if not hasattr(self, "beta"):
            raise ValueError("Beta is not computed. Please compute beta first.")
        self.price_data = self.price_data.with_columns((pl.col("log_return") - pl.col("mkt_log_return") * self.beta).alias("idiosyncratic_returns"))
        return self
    
    def __weighted_avg_tail__ (self, tail_size: int, array):
        w = np.arange(1, tail_size + 1)
        return np.dot(array[-tail_size:], w) / w.sum()
    
    def get_long_term_momentum_signal(self, half_life=112, volatility_model=ema_volatility, volatility_model_args={}):
        self._require_data()
        eta = np.log(2) / half_life
        ltm = compute_ema_signal(price_data=self.price_data, volatility_model=volatility_model, volatility_model_args=volatility_model_args, eta=eta)
        self.ltm = self.__weighted_avg_tail__(5,ltm)
        return self
    
    def get_short_term_momentum_signal(self, half_life=20, volatility_model=ema_volatility, volatility_model_args={}):
        self._require_data()
        eta = np.log(2) / half_life
        stm = compute_ema_signal(price_data=self.price_data, volatility_model=volatility_model, volatility_model_args=volatility_model_args, eta=eta)
        self.stm = self.__weighted_avg_tail__(5, stm)
        return self
    
    def _get_stm_series(self, half_life=20, volatility_model=ema_volatility, volatility_model_args={}):
        self._require_data()
        eta = np.log(2) / half_life
        stm_series = compute_ema_signal(price_data=self.price_data, volatility_model=volatility_model, volatility_model_args=volatility_model_args, eta=eta)
        return stm_series
    
    def _get_ltm_series(self, half_life=112, volatility_model=ema_volatility, volatility_model_args={}):
        self._require_data()
        eta = np.log(2) / half_life
        ltm_series = compute_ema_signal(price_data=self.price_data, volatility_model=volatility_model, volatility_model_args=volatility_model_args, eta=eta)
        return ltm_series
    
    def plot(self, what=np.ndarray):
        try:
            import matplotlib.pyplot as plt
        except ImportError:
            raise ImportError("matplotlib is required for plotting. Please install it with 'pip install matplotlib'.")
        plt.plot(what)
        plt.show()
