import importlib
import io
import json
from datetime import date, datetime, timedelta, timezone
from email.utils import format_datetime
from http.client import IncompleteRead
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlsplit

import pytest

from ats.dataIO import massive
from ats.derivatives import options_risk_premium as options
from ats import fundamental_data
from ats.ticker import EquityTicker


@pytest.fixture
def transport(monkeypatch):
    clock = {"now": 0.0}
    calls = []
    replies = []
    sleeps = []

    def sleep(seconds):
        sleeps.append(seconds)
        clock["now"] += seconds

    def open_request(request, timeout):
        calls.append((clock["now"], request, timeout))
        reply = replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return io.BytesIO(reply if isinstance(reply, bytes) else json.dumps(reply).encode())

    monkeypatch.setenv("POLYGON_IO", "test-secret")
    monkeypatch.delenv("POLYGON_API_BASE_URL", raising=False)
    monkeypatch.setattr(massive, "_limiter", massive._RequestLimiter(clock=lambda: clock["now"], sleep=sleep))
    monkeypatch.setattr(massive, "_open", open_request)
    return replies, calls, sleeps


def error(code, headers=None):
    return HTTPError("https://api.massive.com/?apiKey=test-secret", code, "test-secret", headers or {}, io.BytesIO(b"test-secret"))


def test_shared_pacing_across_tickers_and_legacy_callers(transport):
    replies, calls, _ = transport
    replies.extend([{"results": []}] * 5)
    EquityTicker("AAPL").get_short_interest().get_float()
    EquityTicker("MSFT").get_short_volume()
    fundamental_data.get_dividends("AAPL")
    options.fetch_option_daily_bars("O:TEST", date(2026, 9, 1), date(2026, 9, 2))
    assert [call[0] for call in calls] == [0, 13, 26, 39, 52]
    for _, request, timeout in calls:
        assert timeout == 30
        assert request.get_header("Authorization") == "Bearer test-secret"
        assert "test-secret" not in request.full_url


def test_contract_pagination_preserves_filters_only_on_first_page(transport):
    replies, calls, _ = transport
    replies.extend([
        {"results": [{"ticker": "ONE"}], "next_url": "https://api.massive.com/v3/reference/options/contracts?cursor=abc"},
        {"results": [{"ticker": "TWO"}]},
    ])
    assert options.fetch_candidate_contracts("aapl", date(2026, 10, 1)) == [{"ticker": "ONE"}, {"ticker": "TWO"}]
    assert parse_qs(urlsplit(calls[0][1].full_url).query)["underlying_ticker"] == ["AAPL"]
    assert parse_qs(urlsplit(calls[1][1].full_url).query) == {"cursor": ["abc"]}
    assert calls[1][0] == 13


@pytest.mark.parametrize("destination", ["https://evil.example/data", "https://api.massive.com:8443/data", "https://user@api.massive.com/data"])
def test_pagination_rejects_other_origins_and_credentials(transport, destination):
    replies, calls, _ = transport
    replies.append({"results": [], "next_url": destination})
    with pytest.raises(massive.MassiveAPIError, match="origin"):
        massive.fetch_records("/first")
    assert len(calls) == 1


def test_repeated_page_fails_instead_of_looping(transport):
    replies, calls, _ = transport
    replies.append({"results": [], "next_url": "https://api.massive.com/first"})
    with pytest.raises(massive.MassiveAPIError, match="cycle"):
        massive.fetch_records("/first")
    assert len(calls) == 1


@pytest.mark.parametrize("failure", [error(429, {"Retry-After": "90"}), error(503), URLError("test-secret"), TimeoutError("test-secret"), IncompleteRead(b"test-secret")])
def test_transient_failure_retries_with_shared_pacing(transport, failure):
    replies, calls, sleeps = transport
    replies.extend([failure, {"results": []}, {"results": []}])
    massive.request_json("/first")
    massive.request_json("/second")
    delay = 90 if isinstance(failure, HTTPError) and failure.code == 429 else 60
    assert sleeps == [delay, 13]
    assert [c[0] for c in calls] == [0, delay, delay + 13]


