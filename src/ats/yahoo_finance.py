from __future__ import annotations

import os
import random
import time
import math
from datetime import date
from pathlib import Path
from typing import Tuple

import polars as pl
import yfinance as yf


# Date helpers as provided


def today_yyyymmdd(sep=""):
    return date.today().strftime(f"%Y{sep}%m{sep}%d")

def one_year_ago_yyyymmdd(sep=""):
    return (date.today().replace(year=date.today().year - 1)).strftime(f"%Y{sep}%m{sep}%d")


def _record_rogue_ticker(
    workspace_path: str | os.PathLike[str], ticker: str, reason: str
) -> None:
    """Append a line with ticker and reason to a rogue tickers file in workspace."""
    try:
        ws = Path(workspace_path)
        ws.mkdir(parents=True, exist_ok=True)
        out_file = ws / "rogue_tickers.txt"
        timestamp = date.today().isoformat()
        line = f"{timestamp}\t{ticker}\t{reason.strip()}\n"
        with out_file.open("a", encoding="utf-8") as f:
            f.write(line)
    except Exception:
        # Swallow any file I/O errors to keep function robust
        pass


def fetch_price_data(
    ticker: str,
    cooldown_range: Tuple[float, float] | None = (0.5, 1.5),
    start = None,
    end = None,
    all_available_price_history: bool = False,
) -> pl.DataFrame:
    """
    Fetch historical price data for a ticker via yfinance and return as a Polars DataFrame.

    - Defaults to one year of data using provided date helpers for start/end bounds.
    - If all_available_price_history is True, fetches Yahoo's maximum available history.
    - Adds a random cooldown to avoid spamming the API.

    Parameters
    - ticker: The security symbol (e.g., "AAPL").
    - cooldown_range: Optional (min_seconds, max_seconds) to sleep before the request.
    - start: Optional start date. If provided, it takes precedence over all_available_price_history.
    - end: Optional end date.
    - all_available_price_history: Fetch all available Yahoo history when True.

    Returns
    - A Polars DataFrame with the historical prices. Returns empty DataFrame on failure.
    """

    tkr = (ticker or "").strip()
    if not tkr:
        return pl.DataFrame()

    # Cooldown before hitting the API
    if (
        cooldown_range
        and cooldown_range[0] >= 0
        and cooldown_range[1] >= cooldown_range[0]
    ):
        try:
            time.sleep(random.uniform(*cooldown_range))
        except Exception:
            # Ignore sleep errors
            pass

    if not all_available_price_history and not start:
        start = one_year_ago_yyyymmdd(sep="-")
    if not all_available_price_history and not end:
        end = today_yyyymmdd(sep="-")

    try:
        download_args = {
            "progress": False,
            "auto_adjust": False,
            "threads": False,
            "group_by": "ticker",
        }
        if all_available_price_history and not start:
            download_args["period"] = "max"
            if end:
                download_args["end"] = end
        else:
            download_args["start"] = start
            download_args["end"] = end

        # yfinance returns a pandas DataFrame; suppress progress/threads for determinism
        pdf = yf.download(tkr, **download_args)

        # nothing returned
        if pdf is None or getattr(pdf, "empty", True):
            return pl.DataFrame()

        if hasattr(pdf, "reset_index"):
            pdf = pdf.reset_index()

        pldf = pl.from_pandas(pdf)
        if pldf.height == 0:
            return pl.DataFrame()

        # rename columns to standard names
        # the DataFrame may contain multi-index tuples; convert them to strings
        renames: dict[str, str] = {}
        for col in pldf.columns:
            cstr = str(col)
            if "Date" in cstr:
                renames[col] = "date"
            elif "Adj Close" in cstr:
                renames[col] = "close"
        if "date" not in renames.values() and pldf.columns:
            renames[pldf.columns[0]] = "date"
        if renames:
            pldf = pldf.rename(renames)

        # make sure the two required columns exist
        if not {"date", "close"}.issubset(pldf.columns):
            return pl.DataFrame()

        pldf = pldf.select(["date", "close"]).with_columns(
            pl.col("date").cast(pl.Date),
            pl.lit(tkr).alias("ticker"),
        )
        return pldf
    except Exception:
        return pl.DataFrame()


def fetch_beta(
    ticker: str,
    workspace_path: str | os.PathLike[str],
    cooldown_range: Tuple[float, float] | None = (0.2, 0.6),
) -> float | None:
    """
    Fetch a ticker's beta from Yahoo Finance via yfinance.

    Tries multiple metadata sources (`get_info()`, then `fast_info`) and
    returns the first valid numeric beta found. Records the ticker and reason
    to `rogue_tickers.txt` under `workspace_path` when unavailable or on error.

    Parameters
    - ticker: Symbol to query (e.g., "AAPL").
    - workspace_path: Directory for logging rogue tickers.
    - cooldown_range: Optional (min, max) seconds to sleep before the request.

    Returns
    - float beta if found, otherwise None.
    """
    tkr = (ticker or "").strip()
    if not tkr:
        _record_rogue_ticker(workspace_path, ticker="", reason="Empty ticker for beta")
        return None

    # Gentle cooldown to avoid hammering endpoints
    if (
        cooldown_range
        and cooldown_range[0] >= 0
        and cooldown_range[1] >= cooldown_range[0]
    ):
        try:
            time.sleep(random.uniform(*cooldown_range))
        except Exception:
            pass

    def _is_valid_numeric(x) -> bool:
        try:
            val = float(x)
            return math.isfinite(val)
        except Exception:
            return False

    def _coerce_float(x) -> float | None:
        try:
            v = float(x)
            return v if math.isfinite(v) else None
        except Exception:
            return None

    try:
        tk = yf.Ticker(tkr)

        # Preferred: get_info (dict) — broader coverage
        info = None
        try:
            info = tk.get_info()
        except Exception:
            info = None

        if isinstance(info, dict) and info:
            # Try common beta keys in order of desirability
            for key in (
                "beta",
                "beta3Year",
                "beta_3y",
                "beta_5y_monthly",
                "beta5Year",
                "beta_5y",
            ):
                if key in info and _is_valid_numeric(info[key]):
                    return _coerce_float(info[key])

        # Fallback: fast_info if available and contains beta
        try:
            fast_info = getattr(tk, "fast_info", None)
            if fast_info:
                if isinstance(fast_info, dict) and "beta" in fast_info and _is_valid_numeric(
                    fast_info["beta"]
                ):
                    return _coerce_float(fast_info["beta"]) 
                beta_attr = getattr(fast_info, "beta", None)
                if beta_attr is not None and _is_valid_numeric(beta_attr):
                    return _coerce_float(beta_attr)
        except Exception:
            pass

        _record_rogue_ticker(workspace_path, tkr, "Beta not found in yfinance metadata")
        return None
    except Exception as e:
        _record_rogue_ticker(workspace_path, tkr, f"Beta fetch error: {e}")
        return None
