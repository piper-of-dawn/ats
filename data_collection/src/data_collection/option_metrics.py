"""Dated ATM Black-Scholes IV and IV-minus-realized-volatility from Massive.

The model treats options as European and uses explicit constant rate/yield inputs.
US equity options may be American; these are model estimates, not vendor Greeks.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, timedelta
import math

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view
from scipy.optimize.elementwise import find_root
from scipy.special import ndtr

from . import market, massive


@dataclass(frozen=True)
class OptionSettings:
    history_days: int = 0  # 0 = latest session, -1 = full requested range
    target_days: int = 30
    rv_window: int = 21
    risk_free_rate: float = 0.04
    dividend_yield: float = 0.0

    def validate(self):
        if self.history_days < -1 or self.target_days < 1 or self.rv_window < 2:
            raise ValueError("Invalid options history, maturity or realized-volatility window")
        if not all(math.isfinite(v) for v in (self.risk_free_rate, self.dividend_yield)):
            raise ValueError("Option rate and dividend yield must be finite")
        return self

    def dictionary(self):
        return asdict(self)


def black_scholes(spot, strike, years, sigma, rate=0.04, dividend_yield=0.0, is_call=True):
    """Broadcast Black-Scholes over NumPy arrays; ndtr is SciPy's compiled CDF."""
    spot, strike, years, sigma, rate, dividend_yield, is_call = np.broadcast_arrays(
        spot, strike, years, sigma, rate, dividend_yield, is_call)
    root_t = np.sqrt(years)
    d1 = (np.log(spot / strike) + (rate - dividend_yield + sigma * sigma / 2) * years) / (sigma * root_t)
    d2 = d1 - sigma * root_t
    sign = np.where(is_call, 1.0, -1.0)
    return sign * (spot * np.exp(-dividend_yield * years) * ndtr(sign * d1)
                   - strike * np.exp(-rate * years) * ndtr(sign * d2))


def implied_volatility(price, spot, strike, years, rate=0.04, dividend_yield=0.0, is_call=True):
    """Solve a batch of IVs with SciPy's safeguarded elementwise root solver.

    Invalid inputs, arbitrage bounds, flat/zero-vega prices and failed convergence
    return NaN, which the serializer turns into null; never manufacture zero IV.
    """
    arrays = np.broadcast_arrays(*[np.asarray(x) for x in (price, spot, strike, years, rate, dividend_yield, is_call)])
    p, s, k, t, r, q, calls = arrays
    answer = np.full(p.shape, np.nan, dtype=float)
    valid = np.isfinite(p) & np.isfinite(s) & np.isfinite(k) & np.isfinite(t) & np.isfinite(r) & np.isfinite(q)
    valid &= (p > 0) & (s > 0) & (k > 0) & (t > 0)
    if not np.any(valid):
        return answer
    # Work only on valid rows to avoid invalid logarithms in the vector solver.
    args = tuple(np.asarray(x[valid], dtype=float) for x in (p, s, k, t, r, q)) + (calls[valid],)
    def residual(vol, price, spot, strike, years, rate, div, call):
        return black_scholes(spot, strike, years, vol, rate, div, call) - price
    low, high = np.full(args[0].shape, 1e-8), np.full(args[0].shape, 10.0)
    fl, fh = residual(low, *args), residual(high, *args)
    bracketed = (fl < -1e-10) & (fh > 1e-10)
    roots = np.full(low.shape, np.nan)
    if np.any(bracketed):
        result = find_root(residual, (low[bracketed], high[bracketed]),
                           args=tuple(x[bracketed] for x in args),
                           tolerances={"xatol": 1e-10, "xrtol": 1e-10, "fatol": 1e-10}, maxiter=100)
        roots[bracketed] = np.where(result.success, result.x, np.nan)
    answer[valid] = roots
    return answer


def rolling_realized_volatility(prices, window=21):
    """Annualized sample standard deviation of trailing daily log returns."""
    prices = np.asarray(prices, dtype=float)
    result = np.full(prices.shape, np.nan)
    if prices.size <= window:
        return result
    with np.errstate(divide="ignore", invalid="ignore"):
        returns = np.diff(np.log(np.where(prices > 0, prices, np.nan)))
    result[window:] = np.std(sliding_window_view(returns, window), axis=-1, ddof=1) * np.sqrt(252.0)
    return result


def _number(value):
    try:
        return float(value) if math.isfinite(float(value)) else None
    except (ValueError, TypeError):
        return None


