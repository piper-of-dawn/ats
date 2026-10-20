import io
import json
from datetime import datetime, timezone
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit

import pandas as pd
import pytest

from data_collection import massive
from data_collection import collect_ticker, collect_tickers, iter_tickers
from data_collection import collector


@pytest.fixture
def transport(monkeypatch):
    replies, calls = [], []
    clock = {"now": 0.0}

    def sleep(seconds):
        clock["now"] += seconds

    def open_request(request, timeout):
        calls.append((clock["now"], request))
        reply = replies.pop(0)
        if isinstance(reply, BaseException):
            raise reply
        return io.BytesIO(json.dumps(reply).encode())

    monkeypatch.setenv("POLYGON_IO", "test-secret")
    monkeypatch.delenv("MASSIVE_API_KEY", raising=False)
    monkeypatch.delenv("MASSIVE_API_BASE_URL", raising=False)
    monkeypatch.delenv("POLYGON_API_BASE_URL", raising=False)
    monkeypatch.setattr(massive, "_limiter", massive._RequestLimiter(clock=lambda: clock["now"], sleep=sleep))
    monkeypatch.setattr(massive, "_open", open_request)
    return replies, calls


@pytest.fixture
def yahoo(monkeypatch):
    requests = []

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 10, 20, 12, tzinfo=timezone.utc)

    class Stock:
        def __init__(self, ticker):
            requests.append(("ticker", ticker))

        def history(self, **kwargs):
            requests.append(("history", kwargs))
            return pd.DataFrame(
                {"Close": [100.0], "Adj Close": [float("nan")], "Volume": [1234],
                 "Dividends": [0.5], "Stock Splits": [0.0]},
                index=pd.DatetimeIndex(["2026-10-19"], name="Date"),
            )

        def get_cash_flow(self, **kwargs):
            return pd.DataFrame({pd.Timestamp("2026-06-30"): [20.0, -5.0]},
                                index=["Operating Cash Flow", "Capital Expenditure"])

        get_income_stmt = get_cash_flow
        get_balance_sheet = get_cash_flow

        def get_upgrades_downgrades(self):
            return pd.DataFrame({"Action": ["up"], "Firm": ["Test"]},
                                index=pd.DatetimeIndex(["2026-10-15"]))

        def get_earnings_history(self):
            return pd.DataFrame({"epsActual": [1.2], "epsEstimate": [1.1]},
                                index=pd.DatetimeIndex(["2026-06-30"]))

        @property
        def options(self):
            return ("2026-10-23", "2026-11-10", "2026-11-25", "2027-01-10", "2027-01-25")

        def option_chain(self, expiry):
            requests.append(("option_chain", expiry))
            return SimpleNamespace(
                calls=pd.DataFrame({"strike": [100.0], "impliedVolatility": [0.25]}),
                puts=pd.DataFrame({"strike": [100.0], "impliedVolatility": [0.3]}),
            )

    monkeypatch.setattr(collector.yf, "Ticker", Stock)
    monkeypatch.setattr(collector, "datetime", Clock)
    return requests


def test_default_collection_preserves_inputs_and_follows_every_page(transport, yahoo):
    replies, calls = transport
    replies.extend([
        {"results": {"market_cap": 1000000, "ticker": "AAPL"}},
        {"results": [{"settlement_date": "2026-10-15", "days_to_cover": 2.5}]},
        {"results": [{"effective_date": "2026-10-01", "free_float": 1000}]},
        {"results": [{"date": "2026-10-19", "short_volume_ratio": 42}]},
        {"results": [{"id": "news1", "insights": [{"ticker": "AAPL", "sentiment": "positive"}]}],
         "next_url": "https://api.massive.com/v2/reference/news?cursor=news"},
        {"results": [{"id": "news2"}]},
        {"results": [{"tickers": ["AAPL"], "transaction_code": "G"}],
         "next_url": "https://api.massive.com/stocks/filings/vX/form-4?cursor=insider"},
        {"results": [{"tickers": ["AAPL"], "transaction_code": "P"},
                     {"tickers": ["MSFT"], "transaction_code": "S"}]},
    ])
    result = collect_ticker(" aapl ", start_date="2026-10-01", end_date="2026-10-20")
    feeds = result["datasets"]
    assert result["ticker"] == "AAPL"
    assert all(feed["status"] == "ok" for feed in feeds.values())
    assert feeds["prices"]["data"][0]["Volume"] == 1234
    assert feeds["prices"]["data"][0]["Dividends"] == 0.5
    assert feeds["prices"]["data"][0]["Adj Close"] is None
    assert feeds["cash_flow"]["data"][0]["Capital Expenditure"] == -5
    assert feeds["earnings"]["data"][0]["period_end"].startswith("2026-06-30")
    assert feeds["reference"]["scope"] == "current_snapshot"
    assert [row["id"] for row in feeds["news"]["data"]] == ["news1", "news2"]
    assert feeds["news"]["data"][0]["ticker_insights"][0]["sentiment"] == "positive"
    assert [row["transaction_code"] for row in feeds["insider_transactions"]["data"]] == ["G", "P"]
    assert [time for time, _ in calls] == list(range(0, 8 * 13, 13))
    assert parse_qs(urlsplit(calls[5][1].full_url).query) == {"cursor": ["news"]}
    params = parse_qs(urlsplit(calls[6][1].full_url).query)
    assert params["tickers"] == ["AAPL"]
    assert params["filing_date.gte"] == ["2026-10-01"]
    assert params["limit"] == ["10000"]
    history = next(kwargs for name, kwargs in yahoo if name == "history")
    assert history["end"] == "2026-10-21"  # Yahoo's end is exclusive.
    assert history["actions"] and not history["auto_adjust"]
    selected_expirations = [expiry for name, expiry in yahoo if name == "option_chain"]
    assert selected_expirations == ["2026-11-10", "2026-11-25", "2027-01-10", "2027-01-25"]
    assert "test-secret" not in json.dumps(result, allow_nan=False)


