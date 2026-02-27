"""ATS - Algorithmic Trading System"""

__all__ = ["EquityTicker"]


def __getattr__(name):
    if name == "EquityTicker":
        from .ticker import EquityTicker

        return EquityTicker
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