def collect_option_metrics(ticker, start, end, *, settings=None, adjusted_prices=None,
                           raw_prices=None, warmup_prices=None, api_key=None, checkpoint=None, log=None):
    settings = (settings or OptionSettings()).validate()
    option_start = start if settings.history_days == -1 else max(start, end - timedelta(days=settings.history_days))
    # Fetch enough warm-up history even for a one-day daily update. Adjusted closes
    # are used only for returns; the option solver always uses nominal/raw spot.
    warmup = option_start - timedelta(days=settings.rv_window * 3 + 10)
    first_target = adjusted_prices[-1]["date"] if adjusted_prices and settings.history_days == 0 else option_start.isoformat()
    if warmup_prices is not None:
        adjusted = warmup_prices
    elif adjusted_prices and sum(row["date"] < first_target for row in adjusted_prices) >= settings.rv_window:
        adjusted = adjusted_prices
    else:
        adjusted = market.stock_bars(ticker, warmup, end, api_key=api_key, checkpoint=checkpoint)
    raw = raw_prices if raw_prices is not None else market.stock_bars(
        ticker, warmup, end, adjusted=False, api_key=api_key, checkpoint=checkpoint)
    nominal = {row["date"]: row for row in raw}
    rv = rolling_realized_volatility([row.get("Close") for row in adjusted], settings.rv_window)
    dates = [row["date"] for row in adjusted if start.isoformat() <= row["date"] <= end.isoformat()]
    dates = dates[-1:] if settings.history_days == 0 else [d for d in dates if d >= option_start.isoformat()]
    bars_start = date.fromisoformat(dates[0]) if dates else option_start
    volatility = {row["date"]: value for row, value in zip(adjusted, rv)}
    bars_by_contract = {}
    result, requests = [], []
    for iso in dates:
        day = date.fromisoformat(iso)
        row = {"date": iso, "status": "unavailable", "reason": None, "underlying_price": None,
               "call_implied_volatility": None, "put_implied_volatility": None,
               "implied_volatility": None, "realized_volatility": _number(volatility.get(iso)),
               "implied_risk_premium": None, "variance_spread": None,
               "risk_free_rate": settings.risk_free_rate, "dividend_yield": settings.dividend_yield}
        result.append(row)
        spot = _number(nominal.get(iso, {}).get("Close"))
        if spot is None or spot <= 0:
            row["reason"] = "missing_same_session_underlying_close"
            continue
        row["underlying_price"] = spot
        target = day + timedelta(days=settings.target_days)
        contracts = market.option_contracts(ticker, day, target, target + timedelta(days=45), spot,
                                           api_key=api_key, checkpoint=checkpoint)
        # Exclude adjusted deliverables; their quoted strike is insufficient to
        # price the contract as an ordinary 100-share vanilla option.
        pairs = {}
        for contract in contracts:
            expiry = contract.get("expiration_date", "")
            strike = _number(contract.get("strike_price"))
            side = contract.get("contract_type")
            if not (target.isoformat() <= expiry <= (target + timedelta(days=45)).isoformat()):
                continue
            if strike is None or strike <= 0 or side not in ("call", "put"):
                continue
            if contract.get("shares_per_contract", 100) != 100 or contract.get("additional_underlyings"):
                continue
            pairs.setdefault((expiry, strike), {})[side] = contract
        pairs = [(key, pair) for key, pair in pairs.items() if "call" in pair and "put" in pair]
        if not pairs:
            row["reason"] = "no_standard_call_put_pair"
            continue
        (expiry, strike), pair = min(pairs, key=lambda x: (x[0][0], abs(x[0][1] - spot), x[0][1]))
        row.update(expiration_date=expiry, strike=strike,
                   days_to_expiration=(date.fromisoformat(expiry) - day).days)
        quotes = []
        for side in ("call", "put"):
            symbol = pair[side]["ticker"]  # Preserve actual OCC root; do not invent option symbols.
            if symbol not in bars_by_contract:
                bars = market.option_bars(symbol, bars_start, end, api_key=api_key, checkpoint=checkpoint)
                bars_by_contract[symbol] = {market.bar_date(bar).isoformat(): bar for bar in bars}
            bar = bars_by_contract[symbol].get(iso, {})
            price = _number(bar.get("c"))
            row[side + "_ticker"] = symbol
            row[side + "_price"] = price
            row[side + "_volume"] = _number(bar.get("v"))
            quotes.append(price if price and price > 0 and (_number(bar.get("v")) or 0) > 0 else None)
        if any(price is None for price in quotes):
            row["reason"] = "missing_same_session_option_trade"
            continue
        for side, price in zip((True, False), quotes):
            requests.append((row, side, price, spot, strike, row["days_to_expiration"] / 365.0))
    if requests:
        _, sides, prices, spots, strikes, years = zip(*requests)
        ivs = implied_volatility(prices, spots, strikes, years, settings.risk_free_rate,
                                 settings.dividend_yield, sides)
        for (row, call, *_), value in zip(requests, ivs):
            row[("call" if call else "put") + "_implied_volatility"] = _number(value)
    for row in result:
        call, put = row["call_implied_volatility"], row["put_implied_volatility"]
        if call is not None and put is not None:
            iv = (call + put) / 2
            row["implied_volatility"] = iv
            rv_value = row["realized_volatility"]
            if rv_value is not None:
                row.update(status="ok", reason=None, implied_risk_premium=iv - rv_value,
                           variance_spread=iv * iv - rv_value * rv_value)
            else:
                row["reason"] = "insufficient_realized_volatility_history"
        elif row["reason"] is None:
            row["reason"] = "option_price_outside_black_scholes_bounds"
        if row["status"] != "ok" and log:
            log.warning("UNAVAILABLE ticker=%s feed=options date=%s reason=%s", ticker, row["date"], row["reason"])
    return result
