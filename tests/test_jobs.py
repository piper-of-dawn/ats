from datetime import date

import polars as pl

import ats.jobs as jobs_module


class _FakeMarketTicker:
    calls = []

    def __init__(self, ticker):
        self.ticker = ticker
        self.price_data = pl.DataFrame(
            {
                "date": [date(2024, 1, 1), date(2024, 1, 2)],
                "log_return": [0.0, 0.01],
            }
        )

    def fetch_price_data(self):
        self.calls.append(self.ticker)
        return self

    def make_log_returns(self):
        return self

    def winsorize_log_returns(self):
        return self


def test_prepare_market_index_data_fetches_each_representative_index_once(monkeypatch):
    _FakeMarketTicker.calls = []
    monkeypatch.setattr(jobs_module, "EquityTicker", _FakeMarketTicker)

    result = jobs_module._prepare_market_index_data(
        [
            {"ticker": "AAPL", "representative_index_ticker": "^GSPC"},
            {"ticker": "MSFT", "representative_index_ticker": "^GSPC"},
            {"ticker": "NVDA", "representative_index_ticker": "^IXIC"},
        ]
    )

    assert _FakeMarketTicker.calls == ["^GSPC", "^IXIC"]
    assert set(result) == {"^GSPC", "^IXIC"}
    assert result["^GSPC"]["market_price_data"].height == 2


def test_prepare_market_index_data_records_index_fetch_failures(monkeypatch):
    class FailingTicker(_FakeMarketTicker):
        def fetch_price_data(self):
            raise ValueError("no data")

    monkeypatch.setattr(jobs_module, "EquityTicker", FailingTicker)

    result = jobs_module._prepare_market_index_data(
        [{"ticker": "AAPL", "representative_index_ticker": "^BAD"}]
    )

    assert result == {"^BAD": {"market_error": "market index ^BAD error=no data"}}


def test_prepare_market_index_data_retries_transient_index_failures(monkeypatch):
    class FlakyTicker(_FakeMarketTicker):
        attempts = 0

        def fetch_price_data(self):
            self.__class__.attempts += 1
            if self.__class__.attempts == 1:
                raise ValueError("temporary yahoo miss")
            return self

    monkeypatch.setattr(jobs_module, "EquityTicker", FlakyTicker)

    result = jobs_module._prepare_market_index_data(
        [{"ticker": "AAPL", "representative_index_ticker": "^GSPC"}],
        retry_delay_seconds=0,
    )

    assert FlakyTicker.attempts == 2
    assert result["^GSPC"]["market_price_data"].height == 2


def test_normalize_jobs_strips_tickers_and_drops_incomplete_rows():
    result = jobs_module._normalize_jobs(
        [
            {"ticker": " AAPL ", "representative_index_ticker": " ^GSPC "},
            {"ticker": "", "representative_index_ticker": "^GSPC"},
            {"ticker": "MSFT", "representative_index_ticker": None},
        ]
    )

    assert result == [{"ticker": "AAPL", "representative_index_ticker": "^GSPC"}]


def test_run_jobs_returns_empty_frame_without_starting_pool(monkeypatch):
    def fail_if_called(*args, **kwargs):
        raise AssertionError("pool should not be created for empty jobs")

    monkeypatch.setattr(jobs_module, "get_context", fail_if_called)

    result = jobs_module.run_jobs([], table_name="us_midcap", as_of_date=date(2024, 1, 1))

    assert result.is_empty()
    assert result.columns == ["ticker", "stm", "ltm", "beta", "as_of_date"]


def test_run_jobs_handles_all_null_metrics(monkeypatch):
    class FakePool:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def imap_unordered(self, func, items):
            for item in items:
                yield func(item)

    class FakeContext:
        def Pool(self):
            return FakePool()

    inserted = {}

    def fake_process_ticker(item):
        return {
            "ticker": item["ticker"],
            "mkt_index": item["representative_index_ticker"],
            "stm": None,
            "ltm": None,
            "beta": None,
            "status": "market index failed",
        }

    def fake_batch_insert_polars_df(**kwargs):
        inserted.update(kwargs)

    monkeypatch.setattr(jobs_module, "get_context", lambda name: FakeContext())
    monkeypatch.setattr(jobs_module, "_prepare_market_index_data", lambda jobs: {"^BAD": {}})
    monkeypatch.setattr(jobs_module, "process_ticker", fake_process_ticker)
    monkeypatch.setattr(
        jobs_module, "batch_insert_polars_df", fake_batch_insert_polars_df
    )

    result = jobs_module.run_jobs(
        [{"ticker": "AAPL", "representative_index_ticker": "^BAD"}],
        table_name="us_midcap",
        as_of_date=date(2024, 1, 1),
    )

    assert result["ticker"].to_list() == ["AAPL"]
    assert result["ltm"].dtype == pl.Float64
    assert inserted["table_name"] == "us_midcap_metrics"
