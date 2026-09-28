"""Source data for one equity and its optional market index."""

from datetime import date, datetime, timedelta, timezone

import polars as pl
from yfinance import Ticker as YfTicker

from ats.dataIO import massive


class EquityTicker(YfTicker):
    def __init__(
        self,
        ticker: str,
        mkt_index: "EquityTicker | None" = None,
        price_data: pl.DataFrame | None = None,
    ):
        super().__init__(ticker)
        self.ticker = ticker
        self.mkt_index = mkt_index
        self._price_data = price_data
        self.short_interest = None
        self.short_volume = None
        self.float_data = None
        self.news_sentiment = None
        self.news_coverage: tuple[date, date] | None = None
        self.loaded_recommendations = None
        self.loaded_price_targets = None

    @property
    def price_data(self) -> pl.DataFrame | None:
        """Loaded prices; reading this property never starts a network request."""
        return self._price_data

    @price_data.setter
    def price_data(self, value: pl.DataFrame | None) -> None:
        self._price_data = value

    def fetch_price_data(
        self,
        period: str = "1y",
        *,
        all_available_price_history: bool = False,
    ) -> "EquityTicker":
        if all_available_price_history:
            period = "max"
        self.price_data = self._fetch_price_data_from_yahoo(period=period)
        return self

    def _fetch_price_data_from_yahoo(self, period: str = "1y") -> pl.DataFrame:
        yahoo_history = super().history(period=period, auto_adjust=False, actions=False)
        if yahoo_history is None or yahoo_history.empty:
            return pl.DataFrame()

        price_history = pl.from_pandas(yahoo_history.reset_index())
        if price_history.is_empty():
            return pl.DataFrame()
        date_column = next(
            (
                column for column in price_history.columns
                if str(column).lower() in {"date", "datetime"}
                or "date" in str(column).lower()
            ),
            price_history.columns[0],
        )
        close_column = next(
            (column for column in price_history.columns if str(column).lower() == "adj close"),
            None,
        )
        if close_column is None:
            close_column = next(
                (column for column in price_history.columns if str(column).lower() == "close"),
                None,
            )
        if close_column is None:
            return pl.DataFrame()
        return price_history.select(
            pl.col(date_column).cast(pl.Date, strict=False).alias("date"),
            pl.col(close_column).cast(pl.Float64, strict=False).alias("close"),
            pl.lit(self.ticker).cast(pl.String).alias("ticker"),
        )

    def get_short_interest(self, start_date=None, end_date=None, *, api_key=None):
        """Load the latest short-interest record, or dated history."""
        self.short_interest = None
        self.short_interest = massive.fetch_short_interest(
            self.ticker, start_date, end_date, api_key=api_key
        )
        return self

    def get_short_volume(self, start_date=None, end_date=None, *, api_key=None):
        """Load the latest off-exchange short-sale volume, or dated history."""
        self.short_volume = None
        self.short_volume = massive.fetch_short_volume(
            self.ticker, start_date, end_date, api_key=api_key
        )
        return self

    def get_float(self, *, api_key=None):
        """Load the provider's latest dated float record, if available."""
        self.float_data = None
        self.float_data = massive.fetch_float(self.ticker, api_key=api_key)
        return self

    def get_news_sentiment(self, start_date=None, end_date=None, *, api_key=None):
        """Load articles and ticker-specific insights without scoring them."""
        self.news_sentiment = None
        self.news_coverage = None
        end = date.fromisoformat(end_date) if isinstance(end_date, str) else end_date
        end = end if end is not None else datetime.now(timezone.utc).date()
        start = date.fromisoformat(start_date) if isinstance(start_date, str) else start_date
        start = start if start is not None else end - timedelta(days=6)
        self.news_sentiment = massive.fetch_news_sentiment(
            self.ticker, start, end, api_key=api_key
        )
        self.news_coverage = (start, end)
        return self

    def fetch_analyst_data(self) -> "EquityTicker":
        """Load both Yahoo analyst inputs used by the factor calculation."""
        self.loaded_recommendations = None
        self.loaded_price_targets = None
        self.loaded_recommendations = self.get_recommendations_summary()
        self.loaded_price_targets = self.get_analyst_price_targets()
        return self
