"""Theta options: numerical alignment, durable resume and SDK failure boundaries."""

from datetime import date, timedelta
import json
import logging
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from data_collection import massive
from data_collection.checkpoints import Checkpoints
from data_collection.option_metrics import OptionSettings, black_scholes
from data_collection import theta, theta_pipeline as pipeline


DAY = date(2026, 9, 30)
EXPIRY = DAY + timedelta(days=30)


def samples(day=DAY, *, sigma=.3, spot=100.):
    stock = {"created": f"{day}T21:15:00Z", "last_trade": f"{day}T20:00:00Z", "close": spot}
    options = []
    for side in ("call", "put"):
        price = float(black_scholes(spot, 100., (EXPIRY-day).days/365, sigma, is_call=side == "call"))
        options.append(dict(symbol="AAPL", created=stock["created"], expiration=str(EXPIRY),
                            right=side.upper(), strike=100., bid=price-.05, ask=price+.05, volume=0))
    return [stock], options


def test_midpoint_iv_with_zero_trade_volume_and_split_adjusted_rv():
    stocks, options = samples()
    prices = [{"date": str(DAY-timedelta(days=30-i)), "Close": 50.*np.exp(.01*np.sin(i))} for i in range(31)]
    rows = pipeline.daily_metrics("AAPL", DAY, DAY, stocks, options,
                                  settings=OptionSettings(-1), adjusted_prices=prices)
    row, = rows
    assert row["status"] == row["premium_status"] == "ok"
    assert row["underlying_price"] == 100.  # Never use adjusted 50-dollar prices as option spot.
    assert row["implied_volatility"] == pytest.approx(.3)
    assert row["call_volume"] == 0
    assert row["implied_risk_premium"] == pytest.approx(.3-row["realized_volatility"])
    assert row["variance_spread"] == pytest.approx(.09-row["realized_volatility"]**2)
    json.dumps(row, allow_nan=False)


def test_iv_only_does_not_manufacture_realized_vol_or_zero_premium():
    stocks, options = samples()
    row, = pipeline.daily_metrics("AAPL", DAY, DAY, stocks, options, settings=OptionSettings(-1))
    assert row["status"] == "ok" and row["implied_volatility"] == pytest.approx(.3)
    assert row["premium_status"] == "not_requested"
    assert row["realized_volatility"] is row["implied_risk_premium"] is None


def test_no_carry_forward_crossed_quotes_and_missing_stock_session():
    stocks, options = samples()
    tomorrow = DAY + timedelta(days=1)
    stocks.append(dict(stocks[0], created=f"{tomorrow}T21:15:00Z"))
    options[0]["ask"] = options[0]["bid"] - .1  # Crossed market is unusable.
    rows = pipeline.daily_metrics("AAPL", DAY, tomorrow, stocks, options, settings=OptionSettings(-1))
    assert len(rows) == 2 and all(row["implied_volatility"] is None for row in rows)
    row, = pipeline.daily_metrics("AAPL", DAY, DAY, [], options, settings=OptionSettings(-1))
    assert row["reason"] == "missing_same_session_underlying_close"


def test_expiry_selection_and_impossible_prices():
    stocks, options = samples()
    too_short = [dict(r, expiration=str(DAY+timedelta(days=29))) for r in options]
    longer = [dict(r, expiration=str(DAY+timedelta(days=60)), strike=101.) for r in options]
    row, = pipeline.daily_metrics("AAPL", DAY, DAY, stocks, too_short+longer+options, settings=OptionSettings(-1))
    assert row["expiration_date"] == str(EXPIRY) and row["strike"] == 100.
    for record in options:
        record.update(bid=1000., ask=1001.)
    row, = pipeline.daily_metrics("AAPL", DAY, DAY, stocks, options, settings=OptionSettings(-1))
    assert row["reason"] == "option_price_outside_black_scholes_bounds"
    assert row["implied_volatility"] is None


def test_month_boundaries_leap_year_and_new_york_date_conversion():
    assert list(pipeline.month_windows(date(2024, 2, 28), date(2024, 3, 1))) == [
        (date(2024, 2, 28), date(2024, 2, 29)), (date(2024, 3, 1), date(2024, 3, 1))]
    assert pipeline._session("2026-10-01T00:01:00Z") == "2026-09-30"


class RPCFailure(Exception):
    def __init__(self, code):
        self.value = code
        super().__init__("provider body contains session-secret and email@example.com")

    def code(self):
        return SimpleNamespace(name=self.value)


