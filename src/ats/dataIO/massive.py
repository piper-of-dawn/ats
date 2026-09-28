"""Sequential Massive access. The shared limiter coordinates this process only."""

from __future__ import annotations

import json
import os
import time
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from http.client import HTTPException
from threading import Lock
from typing import Any, Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


class MassiveAPIError(RuntimeError):
    """A Massive request failed; messages never include credentials or response bodies."""


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Do not forward the authorization header to a redirect destination.
        return None


class _RequestLimiter:
    def __init__(self, interval=13.0, clock=None, sleep=None):
        self.interval = interval
        self.clock = clock or time.monotonic
        self.sleep = sleep or time.sleep
        self.last_start = None

    def wait(self):
        if self.last_start is not None:
            remaining = self.interval - (self.clock() - self.last_start)
            while remaining > 0:
                self.sleep(remaining)
                remaining = self.interval - (self.clock() - self.last_start)
        self.last_start = self.clock()


_limiter = _RequestLimiter()
_request_lock = Lock()
_open = build_opener(_NoRedirect()).open


def _request_url(path: str, params: Mapping[str, Any] | None = None) -> str:
    base = urlsplit(os.environ.get("POLYGON_API_BASE_URL", "https://api.massive.com"))
    target = urlsplit(path)
    if not target.scheme and not target.netloc:
        target = urlsplit(urlunsplit((base.scheme, base.netloc, target.path, target.query, "")))
    try:
        same_origin = (
            target.scheme in {"http", "https"}
            and bool(target.hostname)
            and target.scheme == base.scheme
            and target.hostname == base.hostname
            and (target.port or (443 if target.scheme == "https" else 80))
            == (base.port or (443 if base.scheme == "https" else 80))
            and not target.username
            and not target.password
        )
    except ValueError:
        same_origin = False
    if not same_origin:
        raise MassiveAPIError("Massive URL must use the configured API origin")
    query = dict(parse_qsl(target.query, keep_blank_values=True))
    query.update({k: v for k, v in (params or {}).items() if v is not None})
    query = {k: v for k, v in query.items() if k.lower() != "apikey"}
    query = {k: str(v).lower() if isinstance(v, bool) else v for k, v in query.items()}
    return urlunsplit((target.scheme, target.netloc, target.path, urlencode(query), ""))


def _retry_delay(headers) -> float:
    value = headers.get("Retry-After") if headers else None
    if value:
        try:
            return max(60.0, float(value))
        except ValueError:
            try:
                return max(60.0, (parsedate_to_datetime(value) - datetime.now(timezone.utc)).total_seconds())
            except (TypeError, ValueError, OverflowError):
                pass
    return 60.0


def request_json(path: str, params: Mapping[str, Any] | None = None, *, api_key: str | None = None) -> dict:
    """GET JSON with a 30s timeout, shared pacing and at most three attempts.

    Absolute pagination URLs must have the configured origin. A legacy apiKey
    query parameter is accepted, but moved into the authorization header.
    """
    legacy_key = (params or {}).get("apiKey") or dict(parse_qsl(urlsplit(path).query)).get("apiKey")
    key = api_key or legacy_key or os.environ.get("POLYGON_IO")
    if not key:
        raise MassiveAPIError("Set POLYGON_IO or pass api_key for Massive requests")
    url = _request_url(path, params)
    request = Request(url, headers={"Accept": "application/json", "Authorization": f"Bearer {key}"})
    with _request_lock:
        for attempt in range(3):
            _limiter.wait()
            failure = None
            delay = 60.0
            try:
                with _open(request, timeout=30) as response:
                    payload = json.load(response)
            except HTTPError as exc:
                code = exc.code
                delay = _retry_delay(exc.headers)
                exc.close()
                failure = f"Massive HTTP {code}"
                if code != 429 and not 500 <= code < 600:
                    raise MassiveAPIError(failure) from None
            except (URLError, OSError, HTTPException):
                failure = "Massive connection failed or timed out"
            except (ValueError, UnicodeError):
                raise MassiveAPIError("Massive returned malformed JSON") from None
            if failure is None:
                if not isinstance(payload, dict):
                    raise MassiveAPIError("Massive JSON response must be an object")
                if payload.get("status") not in (None, "OK", "DELAYED") or payload.get("error"):
                    raise MassiveAPIError("Massive returned a provider error response")
                return payload
            if attempt == 2:
                raise MassiveAPIError(f"{failure}; exhausted three attempts") from None
            _limiter.sleep(delay)
    raise AssertionError("Unreachable")


