from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
from collections import namedtuple
import polars as pl
import numpy as np
from functools import cached_property


def weighted_avg_tail(x, n):
    if x is None or len(x) < n:
        return None
    w = np.arange(1, n + 1)
    result = np.dot(x[-n:], w) / w.sum()
    return result


def get_winsorized_returns(log_returns, threshold=5.0):
    scale = np.median(np.abs(log_returns))
    robust_z_scores = np.abs(log_returns) / scale
    capped_returns = np.where(
        robust_z_scores > threshold,
        np.sign(log_returns) * threshold * scale,
        log_returns,
    )
    return capped_returns


def get_winsorized_log_returns(df, price_col_name):
    df = df.with_columns(pl.col(price_col_name).log().alias("log_close"))
    df = df.with_columns(pl.col("log_close").diff().alias("log_return")).drop(
        "log_close"
    )
    return get_winsorized_returns(df["log_return"].to_numpy())


def estimate_market_beta(market_returns: np.array, asset_returns: np.array):
    """
    Estimate the market beta of an asset using linear regression.

    Parameters:
    market_returns (np.array): An array of market returns.
    asset_returns (np.array): An array of asset returns.

    Returns:
    float: The estimated market beta.
    """
    if len(market_returns) != len(asset_returns):
        raise ValueError("Market returns and asset returns must have the same length.")

    # Add a constant term for the intercept
    X = np.vstack([market_returns, np.ones(len(market_returns))]).T
    y = asset_returns

    # Perform linear regression using numpy's least squares method
    beta, _ = np.linalg.lstsq(X, y, rcond=None)[0]

    return beta


def clean_yahoo_finance_data(df, symbol):
    return (
        df.rename(
            {
                "('Date', '')": "date",
                f"('{symbol}', 'Adj Close')": "close",
            }
        )
        .select(["date", "close"])
        .with_columns(pl.col("date").cast(pl.Date))
    )


@dataclass
class EquityTicker:
    symbol: str
    market_index: str | None = None
    data_path: str | None = None
    price_data_flag: bool = False

    def __post_init__(self) -> None:  # type: ignore[override]
        s = (self.symbol or "").strip()
        if not s:
            raise ValueError("symbol must be a non-empty string")
        object.__setattr__(self, "symbol", s)

    def __str__(self) -> str:
        return self.symbol

    def __repr__(self) -> str:
        return (
            f"EquityTicker(symbol={self.symbol!r}, "
            f"market_index={self.market_index!r}, data_path={self.data_path!r})"
        )

    @cached_property
    def price_data(self):
        return self._get_price_data()

    @cached_property
    def market_index_data(self):
        if not self.market_index:
            raise ValueError("market_index must be set before getting market returns")
        return pl.read_parquet(f"{self.data_path}/{self.market_index}.parquet")

    def set_market_index(self, index: str) -> "EquityTicker":
        s = (index or "").strip()
        if not s:
            raise ValueError("market index must be a non-empty string")
        object.__setattr__(self, "market_index", s)
        return self

    def clean_yahoo_finance_data(self, df):
        return (
            df.rename(
                {
                    "('Date', '')": "date",
                    f"('{self.symbol}', 'Adj Close')": "close",
                }
            )
            .select(["date", "close"])
            .with_columns(pl.col("date").cast(pl.Date))
        )

    def set_data_path(self, path: str) -> "EquityTicker":
        p = (path or "").strip()
        if not p:
            raise ValueError("data path must be a non-empty string")
        object.__setattr__(self, "data_path", p)
        return self

    # Private helper: check if {symbol}.parquet exists under data_path.
    def _data_file_exists(self) -> bool:
        if not self.data_path:
            return False
        base = Path(self.data_path)
        expected_name = f"{self.symbol}.parquet"
        if base.is_dir():
            target = base / expected_name
            return target.exists()
        # If data_path is a file path, require exact filename match
        return base.name == expected_name and base.exists()

    def _get_price_data(self, verbose=False) -> "EquityTicker":
        if not self.data_path:
            raise ValueError("data_path must be set before getting price data")

        base = Path(self.data_path)
        parquet_path = base / f"{self.symbol}.parquet" if base.is_dir() else base

        if parquet_path.exists():
            if verbose:
                print(f"Reading existing data for {self.symbol} from {parquet_path}")
            try:
                df = self.clean_yahoo_finance_data(pl.read_parquet(str(parquet_path)))
                return df
            except Exception:
                if verbose:
                    print(f"Warning: failed to read parquet file at {parquet_path}")
                self.get_from_yahoo_finance(verbose=verbose)
        else:
            if verbose:
                print(
                    f"No existing data for {self.symbol}; fetching from Yahoo Finance"
                )
            # raise FileNotFoundError(f"{parquet_path} does not exist")
            self.get_from_yahoo_finance(verbose=verbose)
        return self

    def get_from_yahoo_finance(self, verbose=False):
        base = Path(self.data_path)
        parquet_path = base / f"{self.symbol}.parquet" if base.is_dir() else base
        from yahoo_finance import fetch_price_data

        df = fetch_price_data(self.symbol, str(base))
        df = clean_yahoo_finance_data(df, self.symbol)
        if verbose:
            print(
                f"Fetched data for {self.symbol}: {df.shape if df is not None else None}"
            )
            print(df.head(3))
        # If we successfully fetched data, persist it to the expected parquet path
        if isinstance(df, pl.DataFrame) and df.height > 0 and "close" in df.columns:
            parquet_path.parent.mkdir(parents=True, exist_ok=True)
            try:
                df.write_parquet(str(parquet_path))
                self.price_data_flag = True
            except Exception:
                if verbose:
                    print(f"Warning: failed to write parquet file at {parquet_path}")
        self.price_data = self.clean_yahoo_finance_data(df)
        return self

    def get_market_returns_and_asset_log_returns(self):
        mkt_data = self.market_index_data.rename({"close": self.market_index})
        price_data = self.price_data.rename({"close": self.symbol})
        data = (
            mkt_data.join(price_data, on="date", how="left").drop_nulls().sort("date")
        )
        mkt = get_winsorized_log_returns(data, price_col_name=self.market_index)[1:]
        ast = get_winsorized_log_returns(data, price_col_name=self.symbol)[1:]
        # df = mkt.join(ast, on="date", how="left").drop_nulls()
        LogReturns = namedtuple("LogReturns", ["market", "asset"])
        return LogReturns(market=mkt, asset=ast)

    def _create_returns_df(self) -> pl.DataFrame:
        """Return DataFrame with columns [date, log_return] for `ticker`."""
        df = self._clean_data_for()
        return df.select(["date", "log_return"]).sort("date")

    def get_winsorized_returns(self, threshold=5.0):
        log_returns = np.diff(np.log(self.price_data["Close"].to_numpy()))
        scale = np.median(np.abs(log_returns))
        robust_z_scores = np.abs(log_returns) / scale
        self.capped_returns = np.where(
            robust_z_scores > threshold,
            np.sign(log_returns) * threshold * scale,
            log_returns,
        )
        return self

    def get_momentum_signal(self, half_life, market_index):
        market_log_returns, asset_log_returns = (
            self.get_market_returns_and_asset_log_returns()
        )
        eta = np.log(2) / half_life
        beta = estimate_market_beta(market_log_returns, asset_log_returns)
        idiosyncratic_returns = asset_log_returns - beta * market_log_returns
        from helpers import compute_ema_signal_from_returns

        momentum_signal = compute_ema_signal_from_returns(idiosyncratic_returns, eta)
        return weighted_avg_tail(momentum_signal, 5)
