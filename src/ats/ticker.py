import polars as pl
from ats.fundamentals.analyst_price_targets import median_centered_score
from ats.fundamentals.analyst_ratings import agreement, direction, sample_confidence, stability
from ats.fundamentals.analyst_trends import analyst_grade_trend_signal
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
        self.ticker, self.mkt_index = ticker, mkt_index
        self._price_data = None
        self._log_returns_winsorized = False

    @property
    def price_data(self) -> pl.DataFrame:
        if self._price_data is None:
            self._price_data = self._fetch_price_data_from_yahoo()
        return self._price_data

    @price_data.setter
    def price_data(self, value: pl.DataFrame | None):
        self._price_data = value

    def _fetch_price_data_from_yahoo(self) -> pl.DataFrame:
        yahoo_history = super().history(
            period="1y",
            auto_adjust=False,
            actions=False,
        )
        if yahoo_history is None or yahoo_history.empty:
            return pl.DataFrame()

        yahoo_history = yahoo_history.reset_index()
        price_history = pl.from_pandas(yahoo_history)
        if price_history.is_empty():
            return pl.DataFrame()

        date_column = next(
            (
                column
                for column in price_history.columns
                if str(column).lower() in {"date", "datetime"}
                or "date" in str(column).lower()
            ),
            price_history.columns[0],
        )
        close_column = next(
            (
                column
                for column in price_history.columns
                if str(column).lower() == "adj close"
            ),
            None,
        )
        if close_column is None:
            close_column = next(
                (
                    column
                    for column in price_history.columns
                    if str(column).lower() == "close"
                ),
                None,
            )
        if close_column is None:
            return pl.DataFrame()

        return price_history.select(
            pl.col(date_column).cast(pl.Date, strict=False).alias("date"),
            pl.col(close_column).cast(pl.Float64, strict=False).alias("close"),
            pl.lit(self.ticker).cast(pl.String).alias("ticker"),
        )

    def _require_data(self, who="ticker"):
        if self.price_data is None or self.price_data.is_empty():
            raise ValueError(f"Price data is not available for {who} '{self.ticker}'.")

    def make_log_returns(self):
        self._require_data()
        self.price_data = log_returns(self.price_data)
        return self

    def winsorize_log_returns(self, threshold=5.0):
        self._require_data()
        self.price_data = winsorize_log_returns_inplace(self.price_data, threshold=threshold)
        self._log_returns_winsorized = True
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

    def getCombinedRating(self, lam=0.8, k=10):
        data = pl.DataFrame(self.get_recommendations_summary()).to_dicts()
        mu_star = direction(data, lam)
        C_star = agreement(data, lam)
        T = stability(data, lam)
        S = sample_confidence(data, lam, k)
        self.combined_rating = (mu_star / 2) * C_star * T * S
        return self

    def getAnalystPriceTargetDeviation(self):
        price_targets = self.get_analyst_price_targets()
        if not isinstance(price_targets, dict) or not price_targets:
            self.analyst_price_target_deviation = None
            return self
        try:
            self.analyst_price_target_deviation = round(median_centered_score(price_targets), 2)
        except (KeyError, TypeError, ValueError):
            self.analyst_price_target_deviation = None
        return self

    def getAnalystGradeTrendSignal(self):
        analyst_grades = self.get_upgrades_downgrades()
        if analyst_grades is None or analyst_grades.empty:
            self.analyst_grade_trend_signal = None
            return self

        analyst_grades = pl.from_pandas(
            analyst_grades.rename_axis("GradeDate").reset_index()
        ).with_columns(pl.col("GradeDate").dt.date())
        self.analyst_grade_trend_signal = analyst_grade_trend_signal(analyst_grades)
        return self

    def __weighted_avg_tail__ (self, tail_size: int, array):
        w = np.arange(1, tail_size + 1)
        return np.dot(array[-tail_size:], w) / w.sum()

    def _prepare_momentum_data(self, winsorize=True, use_idiosyncratic_returns=True):
        if "log_return" not in self.price_data.columns:
            self.make_log_returns()
        if winsorize and not self._log_returns_winsorized:
            self.winsorize_log_returns()
        if use_idiosyncratic_returns:
            if not self.mkt_index:
                raise ValueError("Market index is required for idiosyncratic momentum.")
            if "log_return" not in self.mkt_index.price_data.columns:
                self.mkt_index.make_log_returns()
            if winsorize and not self.mkt_index._log_returns_winsorized:
                self.mkt_index.winsorize_log_returns()
            if "mkt_log_return" not in self.price_data.columns:
                self.join_with_market_index()
            if not hasattr(self, "beta"):
                self.compute_beta()
            if "idiosyncratic_returns" not in self.price_data.columns:
                self.get_idiosyncratic_returns()
        elif "idiosyncratic_returns" not in self.price_data.columns:
            self.price_data = self.price_data.with_columns(
                pl.col("log_return").alias("idiosyncratic_returns")
            ).drop_nulls()
        return self

    def get_long_term_momentum_signal(self, half_life=112, volatility_model=ema_volatility, volatility_model_args={}, winsorize=True, use_idiosyncratic_returns=True):
        self._prepare_momentum_data(winsorize=winsorize, use_idiosyncratic_returns=use_idiosyncratic_returns)
        eta = np.log(2) / half_life
        ltm = compute_ema_signal(price_data=self.price_data, volatility_model=volatility_model, volatility_model_args=volatility_model_args, eta=eta)
        self.ltm = self.__weighted_avg_tail__(5,ltm)
        return self

    def get_short_term_momentum_signal(self, half_life=20, volatility_model=ema_volatility, volatility_model_args={}, winsorize=True, use_idiosyncratic_returns=True):
        self._prepare_momentum_data(winsorize=winsorize, use_idiosyncratic_returns=use_idiosyncratic_returns)
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