def test_sdk_retries_deadline_and_reauthenticates_without_logging_secrets(tmp_path, monkeypatch):
    monkeypatch.delenv("THETADATA_RATE_STATE", raising=False)
    log = pipeline._logger(tmp_path/"run.log")
    attempts, authentications, delays = [], [], []
    replies = [RPCFailure("UNAUTHENTICATED"), RPCFailure("DEADLINE_EXCEEDED"), pd.DataFrame(samples()[1])]
    def history(**kwargs):
        attempts.append(kwargs)
        response = replies.pop(0)
        if isinstance(response, Exception):
            raise response
        return response
    def factory(key, timeout):
        authentications.append(1)
        return SimpleNamespace(option_history_eod=history)
    client = theta.ThetaEOD("session-secret", factory=factory, sleep=delays.append, log=log)
    monkeypatch.setattr(client.limiter, "wait", lambda: None)
    assert len(client.history("option", "AAPL", DAY, DAY)) == 2
    assert len(authentications) == 2 and len(attempts) == 3
    assert attempts[0]["expiration"] == "*" and attempts[0]["max_dte"] == 75
    assert delays == [5., 10.]
    assert "session-secret" not in (tmp_path/"run.log").read_text()
    assert "email@example.com" not in (tmp_path/"run.log").read_text()


def test_permission_error_is_not_retried_and_empty_is_not_failure(monkeypatch):
    monkeypatch.delenv("THETADATA_RATE_STATE", raising=False)
    def denied(**kwargs):
        raise RPCFailure("PERMISSION_DENIED")
    client = theta.ThetaEOD("key", factory=lambda *_: SimpleNamespace(option_history_eod=denied))
    monkeypatch.setattr(client.limiter, "wait", lambda: None)
    with pytest.raises(theta.ThetaAPIError, match="PERMISSION_DENIED"):
        client.history("option", "AAPL", DAY, DAY)
    class NoDataFoundError(Exception):
        pass
    def empty(**kwargs):
        raise NoDataFoundError("vendor body")
    client.client.option_history_eod = empty
    assert client.history("option", "AAPL", DAY, DAY) == []


def test_theta_and_massive_have_independent_shared_rate_state(tmp_path, monkeypatch):
    monkeypatch.setenv("THETADATA_RATE_STATE", str(tmp_path/"theta-rate.sqlite"))
    monkeypatch.setenv("MASSIVE_RATE_STATE", str(tmp_path/"massive-rate.sqlite"))
    now = [100.]
    monkeypatch.setattr(massive.time, "time", lambda: now[0])
    def sleep(delay):
        now[0] += delay
    first = theta.ThetaEOD("key", sleep=sleep)
    second = theta.ThetaEOD("key", sleep=sleep)
    starts = []
    for worker in (first, second, first):
        worker.limiter.wait()
        starts.append(now[0])
    assert starts == pytest.approx([100., 103.2, 106.4])
    massive._RequestLimiter(sleep=sleep).wait()
    assert now[0] == pytest.approx(106.4)  # Theta did not reserve Massive's allowance.


def test_raw_response_survives_interruption_and_resume_avoids_calls(tmp_path):
    calls = []
    def history(kind, *a, **kw):
        calls.append(kind)
        if calls == ["stock", "option"]:
            raise KeyboardInterrupt()
        return samples()[0 if kind == "stock" else 1]
    client = SimpleNamespace(history=history)
    log = pipeline._logger(tmp_path/"run.log")
    with Checkpoints(tmp_path/"state.sqlite", provider="thetadata_options_v1") as cache:
        with pytest.raises(KeyboardInterrupt):
            pipeline.collect_month(client, cache, "AAPL", DAY, DAY, settings=OptionSettings(-1), log=log)
    with Checkpoints(tmp_path/"state.sqlite", provider="thetadata_options_v1") as cache:
        first = pipeline.collect_month(client, cache, "AAPL", DAY, DAY, settings=OptionSettings(-1), log=log)
        assert pipeline.collect_month(client, cache, "AAPL", DAY, DAY, settings=OptionSettings(-1), log=log) == first
    assert calls == ["stock", "option", "option"]


def test_adaptive_timeout_split_preserves_successful_subranges(tmp_path):
    calls = []
    def history(kind, ticker, start, end, **kwargs):
        calls.append((start, end))
        if start != end:
            raise theta.ThetaAPIError("DEADLINE_EXCEEDED")
        return [{"day": str(start)}]
    log = pipeline._logger(tmp_path/"run.log")
    with Checkpoints(tmp_path/"state.sqlite", provider="thetadata_options_v1") as cache:
        rows = pipeline._theta_response(SimpleNamespace(history=history), cache, "option", "AAPL",
                                         DAY, DAY+timedelta(days=1), OptionSettings(-1), 2, log)
        assert len(rows) == 2 and len(calls) == 3
        pipeline._theta_response(None, cache, "option", "AAPL", DAY, DAY+timedelta(days=1), OptionSettings(-1), 2, log)
    assert len(calls) == 3


def test_checkpoint_provider_isolation_in_both_directions(tmp_path):
    for provider, other in [("massive", "thetadata_options_v1"), ("thetadata_options_v1", "massive")]:
        path = tmp_path/(provider+".sqlite")
        with Checkpoints(path, provider=provider):
            pass
        with pytest.raises(ValueError, match="provider"):
            with Checkpoints(path, provider=other):
                pass


