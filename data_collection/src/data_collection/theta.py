"""Small adapter around the official ThetaData SDK: pacing, deadlines and retries.

Only raw EOD endpoints are used. Authentication payloads and provider exception
bodies are never logged; the pinned SDK otherwise logs its full auth response.
"""

from __future__ import annotations

from contextlib import contextmanager
import json
import logging
import math
import os
from pathlib import Path
from threading import Lock
import time

from .massive import _RequestLimiter


class ThetaAPIError(RuntimeError):
    def __init__(self, code):
        self.code = code
        super().__init__(f"ThetaData request failed ({code})")


def resolve_api_key(key=None):
    return key or os.environ.get("THETADATA_API_KEY") or os.environ.get("THETADATA_APIKEY")


def _sdk_factory(key, timeout):
    # Disable this specific logger before importing/authenticating. Do not mute
    # the application's own progress/error logs or change the root logger.
    logging.getLogger("thetadata.client").disabled = True
    try:
        from thetadata import ThetaClient
    except ImportError:
        raise ThetaAPIError("INSTALL_THETA_EXTRA") from None
    client = ThetaClient(api_key=key, dataframe_type="pandas", dotenv_path=os.devnull)
    # SDK 1.0.15 has no request-deadline argument. Set a real gRPC deadline on
    # these two streaming RPCs; the deadline covers consumption of the stream.
    for name in ("GetOptionHistoryEod", "GetStockHistoryEod"):
        original = getattr(client.stub, name)
        setattr(client.stub, name, lambda request, call=original: call(request, timeout=timeout))
    return client


def _code(exc):
    # Return only an enum name, never an error body/email/session/credential.
    method = getattr(exc, "code", None)
    if callable(method):
        code = getattr(method(), "name", "UNKNOWN")
        return code if code in {
            "DEADLINE_EXCEEDED", "RESOURCE_EXHAUSTED", "UNAVAILABLE", "INTERNAL",
            "UNAUTHENTICATED", "PERMISSION_DENIED", "INVALID_ARGUMENT", "NOT_FOUND",
        } else "UNKNOWN"
    name = type(exc).__name__
    if name == "NoDataFoundError":
        return "NO_DATA"
    if name in {"ConnectError", "ConnectTimeout", "ReadTimeout", "WriteTimeout", "PoolTimeout"}:
        return "UNAVAILABLE"
    return "AUTHENTICATION_FAILED" if name == "AuthenticationError" else "CLIENT_ERROR"


class ThetaEOD:
    """One sequential worker, at most three attempts per request.

    THETADATA_RATE_STATE coordinates both request starts and one in-flight
    request across cooperating processes on the same Linux/Docker host.
    """

    def __init__(self, api_key=None, *, interval=3.2, timeout=120., log=None,
                 factory=None, sleep=time.sleep):
        if not resolve_api_key(api_key):
            raise ValueError("Set THETADATA_API_KEY or THETADATA_APIKEY")
        if not math.isfinite(interval) or interval < 3.2 or not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("Theta request interval must be >= 3.2 seconds and timeout must be positive")
        self.key, self.timeout = resolve_api_key(api_key), timeout
        self.log, self.factory, self.sleep = log, factory or _sdk_factory, sleep
        self.limiter = _RequestLimiter(interval, sleep=sleep, state_env="THETADATA_RATE_STATE")
        self.client = None
        self.lock = Lock()

    @contextmanager
    def _slot(self):
        with self.lock:
            state = os.environ.get("THETADATA_RATE_STATE")
            if state:
                import fcntl
                path = Path(state + ".lock")
                path.parent.mkdir(parents=True, exist_ok=True)
                with path.open("a") as stream:
                    fcntl.flock(stream, fcntl.LOCK_EX)
                    try:
                        yield
                    finally:
                        fcntl.flock(stream, fcntl.LOCK_UN)
            else:
                yield

    def history(self, kind, ticker, start, end, *, max_dte=75, strike_range=2):
        if kind not in {"stock", "option"}:
            raise ValueError("Theta EOD kind must be stock or option")
        arguments = dict(symbol=ticker, start_date=start, end_date=end)
        if kind == "option":
            arguments.update(expiration="*", max_dte=max_dte, strike_range=strike_range)
        for attempt in range(3):
            try:
                with self._slot():
                    if self.client is None:
                        self.client = self.factory(self.key, self.timeout)
                    # Pace actual market-data request starts after authentication.
                    self.limiter.wait()
                    frame = getattr(self.client, kind + "_history_eod")(**arguments)
                    required = {"created", "close"} if kind == "stock" else {
                        "created", "expiration", "strike", "right", "bid", "ask"}
                    if not frame.empty and not required <= set(frame.columns):
                        raise ThetaAPIError("INVALID_SCHEMA")
                    # SDK timestamps become ISO UTC, NaN becomes JSON null.
                    return json.loads(frame.to_json(orient="records", date_format="iso"))
            except ThetaAPIError:
                raise
            except Exception as exc:
                code = _code(exc)
                if code in {"NOT_FOUND", "NO_DATA"}:
                    return []
                if self.log:
                    self.log.warning("THETA ticker=%s feed=%s window=%s..%s attempt=%s code=%s",
                                     ticker, kind, start, end, attempt + 1, code)
                if code == "UNAUTHENTICATED":
                    self.client = None  # Reauthenticate a long-running job on its next attempt.
                if code not in {"DEADLINE_EXCEEDED", "RESOURCE_EXHAUSTED", "UNAVAILABLE",
                                "INTERNAL", "UNAUTHENTICATED"} or attempt == 2:
                    raise ThetaAPIError(code) from None
                self.sleep(60. if code == "RESOURCE_EXHAUSTED" else 5. * 2 ** attempt)
        raise AssertionError("Unreachable")
