"""Numerical recovery, historical alignment, contract reuse and durable resume."""

from datetime import date, datetime, timedelta
import json
import sqlite3

import numpy as np
import pytest

from data_collection import market, massive, collector
from data_collection.checkpoints import Checkpoints
from data_collection.option_metrics import (
    OptionSettings, black_scholes, implied_volatility,
    rolling_realized_volatility, collect_option_metrics,
)


def test_vectorized_iv_recovers_call_put_vols_and_rejects_impossible_prices():
    rng = np.random.default_rng(123)
    spot = rng.uniform(20, 500, 1000)
    strike = spot * rng.uniform(.95, 1.05, 1000)
    years = rng.uniform(20 / 365, 2, 1000)
    expected = rng.uniform(.15, 1.3, 1000)
    calls = rng.integers(0, 2, 1000).astype(bool)
    prices = black_scholes(spot, strike, years, expected, .035, .012, calls)
    np.testing.assert_allclose(implied_volatility(prices, spot, strike, years, .035, .012, calls),
                               expected, rtol=1e-7, atol=1e-8)
    bad = implied_volatility([0, -1, 101, np.nan, 5, 5], 100, 100, [.1, .1, .1, .1, 0, -1])
    assert np.isnan(bad).all()
    assert float(implied_volatility(float(black_scholes(100., 100., .1, .3)), 100., 100., .1)) == pytest.approx(.3)


def test_realized_volatility_matches_trailing_sample_and_does_not_bridge_bad_bars():
    prices = 100 * np.exp(np.cumsum(np.array([0, .01, -.02, .04, -.01, .005])))
    actual = rolling_realized_volatility(prices, window=3)
    assert np.isnan(actual[:3]).all()
    assert actual[3] == pytest.approx(np.std([.01, -.02, .04], ddof=1) * np.sqrt(252))
    prices[3] = np.nan
    assert np.isnan(rolling_realized_volatility(prices, 3)[3:]).all()


@pytest.fixture
def history(monkeypatch):
    start, end = date(2025, 10, 20), date(2025, 10, 21)
    days = [start - timedelta(days=n) for n in range(70, -2, -1)]
    days = [d for d in days if d.weekday() < 5 and d <= end]
    adjusted = [{"date": d.isoformat(), "Close": 50 * np.exp(.01 * np.sin(i))} for i, d in enumerate(days)]
    raw = [{"date": d.isoformat(), "Close": 100.} for d in days]
    expiry = date(2025, 11, 21)
    symbols = {"call": "O:AAPL251121C00100000", "put": "O:AAPL251121P00100000"}
    contracts = [{"ticker": symbol, "strike_price": 100., "expiration_date": str(expiry),
                  "contract_type": side, "shares_per_contract": 100} for side, symbol in symbols.items()]
    counts = {"contracts": [], "bars": [], "stock": []}
    def stock(ticker, first, last, *, adjusted=True, **kw):
        counts["stock"].append(adjusted)
        return history_adjusted if adjusted else raw
    history_adjusted = adjusted
    def catalog(ticker, day, *a, **k):
        counts["contracts"].append(day)
        return contracts
    def bars(symbol, first, last, **kw):
        counts["bars"].append((symbol, first, last))
        return [{"t": int(datetime.combine(d, datetime.min.time(), market.NEW_YORK).timestamp() * 1000),
                 "c": float(black_scholes(100., 100., (expiry-d).days/365, .3, is_call=symbol==symbols['call'])),
                 "v": 100} for d in (start, end)]
    monkeypatch.setattr(market, "stock_bars", stock)
    monkeypatch.setattr(market, "option_contracts", catalog)
    monkeypatch.setattr(market, "option_bars", bars)
    return start, end, adjusted, raw, counts


def test_backfill_uses_raw_split_date_spot_and_fetches_contract_history_once(history):
    start, end, adjusted, raw, calls = history
    rows = collect_option_metrics("AAPL", start, end, settings=OptionSettings(history_days=-1))
    assert [row['date'] for row in rows] == [str(start), str(end)]
    assert all(row['status'] == 'ok' and row['underlying_price'] == 100 for row in rows)
    assert all(row['implied_volatility'] == pytest.approx(.3) for row in rows)
    assert rows[0]['implied_risk_premium'] == pytest.approx(.3 - rows[0]['realized_volatility'])
    assert calls['stock'] == [True, False]
    assert len(calls['bars']) == 2  # Two contracts for the whole range, not four calls.
    assert calls['contracts'] == [start, end]


def test_old_option_trade_is_not_carried_forward(history, monkeypatch):
    start, end, *_ = history
    real = market.option_bars
    monkeypatch.setattr(market, 'option_bars', lambda *a, **k: real(*a, **k)[:1])
    rows = collect_option_metrics('AAPL', start, end, settings=OptionSettings(history_days=-1))
    assert rows[0]['status'] == 'ok'
    assert rows[1]['implied_volatility'] is None
    assert rows[1]['reason'] == 'missing_same_session_option_trade'