def test_page_failure_never_becomes_a_partial_success_and_other_feeds_continue(transport):
    replies, calls = transport
    replies.extend([
        {"results": [{"id": "partial"}], "next_url": "https://api.massive.com/next"},
        HTTPError("https://api.massive.com/?apiKey=test-secret", 403, "test-secret", {}, io.BytesIO()),
        {"results": []},
    ])
    result = collect_ticker("AAPL", datasets=["news", "float"])
    assert result["datasets"]["news"]["status"] == "error"
    assert result["datasets"]["news"]["data"] is None
    assert result["datasets"]["news"]["error"]["message"] == "Massive HTTP 403"
    assert result["datasets"]["float"]["status"] == "empty"
    assert len(calls) == 3
    assert "test-secret" not in json.dumps(result)


def test_selecting_tickers_deduplicates_and_shares_the_limiter(transport, monkeypatch):
    replies, calls = transport
    replies.extend([{"results": {"ticker": symbol}} for symbol in ["MSFT", "AAPL"]])
    monkeypatch.setattr(collector.yf, "Ticker", lambda _: pytest.fail("Yahoo should not be constructed"))
    results = collect_tickers(["msft", "AAPL", " MSFT "], datasets=(x for x in ["reference"]))
    assert list(results) == ["MSFT", "AAPL"]
    assert [time for time, _ in calls] == [0, 13]
    assert results["MSFT"]["history_window"] == results["AAPL"]["history_window"]


@pytest.mark.parametrize("kwargs", [{"ticker": ""}, {"ticker": "AAPL/../../bad"},
                                     {"ticker": "AAPL", "datasets": ["paid_ratios"]},
                                     {"ticker": "AAPL", "datasets": "news"},
                                     {"ticker": "AAPL", "start_date": "2026-10-21", "end_date": "2026-10-20"}])
def test_invalid_inputs_fail_before_network_requests(transport, yahoo, kwargs):
    with pytest.raises(ValueError):
        collect_ticker(**kwargs)
    assert not transport[1] and not yahoo


def test_entire_ticker_list_is_validated_before_first_request(transport):
    with pytest.raises(ValueError):
        list(iter_tickers(["AAPL", ""]))
    assert not transport[1]
    with pytest.raises(ValueError):
        collect_tickers("AAPL")


def test_empty_selection_and_optional_statements_do_not_fetch_massive(transport, yahoo):
    assert collect_ticker("AAPL", datasets=[])["datasets"] == {}
    result = collect_ticker("AAPL", datasets=["income_statement", "balance_sheet"])
    assert set(result["datasets"]) == {"income_statement", "balance_sheet"}
    assert not transport[1]


def test_untrusted_error_text_is_not_serialized(monkeypatch):
    class Stock:
        def get_earnings_history(self):
            raise RuntimeError("https://vendor/?apiKey=secret")
    monkeypatch.setattr(collector.yf, "Ticker", lambda _: Stock())
    result = collect_ticker("AAPL", datasets=["earnings"])
    assert result["datasets"]["earnings"]["error"]["type"] == "RuntimeError"
    assert "secret" not in json.dumps(result)


