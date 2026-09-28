"""News attention and sentiment analyses for loaded equity tickers."""

from datetime import date, datetime, timedelta, timezone
from typing import TYPE_CHECKING
from urllib.parse import urlsplit, urlunsplit

import polars as pl

from ats.analysis_utils import _anomaly, _date

if TYPE_CHECKING:
    from ats.ticker import EquityTicker


def _news_and_sentiment_trend_records(records, ticker, *, start_date, end_date):
    """Daily unique-article counts and sentiment on [-1, 1] within known coverage.

    Deduplicate by provider ID, canonical article URL, or normalized title on
    the same UTC date. Unlabeled articles count toward attention, not sentiment.
    Bounds must describe a completely fetched range; zero means no articles
    were returned for that day, not that no news existed anywhere.
    """
    if records is None:
        raise ValueError("Fetch news records before calculating this analysis")
    start, end = _date(start_date), _date(end_date)
    if start > end:
        raise ValueError("start_date must not be after end_date")
    ticker = ticker.strip().upper()
    daily = {}
    seen = set()
    for record in sorted(records, key=lambda r: r["published_utc"]):
        stamp = datetime.fromisoformat(record["published_utc"].replace("Z", "+00:00"))
        day = stamp.astimezone(timezone.utc).date() if stamp.tzinfo else stamp.date()
        if not start <= day <= end:
            continue
        keys = []
        if record.get("id"):
            keys.append(("id", record["id"]))
        if record.get("article_url"):
            url = urlsplit(record["article_url"])
            keys.append(("url", urlunsplit((url.scheme.lower(), url.netloc.lower(), url.path.rstrip("/"), url.query, ""))))
        if record.get("title"):
            keys.append(("title", day, " ".join(record["title"].casefold().split())))
        duplicate = any(key in seen for key in keys)
        seen.update(keys)
        if duplicate:
            continue
        counts = daily.setdefault(day, {"article_count": 0, "positive": 0, "negative": 0, "neutral": 0, "unlabeled": 0})
        counts["article_count"] += 1
        insights = record.get("ticker_insights", record.get("insights")) or []
        labels = {insight.get("sentiment") for insight in insights if insight.get("ticker", "").upper() == ticker}
        label = next(iter(labels)) if len(labels) == 1 else None
        counts[label if label in {"positive", "negative", "neutral"} else "unlabeled"] += 1
    rows = []
    for offset in range((end - start).days + 1):
        day = start + timedelta(days=offset)
        counts = daily.get(day, {"article_count": 0, "positive": 0, "negative": 0, "neutral": 0, "unlabeled": 0})
        labeled = counts["positive"] + counts["negative"] + counts["neutral"]
        rows.append({"date": day, **counts, "labeled_count": labeled,
                     "sentiment_score": (counts["positive"] - counts["negative"]) / labeled if labeled else None})
    return pl.DataFrame(rows, schema={"date": pl.Date, **{key: pl.Int64 for key in (
        "article_count", "positive", "negative", "neutral", "unlabeled", "labeled_count"
    )}, "sentiment_score": pl.Float64})


def _news_attention_anomaly_records(records, ticker, *, start_date, end_date, recent_window=7, baseline_window=60):
    """Recent daily article count versus preceding baseline calendar days."""
    daily = _news_and_sentiment_trend_records(records, ticker, start_date=start_date, end_date=end_date)
    return _anomaly(list(zip(daily["date"].to_list(), daily["article_count"].to_list())),
                    recent_window, baseline_window).rename({"value": "article_count"})


def _news_bounds(ticker: "EquityTicker", start_date, end_date) -> tuple[date, date]:
    start = _date(start_date) if start_date is not None else None
    end = _date(end_date) if end_date is not None else None
    if ticker.news_coverage is not None:
        fetched_start, fetched_end = ticker.news_coverage
        start = start if start is not None else fetched_start
        end = end if end is not None else fetched_end
        if start < fetched_start or end > fetched_end:
            raise ValueError("Requested news analysis extends beyond the fetched date range")
    if start is None or end is None:
        raise ValueError("Load news first or supply both start_date and end_date for loaded records")
    return start, end


def news_and_sentiment_trend(
    ticker: "EquityTicker", *, start_date=None, end_date=None
) -> pl.DataFrame:
    start, end = _news_bounds(ticker, start_date, end_date)
    return _news_and_sentiment_trend_records(
        ticker.news_sentiment, ticker.ticker, start_date=start, end_date=end
    )


def news_attention_anomaly(
    ticker: "EquityTicker", recent_window: int = 7, baseline_window: int = 60,
    *, start_date=None, end_date=None,
) -> pl.DataFrame:
    start, end = _news_bounds(ticker, start_date, end_date)
    return _news_attention_anomaly_records(
        ticker.news_sentiment, ticker.ticker, start_date=start, end_date=end,
        recent_window=recent_window, baseline_window=baseline_window,
    )
