"""Collect serializable source dictionaries using yfinance and a local Massive client.

No requests run at import time. Massive pagination/retries share a process-wide
13-second limiter. Run one collector process per API allowance.
"""

from __future__ import annotations

import json
import logging
import re
import time
from collections.abc import Iterable, Iterator
from contextlib import ExitStack
from datetime import date, datetime, timedelta, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any
from urllib.parse import quote

import pandas as pd
import yfinance as yf

from . import massive
from .checkpoints import Checkpoints

DEFAULT_LOG_PATH = Path("logs/data_collection.log")


def _logger(path: str | Path) -> logging.Logger:
    path = Path(path).resolve()
    logger = logging.getLogger(f"data_collection:{path}")
    if not logger.handlers:
        path.parent.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(path, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8")
        formatter = logging.Formatter("%(asctime)sZ %(levelname)s %(message)s", datefmt="%Y-%m-%dT%H:%M:%S")
        formatter.converter = time.gmtime
        handler.setFormatter(formatter)
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        logger.propagate = False
    return logger

DEFAULT_DATASETS = (
    "prices", "cash_flow", "analyst_actions", "earnings",
    "reference", "short_interest", "float", "short_volume", "news",
    "insider_transactions", "options",
)
YAHOO_DATASETS = {
    "prices", "cash_flow", "income_statement", "balance_sheet",
    "analyst_actions", "earnings", "options",
}
MASSIVE_DATASETS = {
    "reference", "short_interest", "float", "short_volume", "news",
    "insider_transactions",
}
SNAPSHOT_DATASETS = {
    "reference", "float", "cash_flow", "income_statement", "balance_sheet",
    "earnings", "analyst_actions", "options",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _symbol(ticker: str) -> str:
    if not isinstance(ticker, str) or not re.fullmatch(r"[A-Z0-9^][A-Z0-9.^=\-]*", ticker.strip().upper()):
        raise ValueError("ticker must be a nonempty stock symbol, for example AAPL or BRK-B")
    return ticker.strip().upper()


def _window(start_date, end_date) -> tuple[date, date]:
    end = date.fromisoformat(end_date) if isinstance(end_date, str) else end_date
    end = end if end is not None else datetime.now(timezone.utc).date()
    start = date.fromisoformat(start_date) if isinstance(start_date, str) else start_date
    start = start if start is not None else end - timedelta(days=730)
    if not isinstance(start, date) or not isinstance(end, date) or start > end:
        raise ValueError("start_date and end_date must be dates with start_date <= end_date")
    return start, end


def _checkpoint_window(start_date, end_date, checkpoint: Checkpoints | None) -> tuple[date, date]:
    saved = checkpoint.window() if checkpoint else None
    if saved:
        start_date = saved[0] if start_date is None else start_date
        end_date = saved[1] if end_date is None else end_date
    start, end = _window(start_date, end_date)
    if checkpoint:
        checkpoint.bind_window(start.isoformat(), end.isoformat())
    return start, end


def _datasets(datasets: Iterable[str] | None) -> tuple[str, ...]:
    if isinstance(datasets, str):
        raise ValueError("datasets must be a list of names, not one string")
    selected = tuple(dict.fromkeys(DEFAULT_DATASETS if datasets is None else datasets))
    unknown = set(selected) - YAHOO_DATASETS - MASSIVE_DATASETS
    if unknown:
        raise ValueError(f"Unknown datasets: {', '.join(sorted(unknown))}")
    return selected


def _records(frame: pd.DataFrame, index_name: str) -> list[dict]:
    """Preserve row labels/dates; pandas converts NaN/NaT to JSON null."""
    if frame is None:
        raise ValueError("Yahoo returned no table")
    return json.loads(frame.rename_axis(index_name).reset_index().to_json(
        orient="records", date_format="iso",
    ))


def _reference(ticker: str, api_key: str | None) -> dict:
    payload = massive.request_json(f"/v3/reference/tickers/{quote(ticker, safe='')}", api_key=api_key)
    result = payload.get("results")
    if not isinstance(result, dict):
        raise ValueError("Massive returned no reference object")
    return result


def _insiders(ticker: str, start: date, end: date, api_key: str | None) -> list[dict]:
    # Retain all codes and security types for later interpretation/amendment handling.
    # The provider's P/S/security-type filters have not proved reliable in probes.
    rows = massive.fetch_records("/stocks/filings/vX/form-4", {
        "tickers": ticker, "filing_date.gte": start.isoformat(),
        "filing_date.lte": end.isoformat(), "sort": "filing_date.desc", "limit": 10000,
    }, api_key=api_key)
    return [row for row in rows if ticker in (row.get("tickers") or [])]


def _options(stock: yf.Ticker) -> dict:
    """Current chains bracketing 30/90 calendar days, never historical chains."""
    available = sorted(stock.options)
    selected = set()
    today = datetime.now(timezone.utc).date()
    for days in (30, 90):
        target = (today + timedelta(days=days)).isoformat()
        below = [expiry for expiry in available if expiry <= target]
        above = [expiry for expiry in available if expiry >= target]
        selected.update(below[-1:] + above[:1])
    chains = {}
    for expiry in sorted(selected):
        # One request returns both sides; an expired/missing chain raises per-feed.
        chain = stock.option_chain(expiry)
        chains[expiry] = {
            "calls": _records(chain.calls, "row"),
            "puts": _records(chain.puts, "row"),
        }
    return {"available_expirations": available, "chains": chains}


def collect_ticker(
    ticker: str,
    *,
    start_date: date | str | None = None,
    end_date: date | str | None = None,
    datasets: Iterable[str] | None = None,
    api_key: str | None = None,
    checkpoint_path: str | Path | None = None,
    log_path: str | Path = DEFAULT_LOG_PATH,
) -> dict[str, Any]:
    """Collect one ticker, isolating errors per feed.

    Historical date bounds are inclusive, defaulting to the last 730 calendar
    days. They apply to prices, short interest/volume, news and insider filings.
    Other feeds are current snapshots, regardless of the requested date window.
    The result contains source inputs, not calculated factors or a PIT backtest.
    ``api_key`` defaults to ``MASSIVE_API_KEY`` (or legacy ``POLYGON_IO``).
    With ``checkpoint_path``, restart using the same file to reuse completed
    feeds and retry failed/unfinished ones. Omitted dates reuse the saved window.
    Empty feeds are complete checkpoints; use a new file to refresh snapshots.
    Events and sanitized errors are appended to ``log_path`` (rotated at 5 MiB).
    """
    if checkpoint_path and Path(checkpoint_path).resolve() == Path(log_path).resolve():
        raise ValueError("Checkpoint and log must use different files")
    log = _logger(log_path)
    try:
        with ExitStack() as stack:
            checkpoint = stack.enter_context(Checkpoints(checkpoint_path)) if checkpoint_path else None
            start, end = _checkpoint_window(start_date, end_date, checkpoint)
            return _collect_ticker(ticker, start, end, datasets, api_key, checkpoint, log)
    except KeyboardInterrupt:
        log.warning("Collection interrupted; committed checkpoints retained")
        raise
    except Exception as exc:
        log.error("Collection stopped type=%s; committed checkpoints retained", type(exc).__name__)
        raise


def _collect_ticker(ticker, start, end, datasets, api_key, checkpoint, log) -> dict[str, Any]:
    ticker = _symbol(ticker)
    selected = _datasets(datasets)
    # Wikipedia and Massive use BRK.B/BF.B; Yahoo uses BRK-B/BF-B.
    stock = yf.Ticker(ticker.replace(".", "-")) if set(selected) & YAHOO_DATASETS else None
    fetchers = {
        "prices": lambda: _records(stock.history(
            start=start.isoformat(), end=(end + timedelta(days=1)).isoformat(),
            interval="1d", auto_adjust=False, actions=True, raise_errors=True,
        ), "date"),
        "cash_flow": lambda: _records(stock.get_cash_flow(freq="quarterly").T, "period_end"),
        "income_statement": lambda: _records(stock.get_income_stmt(freq="quarterly").T, "period_end"),
        "balance_sheet": lambda: _records(stock.get_balance_sheet(freq="quarterly").T, "period_end"),
        "analyst_actions": lambda: _records(stock.get_upgrades_downgrades(), "published_at"),
        "earnings": lambda: _records(stock.get_earnings_history(), "period_end"),
        "options": lambda: _options(stock),
        "reference": lambda: _reference(ticker, api_key),
        "short_interest": lambda: massive.fetch_short_interest(ticker, start, end, api_key=api_key),
        "short_volume": lambda: massive.fetch_short_volume(ticker, start, end, api_key=api_key),
        "float": lambda: massive.fetch_float(ticker, api_key=api_key),
        "news": lambda: massive.fetch_news_sentiment(ticker, start, end, api_key=api_key),
        "insider_transactions": lambda: _insiders(ticker, start, end, api_key),
    }
    result = {
        "ticker": ticker, "started_at": _now(),
        "history_window": {"start_date": start.isoformat(), "end_date": end.isoformat()},
        "datasets": {},
    }
    for name in selected:
        cached = checkpoint.load(ticker, name) if checkpoint else None
        if cached is not None:
            cached["resumed_from_checkpoint"] = True
            result["datasets"][name] = cached
            log.info("RESUME ticker=%s feed=%s status=%s", ticker, name, cached["status"])
            continue
        entry = {
            "provider": "yahoo" if name in YAHOO_DATASETS else "massive",
            "scope": "current_snapshot" if name in SNAPSHOT_DATASETS else "history_window",
            "started_at": _now(),
            "resumed_from_checkpoint": False,
        }
        try:
            data = fetchers[name]()
            empty = not data or (name == "options" and not data["chains"])
            entry.update(status="empty" if empty else "ok", data=data)
        except Exception as exc:
            # Unknown exceptions may contain credential-bearing URLs or payloads.
            # Only the Massive helper guarantees sanitized messages.
            entry.update(status="error", data=None, error={
                "type": type(exc).__name__,
                "message": str(exc) if isinstance(exc, massive.MassiveAPIError) else "Collection failed; see error type",
            })
        entry["retrieved_at"] = _now()
        if entry["status"] == "error":
            log.error("UNAVAILABLE ticker=%s provider=%s feed=%s type=%s message=%s",
                      ticker, entry["provider"], name, entry["error"]["type"], entry["error"]["message"])
        elif entry["status"] == "empty":
            log.warning("EMPTY ticker=%s provider=%s feed=%s; provider returned no observations",
                        ticker, entry["provider"], name)
        else:
            log.info("COLLECTED ticker=%s provider=%s feed=%s", ticker, entry["provider"], name)
        # Commit outside the retrieval try block: a checkpoint write failure must
        # stop the run, rather than silently treating unsaved results as durable.
        if checkpoint:
            checkpoint.save(ticker, name, entry)
        result["datasets"][name] = entry
    result["finished_at"] = _now()
    return result


def iter_tickers(tickers: Iterable[str], **kwargs) -> Iterator[dict[str, Any]]:
    """Yield each completed dictionary sequentially, allowing immediate saving."""
    if isinstance(tickers, str):
        raise ValueError("Use collect_ticker for one symbol, or pass a list of tickers")
    symbols = list(dict.fromkeys(_symbol(ticker) for ticker in tickers))
    # Freeze both dates and any generator of dataset names for the whole run.
    start_date, end_date = kwargs.pop("start_date", None), kwargs.pop("end_date", None)
    with ExitStack() as stack:
        path = kwargs.get("checkpoint_path")
        checkpoint = stack.enter_context(Checkpoints(path)) if path else None
        start, end = _checkpoint_window(start_date, end_date, checkpoint)
    selected = _datasets(kwargs.pop("datasets", None))
    for ticker in symbols:
        yield collect_ticker(ticker, start_date=start, end_date=end, datasets=selected, **kwargs)


def collect_tickers(tickers: Iterable[str], **kwargs) -> dict[str, dict[str, Any]]:
    """Return {ticker: collection_dictionary}, preserving selection order."""
    return {result["ticker"]: result for result in iter_tickers(tickers, **kwargs)}