def test_retries_are_bounded_and_error_is_redacted(transport):
    replies, calls, _ = transport
    replies.extend([error(503), error(503), error(503)])
    with pytest.raises(massive.MassiveAPIError, match="exhausted three attempts") as exc:
        massive.request_json("/first")
    assert "test-secret" not in str(exc.value)
    assert len(calls) == 3


@pytest.mark.parametrize("reply", [error(403), b"not JSON test-secret", [], {"status": "ERROR", "error": "test-secret"}, {"status": []}])
def test_permanent_and_malformed_responses_fail_without_retries(transport, reply):
    replies, calls, _ = transport
    replies.append(reply)
    with pytest.raises(massive.MassiveAPIError) as exc:
        massive.request_json("/first")
    assert "test-secret" not in str(exc.value)
    assert len(calls) == 1


def test_authentication_is_lazy_and_query_key_moves_to_header(transport, monkeypatch):
    replies, calls, _ = transport
    monkeypatch.delenv("POLYGON_IO")
    stock = EquityTicker("AAPL")
    assert stock.float_data is None
    with pytest.raises(massive.MassiveAPIError, match="POLYGON_IO"):
        stock.get_float()
    assert calls == []
    replies.append({})
    massive.request_json("/first?apiKey=legacy", api_key="explicit")
    assert calls[0][1].get_header("Authorization") == "Bearer explicit"
    assert "apiKey" not in calls[0][1].full_url


def test_configured_origin_is_used_at_request_time(transport, monkeypatch):
    replies, calls, _ = transport
    monkeypatch.setenv("POLYGON_API_BASE_URL", "https://api.polygon.io")
    replies.append({})
    massive.request_json("/first")
    assert calls[0][1].full_url == "https://api.polygon.io/first"


def test_latest_does_not_follow_history_and_refresh_clears_stale_data(transport):
    replies, calls, _ = transport
    replies.extend([
        {"results": [{"settlement_date": "2026-09-15", "short_interest": 100}], "next_url": "https://api.massive.com/older"},
        {"results": []},
        error(403),
    ])
    stock = EquityTicker("AAPL")
    assert stock.get_short_interest() is stock
    assert stock.short_interest[0]["settlement_date"] == "2026-09-15"
    assert len(calls) == 1
    stock.get_short_interest()
    assert stock.short_interest == []
    stock.short_interest = [{"old": True}]
    with pytest.raises(massive.MassiveAPIError):
        stock.get_short_interest()
    assert stock.short_interest is None


def test_history_filters_and_pagination(transport):
    replies, calls, _ = transport
    replies.extend([{"results": [{"date": "2026-09-02"}], "next_url": "https://api.massive.com/older"}, {"results": [{"date": "2026-09-01"}]}])
    stock = EquityTicker("AAPL").get_short_volume("2026-09-01", date(2026, 9, 2))
    assert len(stock.short_volume) == 2
    params = parse_qs(urlsplit(calls[0][1].full_url).query)
    assert params["date.gte"] == ["2026-09-01"]
    assert params["date.lte"] == ["2026-09-02"]
    with pytest.raises(ValueError, match="start_date"):
        stock.get_short_volume("2026-09-03", "2026-09-02")
    assert len(calls) == 2


def test_news_retains_original_insights_and_selects_matching_ticker(transport):
    replies, calls, _ = transport
    insights = [{"ticker": "MSFT", "sentiment": "positive"}, {"ticker": "AAPL", "sentiment": "negative"}]
    replies.append({"results": [{"id": "article", "published_utc": "2026-09-20T01:00:00Z", "insights": insights}, {"id": "no-insights"}]})
    stock = EquityTicker("AAPL")
    assert stock.get_news_sentiment(end_date="2026-09-26") is stock
    article = stock.news_sentiment[0]
    assert article["insights"] == insights
    assert article["ticker_insights"] == [insights[1]]
    assert stock.news_sentiment[1]["ticker_insights"] == []
    params = parse_qs(urlsplit(calls[0][1].full_url).query)
    assert params["published_utc.gte"] == ["2026-09-20"]
    assert params["published_utc.lt"] == ["2026-09-27"]