def test_cli_rebuilds_output_and_retries_only_failed_feeds(tmp_path, monkeypatch, transport):
    from data_collection import __main__ as cli
    output = tmp_path / "stocks.jsonl"
    log = tmp_path / "collection.log"
    replies, calls = transport
    replies.extend([
        {"results": {"ticker": "AAPL"}},
        HTTPError("https://api.massive.com/?apiKey=test-secret", 403, "test-secret", {}, io.BytesIO()),
    ])
    monkeypatch.setattr("sys.argv", ["data_collection", "AAPL", "MSFT", "--datasets", "reference",
                                    "--output", str(output), "--log-file", str(log)])
    assert cli.main() == 1
    assert len(calls) == 2
    # Simulate a torn final JSON line. Re-export from SQLite must repair it.
    with output.open("a") as stream:
        stream.write('{"ticker":')
    replies.append({"results": {"ticker": "MSFT"}})
    assert cli.main() == 0
    results = [json.loads(line) for line in output.read_text().splitlines()]
    assert [r["ticker"] for r in results] == ["AAPL", "MSFT"]
    assert results[0]["datasets"]["reference"]["resumed_from_checkpoint"]
    assert not results[1]["datasets"]["reference"]["resumed_from_checkpoint"]
    assert len(calls) == 3
    assert "UNAVAILABLE ticker=MSFT" in log.read_text()
    assert "RESUME ticker=AAPL" in log.read_text()
    assert "test-secret" not in log.read_text()


def test_interruption_preserves_completed_feed_and_restarts_incomplete_pagination(tmp_path, transport):
    checkpoint = tmp_path / "run.sqlite"
    log = tmp_path / "run.log"
    replies, calls = transport
    replies.extend([
        {"results": {"ticker": "AAPL"}},
        {"results": [{"id": "first-page"}], "next_url": "https://api.massive.com/next"},
        KeyboardInterrupt(),
    ])
    kwargs = dict(datasets=["reference", "news", "float"], checkpoint_path=checkpoint, log_path=log)
    with pytest.raises(KeyboardInterrupt):
        collect_ticker("AAPL", **kwargs)
    assert len(calls) == 3
    replies.extend([
        {"results": [{"id": "retried-first-page"}], "next_url": "https://api.massive.com/next"},
        {"results": [{"id": "second-page"}]},
        {"results": []},
    ])
    result = collect_ticker("AAPL", **kwargs)
    assert result["datasets"]["reference"]["resumed_from_checkpoint"]
    assert [r["id"] for r in result["datasets"]["news"]["data"]] == ["retried-first-page", "second-page"]
    assert urlsplit(calls[3][1].full_url).path == "/v2/reference/news"
    assert len(calls) == 6
    assert result["datasets"]["float"]["status"] == "empty"
    # Both ok and empty feeds are complete and require no more requests.
    resumed = collect_ticker("AAPL", **kwargs)
    assert all(f["resumed_from_checkpoint"] for f in resumed["datasets"].values())
    assert len(calls) == 6
    text = log.read_text()
    assert "interrupted" in text and "EMPTY ticker=AAPL" in text
    assert "test-secret" not in text


def test_resume_freezes_dates_and_rejects_a_different_explicit_window(tmp_path, transport, monkeypatch, yahoo):
    kwargs = dict(datasets=["reference"], checkpoint_path=tmp_path / "run.sqlite", log_path=tmp_path / "run.log")
    transport[0].append({"results": {"ticker": "AAPL"}})
    original = collect_ticker("AAPL", **kwargs)

    class Later(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 10, 21, 12, tzinfo=timezone.utc)

    monkeypatch.setattr(collector, "datetime", Later)
    resumed = collect_ticker("AAPL", **kwargs)
    assert resumed["history_window"] == original["history_window"]
    assert resumed["datasets"]["reference"]["retrieved_at"] == original["datasets"]["reference"]["retrieved_at"]
    with pytest.raises(ValueError, match="date window"):
        collect_ticker("AAPL", end_date="2026-10-21", **kwargs)
    assert len(transport[1]) == 1


def test_log_records_empty_and_sanitized_yahoo_error(tmp_path, transport, monkeypatch):
    log = tmp_path / "collection.log"
    class Stock:
        def get_earnings_history(self):
            raise RuntimeError("https://vendor/?apiKey=secret")
    monkeypatch.setattr(collector.yf, "Ticker", lambda _: Stock())
    transport[0].append({"results": []})
    result = collect_ticker("AAPL", datasets=["earnings", "float"], log_path=log)
    text = log.read_text()
    assert "ERROR UNAVAILABLE ticker=AAPL provider=yahoo feed=earnings type=RuntimeError" in text
    assert "WARNING EMPTY ticker=AAPL provider=massive feed=float" in text
    assert "secret" not in text
    assert result["datasets"]["float"]["status"] == "empty"