def test_catalog_is_as_of_the_historical_date_not_expired_today(monkeypatch):
    seen = []
    monkeypatch.setattr(market, 'records', lambda path, params, **kw: seen.append(params) or [])
    market.option_contracts('AAPL', date(2025, 10, 20), date(2025, 11, 19), date(2025, 12, 10), 100.)
    assert len(seen) == 1
    assert seen[0]['as_of'] == '2025-10-20'
    assert seen[0]['expired'] == 'false'


def test_response_cache_survives_restart_and_never_saves_failed_pages(tmp_path, monkeypatch):
    path = tmp_path / 'state.sqlite'
    count = []
    def fetch(*a, **k):
        count.append(1)
        return [{'c': 10}]
    monkeypatch.setattr(massive, 'fetch_records', fetch)
    with Checkpoints(path) as cache:
        assert market.records('/test', {'a': 1}, checkpoint=cache) == [{'c': 10}]
    with Checkpoints(path) as cache:
        assert market.records('/test', {'a': 1}, checkpoint=cache) == [{'c': 10}]
        def fail(*a, **k):
            raise massive.MassiveAPIError('Massive HTTP 403')
        monkeypatch.setattr(massive, 'fetch_records', fail)
        with pytest.raises(massive.MassiveAPIError):
            market.records('/other', {}, checkpoint=cache)
        assert cache.response(json.dumps(['/other', {}], sort_keys=True)) is None
    assert len(count) == 1


def test_old_checkpoints_and_changed_model_settings_are_rejected(tmp_path):
    path = tmp_path / 'state.sqlite'
    with Checkpoints(path) as cache:
        cache.bind_options(OptionSettings().dictionary())
        with pytest.raises(ValueError):
            cache.bind_options(OptionSettings(risk_free_rate=.05).dictionary())
    with sqlite3.connect(path) as db:
        db.execute("UPDATE metadata SET value='1' WHERE key='format_version'")
    with pytest.raises(ValueError):
        with Checkpoints(path):
            pass


def test_bulk_prices_fetch_once_for_universe_and_cache_cannot_hide_other_tickers(tmp_path, monkeypatch):
    day = date(2025, 10, 20)
    stamp = int(datetime.combine(day, datetime.min.time(), market.NEW_YORK).timestamp() * 1000)
    calls = []
    def request(*a, **k):
        calls.append(a)
        return {'results': [{'T': t, 't': stamp, 'c': 100., 'v': 10} for t in ('AAPL', 'MSFT')]}
    monkeypatch.setattr(massive, 'request_json', request)
    with Checkpoints(tmp_path/'market.sqlite') as cache:
        for _ in range(2):
            result = market.bulk_price_history(['AAPL', 'MSFT'], day, day, cache=cache)
            assert set(result) == {'AAPL', 'MSFT'}
        assert len(calls) == 1
        market.bulk_price_history(['AAPL'], day, day, cache=cache)
        assert len(calls) == 2


def test_price_and_option_collection_does_not_instantiate_yahoo(history, tmp_path, monkeypatch):
    start, end, *_ = history
    monkeypatch.setattr(collector.yf, 'Ticker', lambda *_: pytest.fail('Yahoo price/option request'))
    result = collector.collect_ticker('AAPL', datasets=['prices', 'options'],
                                     start_date=start, end_date=end, log_path=tmp_path/'run.log')
    assert all(entry['provider'] == 'massive' for entry in result['datasets'].values())
    assert result['datasets']['options']['status'] == 'ok'


def test_shared_rate_limit_coordinates_independent_clients(tmp_path, monkeypatch):
    monkeypatch.setenv('MASSIVE_RATE_STATE', str(tmp_path/'rate.sqlite'))
    clock = [100.]
    monkeypatch.setattr(massive.time, 'time', lambda: clock[0])
    def sleep(delay):
        clock[0] += delay
    clients = [massive._RequestLimiter(sleep=sleep), massive._RequestLimiter(sleep=sleep)]
    starts = []
    for client in (clients[0], clients[1], clients[0]):
        client.wait()
        starts.append(clock[0])
    assert starts == [100., 113., 126.]


def test_changed_backfill_model_does_not_truncate_saved_output(tmp_path, monkeypatch):
    from data_collection.__main__ import main
    monkeypatch.setenv('MASSIVE_API_KEY', 'fake-secret')
    output = tmp_path/'stocks.jsonl'
    output.write_text('preserve me\n')
    with Checkpoints(str(output)+'.checkpoint.sqlite') as cache:
        cache.bind_window('2025-10-20', '2025-10-21')
        cache.bind_options(OptionSettings(history_days=-1).dictionary())
    assert main(['AAPL', '--start', '2025-10-20', '--end', '2025-10-21',
                 '--output', str(output), '--risk-free-rate', '.05',
                 '--log-file', str(tmp_path/'run.log')], backfill=True) == 1
    assert output.read_text() == 'preserve me\n'


def test_backfill_requires_dates_before_any_request(monkeypatch, tmp_path):
    from data_collection.__main__ import main
    monkeypatch.setattr(massive, 'request_json', lambda *a, **kw: pytest.fail('Missing dates must not fetch'))
    with pytest.raises(SystemExit) as failure:
        main(['AAPL', '--output', str(tmp_path/'stocks.jsonl')], backfill=True)
    assert failure.value.code == 2
