from __future__ import annotations

import json
import math
import os
import sys
import time
from collections import deque
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
from functools import wraps
from typing import Any
from urllib.error import HTTPError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

import numpy as np
from yfinance import Ticker

API_BASE_URL = os.environ.get("POLYGON_API_BASE_URL", "https://api.polygon.io")
TRADING_DAYS_PER_YEAR = 252
DEFAULT_TICKERS = ("AMAT", "SNDK", "GLW", "CVS")


@dataclass(frozen=True)
class OptionRiskPremium:
    ticker: str
    underlying_price: float
    target_date: date
    expiration_date: date
    atm_strike: float
    call_ticker: str | None
    put_ticker: str | None
    call_price: float | None
    put_price: float | None
    implied_vol: float
    realized_vol: float
    implied_risk_premium: float


def rate_limited(
    calls_per_minute: int = 5,
    *,
    time_func: Callable[[], float] = time.monotonic,
    sleep_func: Callable[[float], None] = time.sleep,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    if calls_per_minute <= 0:
        raise ValueError("calls_per_minute must be positive")

    window_seconds = 60.0

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        call_times: deque[float] = deque()

        @wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            now = time_func()
            while call_times and now - call_times[0] >= window_seconds:
                call_times.popleft()

            if len(call_times) >= calls_per_minute:
                sleep_for = window_seconds - (now - call_times[0])
                if sleep_for > 0:
                    sleep_func(sleep_for)
                now = time_func()
                while call_times and now - call_times[0] >= window_seconds:
                    call_times.popleft()

            call_times.append(now)
            return func(*args, **kwargs)

        return wrapper

    return decorator


def construct_option_ticker(
    underlying: str,
    expiration_date: date,
    contract_type: str,
    strike_price: float | Decimal,
) -> str:
    option_type = _option_type_code(contract_type)
    strike = Decimal(str(strike_price))
    strike_thousandths = int((strike * Decimal("1000")).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    if strike_thousandths < 0:
        raise ValueError("strike_price must be non-negative")
    return f"O:{underlying.upper()}{expiration_date:%y%m%d}{option_type}{strike_thousandths:08d}"


def one_calendar_month_later(as_of_date: date) -> date:
    year = as_of_date.year + (1 if as_of_date.month == 12 else 0)
    month = 1 if as_of_date.month == 12 else as_of_date.month + 1
    day = min(as_of_date.day, _days_in_month(year, month))
    return date(year, month, day)


def choose_nearest_expiration(
    contracts: Sequence[Mapping[str, Any]],
    target_date: date,
) -> date:
    expirations = sorted(
        {
            _parse_date(str(contract["expiration_date"]))
            for contract in contracts
            if contract.get("expiration_date")
        }
    )
    for expiration in expirations:
        if expiration >= target_date:
            return expiration
    if expirations:
        return expirations[-1]
    raise ValueError("No option expirations were returned.")


def choose_atm_contracts(
    chain_rows: Sequence[Mapping[str, Any]],
    underlying_price: float,
    side: str = "average_call_put",
) -> tuple[float, Mapping[str, Any] | None, Mapping[str, Any] | None]:
    side = side.lower()
    if side not in {"average_call_put", "call", "put"}:
        raise ValueError("side must be 'average_call_put', 'call', or 'put'")

    rows_by_strike: dict[float, dict[str, Mapping[str, Any]]] = {}
    for row in chain_rows:
        details = _details(row)
        if not details:
            continue
        strike = _float_or_none(details.get("strike_price"))
        contract_type = str(details.get("contract_type", "")).lower()
        if strike is None or contract_type not in {"call", "put"}:
            continue
        rows_by_strike.setdefault(strike, {})[contract_type] = row

    if side == "average_call_put":
        candidates = [
            (strike, rows)
            for strike, rows in rows_by_strike.items()
            if "call" in rows and "put" in rows
        ]
    else:
        candidates = [
            (strike, rows)
            for strike, rows in rows_by_strike.items()
            if side in rows
        ]

    if not candidates:
        raise ValueError(f"No ATM option candidates were available for side '{side}'.")

    atm_strike, rows = min(
        candidates,
        key=lambda item: (abs(item[0] - underlying_price), item[0]),
    )
    return atm_strike, rows.get("call"), rows.get("put")


def realized_volatility(prices: Sequence[float], window: int = 21) -> float:
    if window <= 1:
        raise ValueError("window must be greater than 1")

    clean_prices = np.asarray([price for price in prices if price is not None], dtype=np.float64)
    if clean_prices.size < window + 1:
        raise ValueError(f"At least {window + 1} prices are required.")
    clean_prices = clean_prices[-(window + 1) :]
    log_returns = np.diff(np.log(clean_prices))
    vol = float(np.std(log_returns, ddof=1) * math.sqrt(TRADING_DAYS_PER_YEAR))
    if not math.isfinite(vol):
        raise ValueError("Realized volatility is not finite.")
    return vol


def implied_risk_premium(implied_vol: float, realized_vol: float) -> float:
    return float(implied_vol) - float(realized_vol)


def black_scholes_price(
    underlying_price: float,
    strike_price: float,
    time_to_expiration: float,
    volatility: float,
    *,
    risk_free_rate: float = 0.04,
    dividend_yield: float = 0.0,
    contract_type: str = "call",
) -> float:
    if underlying_price <= 0 or strike_price <= 0:
        raise ValueError("underlying_price and strike_price must be positive")
    if time_to_expiration <= 0:
        raise ValueError("time_to_expiration must be positive")
    if volatility <= 0:
        raise ValueError("volatility must be positive")

    sigma_sqrt_t = volatility * math.sqrt(time_to_expiration)
    d1 = (
        math.log(underlying_price / strike_price)
        + (risk_free_rate - dividend_yield + 0.5 * volatility * volatility) * time_to_expiration
    ) / sigma_sqrt_t
    d2 = d1 - sigma_sqrt_t
    discounted_underlying = underlying_price * math.exp(-dividend_yield * time_to_expiration)
    discounted_strike = strike_price * math.exp(-risk_free_rate * time_to_expiration)

    option_type = _option_type_code(contract_type)
    if option_type == "C":
        return discounted_underlying * _normal_cdf(d1) - discounted_strike * _normal_cdf(d2)
    return discounted_strike * _normal_cdf(-d2) - discounted_underlying * _normal_cdf(-d1)


def implied_volatility_from_option_price(
    option_price: float,
    underlying_price: float,
    strike_price: float,
    time_to_expiration: float,
    *,
    risk_free_rate: float = 0.04,
    dividend_yield: float = 0.0,
    contract_type: str = "call",
    low: float = 1e-4,
    high: float = 5.0,
    tolerance: float = 1e-5,
    max_iterations: int = 100,
) -> float:
    if option_price <= 0:
        raise ValueError("option_price must be positive")

    low_price = black_scholes_price(
        underlying_price,
        strike_price,
        time_to_expiration,
        low,
        risk_free_rate=risk_free_rate,
        dividend_yield=dividend_yield,
        contract_type=contract_type,
    )
    high_price = black_scholes_price(
        underlying_price,
        strike_price,
        time_to_expiration,
        high,
        risk_free_rate=risk_free_rate,
        dividend_yield=dividend_yield,
        contract_type=contract_type,
    )
    if option_price < low_price or option_price > high_price:
        raise ValueError(
            f"option_price {option_price} is outside solvable range [{low_price}, {high_price}]"
        )

    for _ in range(max_iterations):
        midpoint = (low + high) / 2.0
        model_price = black_scholes_price(
            underlying_price,
            strike_price,
            time_to_expiration,
            midpoint,
            risk_free_rate=risk_free_rate,
            dividend_yield=dividend_yield,
            contract_type=contract_type,
        )
        if abs(model_price - option_price) <= tolerance:
            return midpoint
        if model_price < option_price:
            low = midpoint
        else:
            high = midpoint
    return (low + high) / 2.0


def get_api_key() -> str:
    return os.environ["POLYGON_IO"]


@rate_limited(calls_per_minute=5)
def http_get_json(url: str, params: Mapping[str, Any] | None = None) -> dict[str, Any]:
    query = _clean_params(params or {})
    request_url = f"{url}?{urlencode(query)}" if query else url
    request = Request(request_url, headers={"Accept": "application/json"})
    try:
        with urlopen(request, timeout=30) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code} from {url}: {body}") from exc
    if payload.get("status") not in {None, "OK", "DELAYED"}:
        raise RuntimeError(f"API returned status {payload.get('status')}: {payload}")
    return payload


def fetch_current_price(ticker: str) -> float:
    yf_ticker = Ticker(ticker)
    fast_info = yf_ticker.fast_info
    price = _float_or_none(_mapping_get(fast_info, "last_price"))
    if price is not None:
        return price

    history = yf_ticker.history(period="1mo", auto_adjust=False, actions=False)
    if history is None or history.empty:
        raise ValueError(f"No price history was available for {ticker}.")
    return float(history["Close"].dropna().iloc[-1])


def fetch_recent_closes(ticker: str, days: int = 45) -> list[float]:
    history = Ticker(ticker).history(period=f"{days}d", auto_adjust=False, actions=False)
    if history is None or history.empty:
        raise ValueError(f"No price history was available for {ticker}.")
    return [float(price) for price in history["Close"].dropna().to_list()]


def fetch_candidate_contracts(
    ticker: str,
    target_date: date,
    *,
    api_key: str | None = None,
) -> list[dict[str, Any]]:
    payload = http_get_json(
        f"{API_BASE_URL}/v3/reference/options/contracts",
        {
            "underlying_ticker": ticker.upper(),
            "expired": "false",
            "expiration_date.gte": target_date.isoformat(),
            "sort": "expiration_date",
            "order": "asc",
            "limit": 1000,
            "apiKey": api_key or get_api_key(),
        },
    )
    return list(payload.get("results") or [])


def fetch_option_daily_bars(
    option_ticker: str,
    start_date: date,
    end_date: date,
    *,
    api_key: str | None = None,
) -> list[dict[str, Any]]:
    payload = http_get_json(
        (
            f"{API_BASE_URL}/v2/aggs/ticker/{quote(option_ticker, safe='')}"
            f"/range/1/day/{start_date.isoformat()}/{end_date.isoformat()}"
        ),
        {
            "adjusted": "true",
            "sort": "asc",
            "limit": 5000,
            "apiKey": api_key or get_api_key(),
        },
    )
    return list(payload.get("results") or [])


def latest_close_from_bars(bars: Sequence[Mapping[str, Any]]) -> float:
    if not bars:
        raise ValueError("No option daily bars were returned.")
    latest = max(bars, key=lambda bar: int(bar.get("t", 0)))
    close = _float_or_none(latest.get("c"))
    if close is None or close <= 0:
        raise ValueError("Latest option close was not available.")
    return close


def fetch_latest_option_close(
    option_ticker: str,
    as_of_date: date,
    *,
    lookback_days: int = 14,
    api_key: str | None = None,
) -> float:
    bars = fetch_option_daily_bars(
        option_ticker,
        as_of_date - timedelta(days=lookback_days),
        as_of_date,
        api_key=api_key,
    )
    return latest_close_from_bars(bars)


def calculate_option_risk_premium(
    ticker: str,
    *,
    as_of_date: date | None = None,
    side: str = "average_call_put",
    risk_free_rate: float = 0.04,
    dividend_yield: float = 0.0,
    api_key: str | None = None,
) -> OptionRiskPremium:
    ticker = ticker.upper()
    as_of_date = as_of_date or date.today()
    target_date = one_calendar_month_later(as_of_date)
    current_price = fetch_current_price(ticker)
    contracts = fetch_candidate_contracts(ticker, target_date, api_key=api_key)
    expiration_date = choose_nearest_expiration(contracts, target_date)
    expiration_contracts = [
        contract
        for contract in contracts
        if contract.get("expiration_date") == expiration_date.isoformat()
    ]
    atm_strike, call_row, put_row = choose_atm_contracts(expiration_contracts, current_price, side=side)
    call_ticker = construct_option_ticker(ticker, expiration_date, "call", atm_strike) if call_row else None
    put_ticker = construct_option_ticker(ticker, expiration_date, "put", atm_strike) if put_row else None
    time_to_expiration = (expiration_date - as_of_date).days / 365.0

    call_price = fetch_latest_option_close(call_ticker, as_of_date, api_key=api_key) if call_ticker else None
    put_price = fetch_latest_option_close(put_ticker, as_of_date, api_key=api_key) if put_ticker else None
    implied_vol = average_implied_vol_from_prices(
        call_price,
        put_price,
        current_price,
        atm_strike,
        time_to_expiration,
        side=side,
        risk_free_rate=risk_free_rate,
        dividend_yield=dividend_yield,
    )
    rv = realized_volatility(fetch_recent_closes(ticker))
    premium = implied_risk_premium(implied_vol, rv)

    return OptionRiskPremium(
        ticker=ticker,
        underlying_price=current_price,
        target_date=target_date,
        expiration_date=expiration_date,
        atm_strike=atm_strike,
        call_ticker=call_ticker,
        put_ticker=put_ticker,
        call_price=call_price,
        put_price=put_price,
        implied_vol=implied_vol,
        realized_vol=rv,
        implied_risk_premium=premium,
    )


def average_implied_vol_from_prices(
    call_price: float | None,
    put_price: float | None,
    underlying_price: float,
    strike_price: float,
    time_to_expiration: float,
    *,
    side: str = "average_call_put",
    risk_free_rate: float = 0.04,
    dividend_yield: float = 0.0,
) -> float:
    side = side.lower()
    vols = []
    if side in {"average_call_put", "call"}:
        if call_price is None:
            raise ValueError("Call option close was not available.")
        vols.append(
            implied_volatility_from_option_price(
                call_price,
                underlying_price,
                strike_price,
                time_to_expiration,
                risk_free_rate=risk_free_rate,
                dividend_yield=dividend_yield,
                contract_type="call",
            )
        )
    if side in {"average_call_put", "put"}:
        if put_price is None:
            raise ValueError("Put option close was not available.")
        vols.append(
            implied_volatility_from_option_price(
                put_price,
                underlying_price,
                strike_price,
                time_to_expiration,
                risk_free_rate=risk_free_rate,
                dividend_yield=dividend_yield,
                contract_type="put",
            )
        )
    if not vols:
        raise ValueError(f"No implied volatility data was available for side '{side}'.")
    return float(sum(vols) / len(vols))


def print_results(results: Iterable[OptionRiskPremium]) -> None:
    headers = (
        "ticker",
        "underlying_price",
        "target_date",
        "expiration_date",
        "atm_strike",
        "call_ticker",
        "put_ticker",
        "call_price",
        "put_price",
        "implied_vol",
        "realized_vol",
        "implied_risk_premium",
    )
    print(",".join(headers))
    for result in results:
        print(
            ",".join(
                [
                    result.ticker,
                    f"{result.underlying_price:.4f}",
                    result.target_date.isoformat(),
                    result.expiration_date.isoformat(),
                    f"{result.atm_strike:.4f}",
                    result.call_ticker or "",
                    result.put_ticker or "",
                    f"{result.call_price:.4f}" if result.call_price is not None else "",
                    f"{result.put_price:.4f}" if result.put_price is not None else "",
                    f"{result.implied_vol:.6f}",
                    f"{result.realized_vol:.6f}",
                    f"{result.implied_risk_premium:.6f}",
                ]
            )
        )


def main() -> None:
    api_key = get_api_key()
    results: list[OptionRiskPremium] = []
    errors: list[tuple[str, str]] = []
    for ticker in DEFAULT_TICKERS:
        try:
            results.append(calculate_option_risk_premium(ticker, api_key=api_key))
        except Exception as exc:
            errors.append((ticker, str(exc)))

    if results:
        print_results(results)
    for ticker, error in errors:
        print(f"{ticker}: {error}", file=sys.stderr)
    if errors:
        raise SystemExit(1)


def _days_in_month(year: int, month: int) -> int:
    if month == 12:
        next_month = date(year + 1, 1, 1)
    else:
        next_month = date(year, month + 1, 1)
    return (next_month - date(year, month, 1)).days


def _parse_date(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()


def _option_type_code(contract_type: str) -> str:
    normalized = contract_type.lower()
    if normalized in {"call", "c"}:
        return "C"
    if normalized in {"put", "p"}:
        return "P"
    raise ValueError("contract_type must be call/put or C/P")


def _normal_cdf(value: float) -> float:
    return 0.5 * (1.0 + math.erf(value / math.sqrt(2.0)))


def _clean_params(params: Mapping[str, Any]) -> dict[str, str]:
    return {
        key: str(value).lower() if isinstance(value, bool) else str(value)
        for key, value in params.items()
        if value is not None
    }


def _details(row: Mapping[str, Any]) -> Mapping[str, Any]:
    details = row.get("details")
    if isinstance(details, Mapping):
        return details
    return row


def _float_or_none(value: Any) -> float | None:
    if value is None:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(result):
        return None
    return result


def _mapping_get(value: Any, key: str) -> Any:
    if isinstance(value, Mapping):
        return value.get(key)
    try:
        return value[key]
    except (KeyError, TypeError):
        return None


def _option_ticker_from_row(row: Mapping[str, Any] | None) -> str | None:
    if row is None:
        return None
    details = _details(row)
    ticker = details.get("ticker")
    return str(ticker) if ticker else None


if __name__ == "__main__":
    main()
