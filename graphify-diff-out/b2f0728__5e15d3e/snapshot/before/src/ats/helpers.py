import numpy as np
from typing import Callable, Mapping, Any
import polars as pl

def compute_ema_signal(
    price_data: "pl.DataFrame",
    volatility_model: Callable[..., np.ndarray],
    volatility_model_args: Mapping[str, Any],
    eta: float,
) -> np.ndarray:
    prices = np.asarray(price_data["close"], dtype=np.float64)
    if prices.size < 2: 
        return np.empty(0, dtype=np.float64)
    returns = price_data['idiosyncratic_returns'].to_numpy()

    # enforce same eta for signal+vol model; vol model may ignore it (GARCH does)
    sigma2 = volatility_model(**dict(volatility_model_args), returns=returns, eta=eta)

    T = returns.size
    ema = np.empty(T, dtype=np.float64)
    EPS = 1e-12
    ema[0] = np.sqrt(eta) * returns[0] / np.sqrt(sigma2[0] + EPS)
    for t in range(1, T):
        ema[t] = (1 - eta) * ema[t - 1] + np.sqrt(eta) * (returns[t] / (np.sqrt(sigma2[t]) + EPS))
    if not np.isfinite(ema).all(): 
        raise ValueError("EMA produced non-finite values")
    return ema

def ema_volatility(returns: np.ndarray, eta: float) -> np.ndarray:
    returns = np.asarray(returns, dtype=np.float64)
    T = returns.size
    if T == 0: 
        return returns
    if not (0.0 < eta < 1.0): 
        raise ValueError("eta must be in (0,1)")
    if T < 2: 
        return np.full(T, np.nan)
    sigma2 = np.empty(T, dtype=np.float64)
    k = min(5, T)
    sigma2[0] = np.nanvar(returns[:k], ddof=1)
    for t in range(1, T): 
        sigma2[t] = (1 - eta) * sigma2[t-1] + eta * returns[t] * returns[t]
    return sigma2

def garch_volatility(
    returns: np.ndarray,
    eta: float,  # ignored (kept so it plugs into the same call signature)
    omega: float,
    alpha: float,
    beta: float,
) -> np.ndarray:
    r = np.asarray(returns, dtype=np.float64)
    T = returns.size
    if T == 0: 
        return r
    if omega <= 0: 
        raise ValueError("omega must be > 0")
    if alpha < 0 or beta < 0 or (alpha + beta) >= 1: 
        raise ValueError("require alpha>=0, beta>=0, alpha+beta<1")
    if T < 2: 
        return np.full(T, np.nan)

    sigma2 = np.empty(T, dtype=np.float64)
    k = min(5, T)
    sigma2[0] = np.nanvar(r[:k], ddof=1)
    if not np.isfinite(sigma2[0]) or sigma2[0] <= 0: 
        sigma2[0] = omega / (1 - alpha - beta)

    for t in range(1, T):
        sigma2[t] = omega + alpha * (r[t - 1] * r[t - 1]) + beta * sigma2[t - 1]
    return sigma2

