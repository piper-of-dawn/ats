import numpy as np
from functools import wraps
from inspect import signature
from typing import Callable, Mapping, Any
import polars as pl

EPS = 1e-12
GARCH_PARAM_NAMES = ("omega", "alpha", "beta")

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

def _validate_garch_params(
    omega: float | None,
    alpha: float | None,
    beta: float | None,
) -> tuple[float, float, float]:
    if omega is None or alpha is None or beta is None:
        raise ValueError("omega, alpha, and beta must all be set or all be calibrated")
    omega, alpha, beta = float(omega), float(alpha), float(beta)
    if omega <= 0:
        raise ValueError("omega must be > 0")
    if alpha < 0 or beta < 0 or (alpha + beta) >= 1:
        raise ValueError("require alpha>=0, beta>=0, alpha+beta<1")
    return omega, alpha, beta


def _compute_garch_sigma2(
    returns: np.ndarray,
    omega: float,
    alpha: float,
    beta: float,
) -> np.ndarray:
    r = np.asarray(returns, dtype=np.float64)
    T = r.size
    omega, alpha, beta = _validate_garch_params(omega, alpha, beta)

    sigma2 = np.empty(T, dtype=np.float64)
    k = min(5, T)
    sigma2[0] = np.nanvar(r[:k], ddof=1)

    if not np.isfinite(sigma2[0]) or sigma2[0] <= 0:
        sigma2[0] = omega / (1 - alpha - beta)

    for t in range(1, T):
        sigma2[t] = omega + alpha * r[t - 1] ** 2 + beta * sigma2[t - 1]

    return sigma2


def calibrate(
    calibration_func: Callable[..., Mapping[str, float] | tuple[float, float, float]],
    parameter_names: tuple[str, ...] = GARCH_PARAM_NAMES,
) -> Callable[[Callable[..., np.ndarray]], Callable[..., np.ndarray]]:
    def decorator(volatility_func: Callable[..., np.ndarray]) -> Callable[..., np.ndarray]:
        func_signature = signature(volatility_func)

        @wraps(volatility_func)
        def wrapper(*args: Any, **kwargs: Any) -> np.ndarray:
            bound = func_signature.bind_partial(*args, **kwargs)
            bound.apply_defaults()

            if any(bound.arguments.get(name) is None for name in parameter_names):
                calibrated = calibration_func(
                    returns=bound.arguments["returns"],
                    eta=bound.arguments.get("eta"),
                )
                if isinstance(calibrated, Mapping):
                    params = {name: calibrated[name] for name in parameter_names}
                else:
                    params = dict(zip(parameter_names, calibrated, strict=True))
                bound.arguments.update(params)

            return volatility_func(*bound.args, **bound.kwargs)

        return wrapper

    return decorator


def garch_mle_calibration(
    returns: np.ndarray,
    eta: float | None = None,  # ignored; kept for calibration function compatibility
) -> dict[str, float]:
    r = np.asarray(returns, dtype=np.float64)
    if r.size < 2:
        raise ValueError("at least two returns are required to calibrate GARCH")

    try:
        from scipy.optimize import minimize
    except ImportError as exc:
        raise ImportError("scipy is required to calibrate GARCH parameters") from exc

    var = np.nanvar(r)
    if not np.isfinite(var) or var <= 0:
        var = EPS
    init = np.array([var * 0.05, 0.05, 0.90], dtype=np.float64)

    def nll(params):
        omega, alpha, beta = params
        if omega <= 0 or alpha < 0 or beta < 0 or alpha + beta >= 1:
            return np.inf

        sigma2 = _compute_garch_sigma2(r, omega, alpha, beta)[1:]
        rr = r[1:]

        if np.any(~np.isfinite(sigma2)) or np.any(sigma2 <= 0):
            return np.inf

        return np.sum(np.log(sigma2) + rr**2 / sigma2)

    res = minimize(
        nll,
        init,
        method="Nelder-Mead",
        options={"maxiter": 5000},
    )

    if not res.success:
        raise RuntimeError(f"GARCH calibration failed: {res.message}")

    omega, alpha, beta = _validate_garch_params(*res.x)
    return {"omega": omega, "alpha": alpha, "beta": beta}


@calibrate(garch_mle_calibration)
def garch_volatility(
    returns: np.ndarray,
    eta: float = 0.0,  # ignored
    omega: float | None = None,
    alpha: float | None = None,
    beta: float | None = None,
) -> np.ndarray:
    r = np.asarray(returns, dtype=np.float64)
    T = r.size

    if T == 0:
        return r
    if T < 2:
        return np.full(T, np.nan)

    return _compute_garch_sigma2(r, omega, alpha, beta)