def test_legacy_credentials_and_batches_are_sequential_without_response_cache(transport, monkeypatch, tmp_path):
    replies, calls, _ = transport
    monkeypatch.delenv("POLYGON_IO")
    secrets = tmp_path / "env.toml"
    secrets.write_text('polygon_io_api_key_1 = "financial-key"\npolygon_io_api_key_2 = "dividend-key"\n')
    replies.extend([{"results": [1]}, {"results": [2]}, {"results": [3]}])
    rows = fundamental_data.get_fundamentals_parallel(["AAPL", "AAPL"], max_workers=10, batch_size=0, batch_sleep=999, secrets_toml_path=secrets)
    assert rows == [{"results": [1]}, {"results": [2]}]
    assert [c[0] for c in calls] == [0, 13]
    assert calls[0][1].get_header("Authorization") == "Bearer financial-key"
    monkeypatch.setenv("POLYGON_IO", "environment-key")
    fundamental_data.get_dividends("AAPL", secrets_toml_path=secrets)
    assert calls[-1][1].get_header("Authorization") == "Bearer environment-key"


def test_fundamental_script_import_does_not_fetch(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Import must not fetch data")
    monkeypatch.setattr(fundamental_data, "get_fundamentals_parallel", forbidden)
    importlib.import_module("ats.get_fundamental_data")


def test_retry_after_accepts_http_date_and_invalid_values():
    future = datetime.now(timezone.utc) + timedelta(seconds=180)
    assert 178 <= massive._retry_delay({"Retry-After": format_datetime(future)}) <= 180
    assert massive._retry_delay({"Retry-After": "invalid"}) == 60
    assert massive._retry_delay({"Retry-After": "2"}) == 60


def test_end_date_alone_selects_latest_and_preserves_missing_results(transport):
    replies, calls, _ = transport
    replies.append({"status": "OK"})
    assert massive.fetch_short_interest(" aapl ", end_date="2026-09-01") == []
    params = parse_qs(urlsplit(calls[0][1].full_url).query)
    assert params["settlement_date.lte"] == ["2026-09-01"]
    assert params["limit"] == ["1"]
    assert params["ticker"] == ["AAPL"]
    assert "settlement_date.gte" not in params


def test_malformed_records_fail_instead_of_becoming_empty(transport):
    replies, _, _ = transport
    replies.append({"results": {"unexpected": True}})
    with pytest.raises(massive.MassiveAPIError, match="list of records"):
        massive.fetch_float("AAPL")


def test_get_float_fetches_only_latest_record_and_handles_empty_results(transport):
    replies, calls, _ = transport
    latest = {"ticker": "AAPL", "effective_date": "2026-09-15", "free_float": 123}
    older = {"ticker": "AAPL", "effective_date": "2026-08-15", "free_float": 100}
    replies.append({
        "results": [latest, older],
        "next_url": "https://api.massive.com/stocks/vX/float?cursor=older",
    })

    ticker = EquityTicker("AAPL")
    assert ticker.get_float() is ticker
    assert ticker.float_data == [latest]
    assert len(calls) == 1
    assert urlsplit(calls[0][1].full_url).path == "/stocks/vX/float"
    assert parse_qs(urlsplit(calls[0][1].full_url).query) == {
        "ticker": ["AAPL"], "limit": ["1"]
    }

    replies.append({"results": [], "next_url": "https://api.massive.com/stocks/vX/float?cursor=older"})
    assert ticker.get_float() is ticker
    assert ticker.float_data == []
    assert len(calls) == 2
