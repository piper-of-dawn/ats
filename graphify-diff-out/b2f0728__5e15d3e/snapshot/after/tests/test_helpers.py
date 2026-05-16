import numpy as np
import polars as pl

from ats.helpers import calibrate, compute_ema_signal, ema_volatility, garch_volatility


def test_garch_volatility_calibrates_when_params_are_none():
    returns = np.array([0.01, -0.02, 0.015, 0.005, -0.01, 0.02, -0.03, 0.01])

    sigma2 = garch_volatility(
        returns,
        omega=None,
        alpha=None,
        beta=None,
    )

    assert sigma2.shape == returns.shape
    assert np.isfinite(sigma2).all()
    assert (sigma2 > 0).all()


def test_garch_volatility_uses_fixed_params():
    returns = np.array([0.01, -0.02, 0.015, 0.005, -0.01, 0.02, -0.03, 0.01])

    sigma2 = garch_volatility(
        returns,
        omega=0.00001,
        alpha=0.05,
        beta=0.90,
    )

    assert sigma2.shape == returns.shape
    assert np.isfinite(sigma2).all()
    assert (sigma2 > 0).all()


def test_calibrate_decorator_accepts_custom_calibration_function():
    def fixed_calibration(returns, eta=None):
        return {"omega": 0.00001, "alpha": 0.05, "beta": 0.90}

    @calibrate(fixed_calibration)
    def custom_volatility(returns, eta=0.0, omega=None, alpha=None, beta=None):
        return np.array([omega, alpha, beta])

    params = custom_volatility(np.array([0.01, -0.02]), omega=None, alpha=None, beta=None)

    np.testing.assert_allclose(params, np.array([0.00001, 0.05, 0.90]))


def test_compute_ema_signal_accepts_garch_none_args():
    price_data = pl.DataFrame(
        {
            "close": [100.0, 101.0, 100.5, 102.0, 101.5, 103.0, 102.5, 104.0],
            "idiosyncratic_returns": [
                0.01,
                -0.02,
                0.015,
                0.005,
                -0.01,
                0.02,
                -0.03,
                0.01,
            ],
        }
    )

    signal = compute_ema_signal(
        price_data=price_data,
        volatility_model=garch_volatility,
        volatility_model_args={"omega": None, "alpha": None, "beta": None},
        eta=np.log(2) / 112,
    )

    assert signal.shape == (8,)
    assert np.isfinite(signal).all()


def test_compute_ema_signal_defaults_to_ema_volatility():
    price_data = pl.DataFrame(
        {
            "close": [100.0, 101.0, 100.5, 102.0, 101.5],
            "idiosyncratic_returns": [0.01, -0.02, 0.015, 0.005, -0.01],
        }
    )

    signal = compute_ema_signal(
        price_data=price_data,
        volatility_model=ema_volatility,
        volatility_model_args={},
        eta=np.log(2) / 20,
    )

    assert signal.shape == (5,)
    assert np.isfinite(signal).all()
