"""Separate, resumable ThetaData EOD options backfill and daily panel export."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
import json
import logging
import os
from pathlib import Path
import sqlite3
import sys

from dotenv import load_dotenv
import numpy as np
import pandas as pd

from . import market, massive
from .__main__ import _read_tickers
from .checkpoints import Checkpoints
from .collector import _logger, _symbol
from .option_metrics import OptionSettings, _number, implied_volatility, rolling_realized_volatility
from .theta import ThetaAPIError, ThetaEOD, resolve_api_key


def month_windows(start, end):
    """Inclusive calendar-month chunks, including partial first/last months."""
    while start <= end:
        following = (start.replace(day=28) + timedelta(days=4)).replace(day=1)
        last = min(end, following - timedelta(days=1))
        yield start, last
        start = last + timedelta(days=1)


def _session(value):
    return pd.to_datetime(value, utc=True).tz_convert("America/New_York").date().isoformat()


def _dated(rows):
    result = {}
    for row in rows:
        day = _session(row["created"])
        if day in result:
            raise ValueError("Duplicate Theta stock sessions")
        result[day] = row
    return result


def daily_metrics(ticker, start, end, stocks, options, *, settings, adjusted_prices=None):
    """One numeric IV per ticker/session; never carry a prior session's quote.

    All IVs are solved in one NumPy/SciPy batch for this month. Quote volume
    need not be positive: we use valid same-report bid/ask midpoints, not trades.
    """
    nominal = _dated(stocks)
    by_day = {}
    for record in options:
        if record.get("symbol", ticker) != ticker:
            continue  # Do not mix a separately named adjusted option root.
        by_day.setdefault(_session(record["created"]), []).append(record)
    rv = {}
    if adjusted_prices is not None:
        dates = [row["date"] for row in adjusted_prices]
        if dates != sorted(set(dates)):
            raise ValueError("Adjusted prices must have unique ordered sessions")
        values = rolling_realized_volatility([row.get("Close") for row in adjusted_prices], settings.rv_window)
        rv = {day: _number(value) for day, value in zip(dates, values)}
    sessions = sorted(day for day in set(nominal) | set(by_day) | set(rv) if str(start) <= day <= str(end))
    result, solves = [], []
    for iso in sessions:
        day = date.fromisoformat(iso)
        stock = nominal.get(iso, {})
        spot = _number(stock.get("close"))
        row = dict(ticker=ticker, date=iso, status="unavailable", reason=None,
                   options_provider="thetadata", underlying_price_provider="thetadata",
                   option_price_basis="eod_nbbo_midpoint", model="black_scholes_european",
                   underlying_price=spot, stock_report_created=stock.get("created"),
                   stock_last_trade=stock.get("last_trade"),
                   risk_free_rate=settings.risk_free_rate, dividend_yield=settings.dividend_yield,
                   rv_window=settings.rv_window, realized_volatility=rv.get(iso),
                   realized_volatility_price_source="massive_split_adjusted" if adjusted_prices is not None else None,
                   call_implied_volatility=None, put_implied_volatility=None,
                   implied_volatility=None, implied_risk_premium=None, variance_spread=None,
                   premium_status="unavailable" if adjusted_prices is not None else "not_requested",
                   contract_deliverable_verified=False)
        result.append(row)
        if spot is None or spot <= 0:
            row["reason"] = "missing_same_session_underlying_close"
            continue
        candidates = {}
        for record in by_day.get(iso, []):
            expiry = pd.to_datetime(record["expiration"]).date()
            strike = _number(record["strike"])
            side = str(record["right"]).lower()
            side = {"c": "call", "p": "put"}.get(side, side)
            if side not in {"call", "put"} or strike is None or strike <= 0:
                continue
            if not settings.target_days <= (expiry - day).days <= settings.target_days + 45:
                continue
            bid, ask = _number(record.get("bid")), _number(record.get("ask"))
            if bid is None or ask is None or not (0 < bid <= ask):
                continue
            pair = candidates.setdefault((expiry, strike), {})
            if side in pair:
                raise ValueError("Duplicate Theta option contract/session")
            pair[side] = record
        pairs = [(key, pair) for key, pair in candidates.items() if set(pair) == {"call", "put"}]
        if not pairs:
            row["reason"] = "no_valid_same_session_call_put_quotes"
            continue
        (expiry, strike), pair = min(pairs, key=lambda item: (item[0][0], abs(item[0][1] - spot), item[0][1]))
        row.update(expiration_date=str(expiry), strike=strike, days_to_expiration=(expiry - day).days)
        for side in ("call", "put"):
            quote = pair[side]
            midpoint = (float(quote["bid"]) + float(quote["ask"])) / 2
            for field in ("bid", "ask", "volume", "bid_size", "ask_size", "bid_condition", "ask_condition"):
                row[side + "_" + field] = _number(quote.get(field))
            row[side + "_price"] = midpoint
            row[side + "_report_created"] = quote["created"]
            row[side + "_last_trade"] = quote.get("last_trade")
            solves.append((row, side, midpoint, spot, strike, (expiry - day).days / 365.))
    if solves:
        _, sides, prices, spots, strikes, years = zip(*solves)
        values = implied_volatility(prices, spots, strikes, years, settings.risk_free_rate,
                                    settings.dividend_yield, np.asarray(sides) == "call")
        for (row, side, *_), value in zip(solves, values):
            row[side + "_implied_volatility"] = _number(value)
    for row in result:
        call, put = row["call_implied_volatility"], row["put_implied_volatility"]
        if call is None or put is None:
            row["reason"] = row["reason"] or "option_price_outside_black_scholes_bounds"
            continue
        iv = (call + put) / 2
        row.update(implied_volatility=iv, status="ok", reason=None)
        realized = row["realized_volatility"]
        if adjusted_prices is not None:
            if realized is None:
                row.update(status="partial", reason="insufficient_realized_volatility_history")
            else:
                row.update(implied_risk_premium=iv - realized,
                           variance_spread=iv * iv - realized * realized, premium_status="ok")
    return result


def _theta_response(client, cache, kind, ticker, start, end, settings, strike_range, log):
    params = dict(max_dte=settings.target_days + 45, strike_range=strike_range)
    key = json.dumps(["theta-sdk-1.0.15-eod-v1", kind, ticker, str(start), str(end), params], sort_keys=True)
    saved = cache.response(key)
    if saved is not None:
        log.info("RESUME ticker=%s feed=%s window=%s..%s", ticker, kind, start, end)
        return saved
    try:
        rows = client.history(kind, ticker, start, end, **params)
    except ThetaAPIError as exc:
        if exc.code != "DEADLINE_EXCEEDED" or start == end:
            raise
        middle = start + (end - start) // 2
        log.warning("SPLIT ticker=%s feed=%s window=%s..%s", ticker, kind, start, end)
        rows = _theta_response(client, cache, kind, ticker, start, middle, settings, strike_range, log)
        rows += _theta_response(client, cache, kind, ticker, middle + timedelta(days=1), end, settings, strike_range, log)
    cache.save_response(key, rows)  # Complete empty responses are also durable.
    log.info("FETCH ticker=%s feed=%s window=%s..%s records=%s", ticker, kind, start, end, len(rows))
    return rows


def collect_month(client, cache, ticker, start, end, *, settings, strike_range=2,
                  adjusted_prices=None, log):
    dataset = f"theta_options:{start}:{end}"
    saved = cache.load(ticker, dataset)
    if saved is not None:
        log.info("RESUME ticker=%s feed=metrics window=%s..%s", ticker, start, end)
        return saved["data"]
    stocks = _theta_response(client, cache, "stock", ticker, start, end, settings, strike_range, log)
    options = _theta_response(client, cache, "option", ticker, start, end, settings, strike_range, log)
    rows = daily_metrics(ticker, start, end, stocks, options, settings=settings, adjusted_prices=adjusted_prices)
    for row in rows:
        row["retrieved_at"] = datetime.now(timezone.utc).isoformat()
        if row["status"] != "ok":
            log.warning("UNAVAILABLE ticker=%s date=%s reason=%s", ticker, row["date"], row["reason"])
    if not rows:
        log.warning("EMPTY ticker=%s window=%s..%s", ticker, start, end)
    # 'ok' means retrieval/computation finished, not that every metric exists.
    # Unavailable daily values remain explicit nulls and are not retried forever.
    cache.save(ticker, dataset, {"status": "ok" if rows else "empty", "data": rows})
    return rows


@contextmanager
def _writer_lock(path):
    import fcntl
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError("Another worker owns this output/checkpoint") from None
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Separate ThetaData options daily panel/backfill")
    parser.add_argument("tickers", nargs="*")
    parser.add_argument("--tickers-file", type=Path)
    parser.add_argument("--start", required=True, type=date.fromisoformat)
    parser.add_argument("--end", required=True, type=date.fromisoformat)
    parser.add_argument("--output", required=True, type=Path, help="Daily JSONL, one row per ticker/session")
    parser.add_argument("--checkpoint", type=Path)
    parser.add_argument("--env-file", type=Path)
    parser.add_argument("--api-key-file", type=Path, help="ThetaData secret file, overrides environment")
    parser.add_argument("--massive-api-key-file", type=Path)
    parser.add_argument("--iv-only", action="store_true", help="ThetaData only; omit RV and risk premium")
    parser.add_argument("--option-target-days", type=int, default=30)
    parser.add_argument("--strike-range", type=int, default=2, help="ATM strikes on either side requested from ThetaData")
    parser.add_argument("--rv-window", type=int, default=21)
    parser.add_argument("--risk-free-rate", type=float, default=.04)
    parser.add_argument("--dividend-yield", type=float, default=0.)
    parser.add_argument("--request-interval", type=float, default=3.2, help="At least 3.2 seconds between Theta request starts")
    parser.add_argument("--request-timeout", type=float, default=120.)
    parser.add_argument("--log-file", type=Path)
    parser.add_argument("--log-to-stderr", action="store_true")
    args = parser.parse_args(argv)
    if args.env_file:
        load_dotenv(args.env_file)
    checkpoint_path = args.checkpoint or Path(str(args.output) + ".checkpoint.sqlite")
    log_path = args.log_file or Path(str(args.output) + ".log")
    paths = [p.resolve() for p in (args.output, checkpoint_path, log_path)]
    if len(set(paths)) != len(paths):
        parser.error("Output, checkpoint and log must be different files")
    inputs = [p.resolve() for p in (args.tickers_file, args.env_file, args.api_key_file,
                                   args.massive_api_key_file) if p is not None]
    if set(paths) & set(inputs):
        parser.error("Output, checkpoint and log must not overwrite input or credential files")
    log = _logger(log_path)
    console = logging.StreamHandler(sys.stderr) if args.log_to_stderr else None
    if console:
        console.setFormatter(log.handlers[0].formatter)
        log.addHandler(console)
    failed = False
    try:
        tickers = args.tickers + (_read_tickers(args.tickers_file) if args.tickers_file else [])
        tickers = list(dict.fromkeys(_symbol(ticker) for ticker in tickers))
        if not tickers or args.start > args.end or args.strike_range < 1:
            raise ValueError("Supply tickers, an ordered date range and a positive strike range")
        if args.end >= datetime.now(market.NEW_YORK).date():
            raise ValueError("Use completed sessions: END must be before today in New York")
        settings = OptionSettings(-1, args.option_target_days, args.rv_window,
                                  args.risk_free_rate, args.dividend_yield).validate()
        key = args.api_key_file.read_text().strip() if args.api_key_file else resolve_api_key()
        if not key:
            raise ValueError("Supply a ThetaData API key")
        massive_key = args.massive_api_key_file.read_text().strip() if args.massive_api_key_file else massive.resolve_api_key()
        if args.massive_api_key_file and not massive_key:
            raise ValueError("Massive API key file is empty")
        if not args.iv_only and not massive_key:
            raise ValueError("RV/premium requires a Massive key; use --iv-only for ThetaData alone")
        # Both per-host paths may be shared by other daily/backfill jobs.
        os.environ.setdefault("THETADATA_RATE_STATE", str(args.output.parent / "cache/theta-rate.sqlite"))
        os.environ.setdefault("MASSIVE_RATE_STATE", str(args.output.parent / "cache/massive-rate.sqlite"))
        client = ThetaEOD(key, interval=args.request_interval, timeout=args.request_timeout, log=log)
        if args.output.exists() and not checkpoint_path.exists():
            raise ValueError("Existing output has no checkpoint; choose a new output path")
        with _writer_lock(Path(str(checkpoint_path) + ".lock")), _writer_lock(Path(str(args.output) + ".lock")), \
                Checkpoints(checkpoint_path, provider="thetadata_options_v1") as cache:
            cache.bind_window(str(args.start), str(args.end))
            cache.bind_run(dict(tickers=tickers, settings=settings.dictionary(), strike_range=args.strike_range,
                                iv_only=args.iv_only, price_basis="eod_nbbo_midpoint", sdk="1.0.15",
                                theta_environment={name: os.environ.get(name) for name in (
                                    "THETADATA_MDDS_TYPE", "THETADATA_MDDS_HOST", "THETADATA_MDDS_PORT")},
                                massive_origin=None if args.iv_only else massive.base_url()))
            args.output.parent.mkdir(parents=True, exist_ok=True)
            log.info("RUN ThetaData tickers=%s window=%s..%s iv_only=%s", len(tickers), args.start, args.end, args.iv_only)
            # Rebuild only after validation. SQLite is authoritative if interrupted.
            with args.output.open("w", encoding="utf-8") as stream:
                for ticker in tickers:
                    adjusted = None
                    if not args.iv_only:
                        warmup = args.start - timedelta(days=settings.rv_window * 3 + 10)
                        try:
                            adjusted = market.stock_bars(ticker, warmup, args.end, api_key=massive_key, checkpoint=cache)
                            if not adjusted:
                                raise ValueError("No split-adjusted price history")
                        except (OSError, sqlite3.Error):
                            raise  # Stop on state/disk failure before making more API calls.
                        except Exception as exc:
                            log.error("ERROR ticker=%s feed=adjusted_prices type=%s", ticker, type(exc).__name__)
                            failed = True
                            continue
                    for start, end in month_windows(args.start, args.end):
                        try:
                            rows = collect_month(client, cache, ticker, start, end, settings=settings,
                                                 strike_range=args.strike_range, adjusted_prices=adjusted, log=log)
                        except (OSError, sqlite3.Error):
                            raise
                        except Exception as exc:
                            if isinstance(exc, ThetaAPIError) and exc.code in {"INSTALL_THETA_EXTRA", "AUTHENTICATION_FAILED"}:
                                raise  # A missing SDK or invalid key affects the whole job.
                            code = exc.code if isinstance(exc, ThetaAPIError) else type(exc).__name__
                            log.error("ERROR ticker=%s window=%s..%s code=%s; rerun to retry", ticker, start, end, code)
                            failed = True
                            continue
                        for row in rows:
                            stream.write(json.dumps(row, allow_nan=False) + "\n")
                            failed |= row["status"] != "ok"
                        stream.flush()
            log.info("RUN finished failed_or_unavailable=%s output=%s", failed, args.output)
    except KeyboardInterrupt:
        log.warning("RUN interrupted; rerun the same command to resume completed chunks")
        return 130
    except Exception as exc:
        code = exc.code if isinstance(exc, ThetaAPIError) else type(exc).__name__
        log.error("RUN stopped code=%s; checkpoints retained", code)
        print(f"ThetaData pipeline stopped ({code}); see {log_path}", file=sys.stderr)
        return 1
    finally:
        if console:
            log.removeHandler(console)
            console.close()
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())