@pytest.fixture
def cli_setup(tmp_path, monkeypatch):
    monkeypatch.setenv("THETADATA_APIKEY", "theta-secret")
    monkeypatch.delenv("THETADATA_API_KEY", raising=False)
    monkeypatch.delenv("MASSIVE_API_KEY", raising=False)
    monkeypatch.delenv("POLYGON_IO", raising=False)
    monkeypatch.setenv("THETADATA_RATE_STATE", str(tmp_path/"theta-rate.sqlite"))
    monkeypatch.setenv("MASSIVE_RATE_STATE", str(tmp_path/"massive-rate.sqlite"))
    calls = []
    class Fake:
        def __init__(self, *a, **kw):
            pass
        def history(self, kind, ticker, start, end, **kwargs):
            calls.append((kind, ticker))
            return samples()[0] if kind == "stock" else [dict(r, symbol=ticker) for r in samples()[1]]
    monkeypatch.setattr(pipeline, "ThetaEOD", Fake)
    output = tmp_path/"options.jsonl"
    argv = ["AAPL", "--start", str(DAY), "--end", str(DAY), "--output", str(output), "--iv-only"]
    return argv, output, calls


def test_cli_resume_rebuilds_torn_output_and_rejects_changed_model_or_universe(cli_setup):
    argv, output, calls = cli_setup
    assert pipeline.main(argv) == 0
    original = output.read_text()
    output.write_text(original+'{"torn":')
    assert pipeline.main(argv) == 0 and output.read_text() == original
    assert len(calls) == 2
    assert pipeline.main(argv+["--risk-free-rate", ".05"]) == 1
    assert pipeline.main(["MSFT"]+argv[1:]) == 1
    assert output.read_text() == original and len(calls) == 2


def test_cli_requires_massive_for_premium_before_fetch_or_truncation(cli_setup):
    argv, output, calls = cli_setup
    assert pipeline.main(argv[:-1]) == 1
    assert calls == [] and not output.exists()


def test_cli_will_not_overwrite_a_secret_file(cli_setup, tmp_path):
    argv, output, calls = cli_setup
    key_file = tmp_path/"theta.key"
    key_file.write_text("preserve-this-secret")
    with pytest.raises(SystemExit):
        pipeline.main(argv+["--api-key-file", str(key_file), "--log-file", str(key_file)])
    assert key_file.read_text() == "preserve-this-secret" and calls == []


def test_disk_checkpoint_failure_stops_before_next_ticker(cli_setup, monkeypatch):
    argv, output, calls = cli_setup
    def full(*a, **kw):
        raise OSError("disk full")
    monkeypatch.setattr(Checkpoints, "save_response", full)
    assert pipeline.main(["MSFT"]+argv) == 1
    assert calls == [("stock", "MSFT")]


def test_invalid_authentication_stops_instead_of_retrying_every_ticker(cli_setup, monkeypatch):
    argv, output, calls = cli_setup
    attempted = []
    def failed(self, kind, ticker, *a, **kw):
        attempted.append(ticker)
        raise theta.ThetaAPIError("AUTHENTICATION_FAILED")
    monkeypatch.setattr(pipeline.ThetaEOD, "history", failed)
    assert pipeline.main(["MSFT"]+argv) == 1
    assert attempted == ["MSFT"]


def test_cli_continues_after_ticker_failure_then_retries_only_failure(cli_setup, monkeypatch):
    argv, output, calls = cli_setup
    original = pipeline.ThetaEOD.history
    def denied(self, kind, ticker, *a, **kw):
        if ticker == "BAD":
            raise theta.ThetaAPIError("PERMISSION_DENIED")
        return original(self, kind, ticker, *a, **kw)
    monkeypatch.setattr(pipeline.ThetaEOD, "history", denied)
    assert pipeline.main(["BAD"]+argv) == 1
    assert json.loads(output.read_text())["ticker"] == "AAPL"
    monkeypatch.setattr(pipeline.ThetaEOD, "history", original)
    assert pipeline.main(["BAD"]+argv) == 0
    assert [json.loads(line)["ticker"] for line in output.read_text().splitlines()] == ["BAD", "AAPL"]
    assert len(calls) == 4


def test_two_writers_cannot_own_same_output(tmp_path):
    with pipeline._writer_lock(tmp_path/"writer.lock"):
        with pytest.raises(ValueError, match="Another worker"):
            with pipeline._writer_lock(tmp_path/"writer.lock"):
                pass


def test_sdk_adapter_applies_rpc_deadlines_and_suppresses_auth_logger(monkeypatch):
    # Exercise the pinned SDK's public constructor and the actual stub interface
    # without authenticating or connecting to the network.
    pytest.importorskip("thetadata")
    import thetadata
    calls = []
    def rpc(request, **kwargs):
        calls.append(kwargs)
        return []
    def constructor(**kwargs):
        assert kwargs["api_key"] == "key" and kwargs["dataframe_type"] == "pandas"
        assert logging.getLogger("thetadata.client").disabled
        return SimpleNamespace(stub=SimpleNamespace(GetOptionHistoryEod=rpc, GetStockHistoryEod=rpc))
    monkeypatch.setattr(thetadata, "ThetaClient", constructor)
    client = theta._sdk_factory("key", 123.)
    client.stub.GetOptionHistoryEod(object())
    client.stub.GetStockHistoryEod(object())
    assert calls == [{"timeout": 123.}, {"timeout": 123.}]