def test_checkpoint_write_failure_stops_before_another_request(tmp_path, transport, monkeypatch):
    from data_collection.checkpoints import Checkpoints
    def broken_save(*args):
        raise OSError("disk is full")
    monkeypatch.setattr(Checkpoints, "save", broken_save)
    transport[0].append({"results": {"ticker": "AAPL"}})
    with pytest.raises(OSError):
        collect_ticker("AAPL", datasets=["reference", "float"],
                       checkpoint_path=tmp_path / "run.sqlite", log_path=tmp_path / "run.log")
    assert len(transport[1]) == 1
    assert "Collection stopped type=OSError" in (tmp_path / "run.log").read_text()


def test_cli_preserves_legacy_output_without_checkpoint(tmp_path, monkeypatch):
    from data_collection import __main__ as cli
    output = tmp_path / "legacy.jsonl"
    output.write_text('{"ticker":"OLD"}\n')
    monkeypatch.setattr("sys.argv", ["data_collection", "AAPL", "--output", str(output),
                                    "--log-file", str(tmp_path / "run.log")])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 2
    assert output.read_text() == '{"ticker":"OLD"}\n'


def test_cli_interrupt_returns_130_then_resumes_partial_ticker(tmp_path, monkeypatch, transport):
    from data_collection import __main__ as cli
    output = tmp_path / "stocks.jsonl"
    replies, calls = transport
    replies.extend([{"results": {"ticker": "AAPL"}}, KeyboardInterrupt()])
    monkeypatch.setattr("sys.argv", ["data_collection", "AAPL", "--datasets", "reference", "float",
                                    "--output", str(output), "--log-file", str(tmp_path / "run.log")])
    assert cli.main() == 130
    assert output.read_text() == ""
    replies.append({"results": []})
    assert cli.main() == 0
    result = json.loads(output.read_text())
    assert result["datasets"]["reference"]["resumed_from_checkpoint"]
    assert len(calls) == 3


def test_cli_collects_500_tickers_from_file_with_a_runtime_key(tmp_path, monkeypatch, transport, capsys):
    from data_collection import __main__ as cli
    monkeypatch.delenv("POLYGON_IO")
    names = [f"STOCK{i:03}" for i in range(500)]
    ticker_file = tmp_path / "tickers.txt"
    ticker_file.write_text("# Selected universe\n" + ",\n".join(names) + "\nSTOCK000\n")
    key_file = tmp_path / "key.txt"
    key_file.write_text("file-secret\n")
    output = tmp_path / "stocks.jsonl"
    log_file = tmp_path / "collection.log"
    transport[0].extend({"results": {"ticker": symbol}} for symbol in names)
    monkeypatch.setattr("sys.argv", ["collect-stocks", "--tickers-file", str(ticker_file),
                                    "--api-key-file", str(key_file), "--datasets", "reference",
                                    "--output", str(output), "--log-file", str(log_file), "--log-to-stderr"])
    assert cli.main() == 0
    assert [json.loads(line)["ticker"] for line in output.read_text().splitlines()] == names
    assert len(transport[1]) == 500
    assert all(request.get_header("Authorization") == "Bearer file-secret" for _, request in transport[1])
    assert "file-secret" not in log_file.read_text() + output.read_text() + capsys.readouterr().err


def test_cli_accepts_dataset_parameter_as_one_comma_separated_argument(tmp_path, monkeypatch, transport):
    from data_collection import __main__ as cli
    transport[0].extend([{"results": {"ticker": "AAPL"}}, {"results": []}])
    output = tmp_path / "stocks.jsonl"
    monkeypatch.setattr("sys.argv", ["collect-stocks", "AAPL", "--datasets", "reference,float",
                                    "--output", str(output), "--log-file", str(tmp_path / "run.log")])
    assert cli.main() == 0
    assert set(json.loads(output.read_text())["datasets"]) == {"reference", "float"}


def test_cli_missing_key_fails_before_any_requests_or_output(tmp_path, monkeypatch, transport):
    from data_collection import __main__ as cli
    monkeypatch.delenv("POLYGON_IO")
    output = tmp_path / "stocks.jsonl"
    log = tmp_path / "collection.log"
    monkeypatch.setattr("sys.argv", ["collect-stocks", "AAPL", "--datasets", "reference",
                                    "--output", str(output), "--log-file", str(log)])
    assert cli.main() == 1
    assert not transport[1] and not output.exists()
    assert "Missing Massive key" in log.read_text()
