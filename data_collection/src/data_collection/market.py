"""Free Massive stock/option bars, dated contracts and completed-response caching."""

from __future__ import annotations

import json
from datetime import date, datetime
from urllib.parse import quote
from zoneinfo import ZoneInfo

from . import massive

NEW_YORK = ZoneInfo("America/New_York")


def bar_date(bar: dict) -> date:
    return datetime.fromtimestamp(float(bar["t"]) / 1000, NEW_YORK).date()


def records(path, params, *, api_key=None, checkpoint=None):
    """Cache a complete paginated response, never a partial response or credentials."""
    key = json.dumps([path, params], sort_keys=True)
    saved = checkpoint.response(key) if checkpoint else None
    if saved is not None:
        return saved
    result = massive.fetch_records(path, params, api_key=api_key)
    if checkpoint and result:
        checkpoint.save_response(key, result)
    return result


def stock_bars(ticker: str, start: date, end: date, *, adjusted=True, api_key=None, checkpoint=None):
    rows = records(
        f"/v2/aggs/ticker/{quote(ticker, safe='')}/range/1/day/{start}/{end}",
        {"adjusted": str(adjusted).lower(), "sort": "asc", "limit": 50000},
        api_key=api_key, checkpoint=checkpoint,
    )
    # Retain original Massive fields alongside readable aliases. This is split
    # adjustment only: never label these prices as dividend-adjusted Adj Close.
    result = []
    seen = set()
    for row in rows:
        day = bar_date(row)
        if not start <= day <= end:
            continue
        if day in seen:
            raise massive.MassiveAPIError("Massive returned duplicate stock price dates")
        seen.add(day)
        result.append(dict(row, date=day.isoformat(), Open=row.get("o"), High=row.get("h"),
                           Low=row.get("l"), Close=row.get("c"), Volume=row.get("v"),
                           VWAP=row.get("vw"), split_adjusted=adjusted))
    return sorted(result, key=lambda row: row["date"])


def option_contracts(ticker, day, minimum_expiry, maximum_expiry, spot, *, api_key=None, checkpoint=None):
    # expired is evaluated relative to as_of, not the collection date. These
    # contracts must still be live on the historical valuation date.
    width = max(spot * 0.10, 5.0)
    params = {
        "underlying_ticker": ticker, "as_of": day.isoformat(), "expired": "false",
        "expiration_date.gte": minimum_expiry.isoformat(), "expiration_date.lte": maximum_expiry.isoformat(),
        "strike_price.gte": max(0, spot - width), "strike_price.lte": spot + width,
        "sort": "expiration_date", "order": "asc", "limit": 1000,
    }
    return records("/v3/reference/options/contracts", params, api_key=api_key, checkpoint=checkpoint)


def option_bars(ticker, start, end, *, api_key=None, checkpoint=None):
    return records(
        f"/v2/aggs/ticker/{quote(ticker, safe='')}/range/1/day/{start}/{end}",
        {"adjusted": "false", "sort": "asc", "limit": 50000},
        api_key=api_key, checkpoint=checkpoint,
    )


def bulk_price_history(tickers, start, end, *, warmup_start=None, api_key=None, cache=None, log=None):
    """One market-wide request per session/adjustment, rather than per ticker.

    Cache only the requested universe. A different universe gets a different key,
    so a cached subset cannot masquerade as complete coverage of another list.
    """
    universe = sorted(set(tickers))
    wanted = set(universe)
    result = {ticker: {"adjusted": [], "raw": []} for ticker in universe}
    first = min(start, warmup_start) if warmup_start else start
    day = first
    from datetime import timedelta
    while day <= end:
        if day.weekday() < 5:
            for adjusted in (True, False) if warmup_start else (True,):
                key = json.dumps(["grouped-v2", day.isoformat(), adjusted, universe])
                saved = cache.response(key) if cache else None
                if saved is None:
                    path = f"/v2/aggs/grouped/locale/us/market/stocks/{day}"
                    payload = massive.request_json(path, {"adjusted": str(adjusted).lower()}, api_key=api_key)
                    if not isinstance(payload.get("results", []), list) or any(
                        not isinstance(row, dict) for row in payload.get("results", [])
                    ):
                        raise massive.MassiveAPIError("Invalid Massive grouped price response")
                    saved = [row for row in payload.get("results", []) if row.get("T") in wanted]
                    if cache and saved:
                        cache.save_response(key, saved)
                    if log:
                        log.info("BULK prices date=%s adjusted=%s tickers=%s", day, adjusted, len(saved))
                seen = set()
                for row in saved:
                    ticker = row["T"]
                    if ticker in seen or bar_date(row) != day:
                        raise massive.MassiveAPIError("Invalid or duplicate Massive grouped price date")
                    seen.add(ticker)
                    result[ticker]["adjusted" if adjusted else "raw"].append(
                        dict(row, date=day.isoformat(), Open=row.get("o"), High=row.get("h"),
                             Low=row.get("l"), Close=row.get("c"), Volume=row.get("v"),
                             VWAP=row.get("vw"), split_adjusted=adjusted))
        day += timedelta(days=1)
    return result
