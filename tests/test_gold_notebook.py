import json
from pathlib import Path
import sys
from types import ModuleType

import numpy as np
import pandas as pd
import pytest


NOTEBOOK_PATH = Path(__file__).parents[1] / "notebooks" / "gold.ipynb"


def load_notebook_namespace(*tags: str) -> dict[str, object]:
    """Execute only tagged, network-free notebook cells for focused tests."""
    notebook = json.loads(NOTEBOOK_PATH.read_text())
    requested = set(tags)
    sources = []
    for cell in notebook["cells"]:
        cell_tags = set(cell.get("metadata", {}).get("tags", []))
        if cell.get("cell_type") == "code" and requested.intersection(cell_tags):
            sources.append("".join(cell.get("source", [])))

    module = ModuleType("gold_notebook_test")
    module.__file__ = str(NOTEBOOK_PATH)
    sys.modules[module.__name__] = module
    exec(compile("\n\n".join(sources), str(NOTEBOOK_PATH), "exec"), module.__dict__)
    return module.__dict__


def test_notebook_exposes_research_configuration():
    namespace = load_notebook_namespace("research-core")

    config = namespace["ResearchConfig"]()
    assert config.entry_z == 2.0
    assert config.exit_z == 0.5
    assert config.max_holding_days == 20
    assert config.transaction_cost_bps == 5.0
    assert config.annual_short_borrow == 0.02
    assert config.bootstrap_repetitions >= 2_000

    assert {"GLD", "GDX"}.issubset(namespace["TRADABLE_TICKERS"])
    assert {"SPY", "UUP"}.issubset(namespace["FACTOR_TICKERS"])


@pytest.fixture
def core():
    return load_notebook_namespace("research-core")


def test_price_validation_rejects_nonpositive_and_does_not_fill_missing_returns(core):
    dates = pd.date_range("2025-01-01", periods=5, freq="D")
    raw = pd.DataFrame({"GLD": [100.0, 0.0, 102.0, np.nan, 104.0]}, index=dates)

    clean = core["validate_price_panel"](raw)
    simple, log = core["simple_and_log_returns"](clean)

    assert pd.isna(clean.loc[dates[1], "GLD"])
    assert simple["GLD"].isna().sum() >= 3
    assert log["GLD"].isna().sum() >= 3


def test_rolling_ols_uses_only_observations_strictly_before_timestamp(core):
    dates = pd.date_range("2025-01-01", periods=12, freq="D")
    x = pd.DataFrame({"factor": np.arange(12.0)}, index=dates)
    y = pd.Series(1.0 + 2.0 * x["factor"], index=dates)
    changed = y.copy()
    changed.iloc[8:] += 1_000.0

    beta, _ = core["rolling_ols_lagged"](y, x, window=5)
    changed_beta, _ = core["rolling_ols_lagged"](changed, x, window=5)

    pd.testing.assert_series_equal(beta.loc[dates[8]], changed_beta.loc[dates[8]])


def test_lagged_robust_zscore_uses_prior_window_for_current_value(core):
    values = pd.Series([1.0, 2.0, 3.0, 100.0])

    zscore = core["lagged_robust_zscore"](values, window=3)

    assert zscore.iloc[3] == pytest.approx((100.0 - 2.0) / 1.4826)


def test_chronological_boundaries_are_ordered_sixty_twenty_twenty(core):
    dates = pd.date_range("2025-01-01", periods=100, freq="D")

    bounds = core["chronological_boundaries"](dates)

    assert dates.min() <= bounds.discovery_end < bounds.validation_end < dates.max()
    assert abs(dates.get_loc(bounds.discovery_end) + 1 - 60) <= 1
    assert abs(dates.get_loc(bounds.validation_end) + 1 - 80) <= 1