def fetch_records(path: str, params: Mapping[str, Any] | None = None, *, api_key: str | None = None) -> list[dict]:
    """Fetch all pages sequentially, preserving provider records and dates."""
    records = []
    seen = set()
    while path:
        canonical_url = _request_url(path, params)
        if canonical_url in seen:
            raise MassiveAPIError("Massive returned a pagination cycle")
        seen.add(canonical_url)
        payload = request_json(path, params, api_key=api_key)
        rows = payload.get("results", [])
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            raise MassiveAPIError("Massive results must be a list of records")
        records.extend(rows)
        path = payload.get("next_url")
        if path is not None and not isinstance(path, str):
            raise MassiveAPIError("Massive next_url must be a URL string")
        params = None
    return records


def _date(value: date | str) -> date:
    return date.fromisoformat(value) if isinstance(value, str) else value


def _history_params(ticker, field, start_date, end_date):
    end = _date(end_date) if end_date is not None else date.today()
    params = {"ticker": ticker.strip().upper(), "sort": f"{field}.desc", f"{field}.lte": end.isoformat(), "limit": 1}
    if start_date is not None:
        start = _date(start_date)
        if start > end:
            raise ValueError("start_date must not be after end_date")
        params.update({f"{field}.gte": start.isoformat(), "limit": 1000})
    return params


def _short_records(path, ticker, field, start_date, end_date, api_key):
    params = _history_params(ticker, field, start_date, end_date)
    if start_date is None:
        # The API supplies next_url even with limit=1; latest-only must not follow it.
        payload = request_json(path, params, api_key=api_key)
        rows = payload.get("results", [])
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            raise MassiveAPIError("Massive results must be a list of records")
        return rows[:1]
    return fetch_records(path, params, api_key=api_key)


def fetch_short_interest(ticker: str, start_date=None, end_date=None, *, api_key=None) -> list[dict]:
    """Latest report, or all reports within inclusive dates when start_date is set."""
    return _short_records("/stocks/v1/short-interest", ticker, "settlement_date", start_date, end_date, api_key)


def fetch_short_volume(ticker: str, start_date=None, end_date=None, *, api_key=None) -> list[dict]:
    """Latest daily volume, or all daily records within inclusive date bounds."""
    return _short_records("/stocks/v1/short-volume", ticker, "date", start_date, end_date, api_key)


def fetch_float(ticker: str, *, api_key=None) -> list[dict]:
    """Return the latest float measurement with its provider effective date."""
    payload = request_json(
        "/stocks/vX/float", {"ticker": ticker.strip().upper(), "limit": 1}, api_key=api_key
    )
    rows = payload.get("results", [])
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise MassiveAPIError("Massive results must be a list of records")
    return rows[:1]


def fetch_news_sentiment(ticker: str, start_date=None, end_date=None, *, api_key=None) -> list[dict]:
    """Inclusive UTC date bounds; default to the preceding seven UTC dates."""
    ticker = ticker.strip().upper()
    end = _date(end_date) if end_date is not None else datetime.now(timezone.utc).date()
    start = _date(start_date) if start_date is not None else end - timedelta(days=6)
    if start > end:
        raise ValueError("start_date must not be after end_date")
    rows = fetch_records("/v2/reference/news", {
        "ticker": ticker, "published_utc.gte": start.isoformat(),
        "published_utc.lt": (end + timedelta(days=1)).isoformat(),
        "sort": "published_utc", "order": "desc", "limit": 1000,
    }, api_key=api_key)
    return [dict(row, ticker_insights=[insight for insight in (row.get("insights") or [])
                                     if insight.get("ticker") == ticker]) for row in rows]
