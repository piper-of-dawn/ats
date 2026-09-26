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


@pytest.fixture
def research():
    return load_notebook_namespace("research-core", "research-inference")


@pytest.fixture
def backtest():
    return load_notebook_namespace("research-core", "research-inference", "research-backtest")


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


def _synthetic_returns(seed=7, observations=180):
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2020-01-01", periods=observations)
    gold = rng.normal(0.0002, 0.01, observations)
    spy = rng.normal(0.0003, 0.012, observations)
    noise = rng.normal(0.0, 0.004, observations)
    gdx = 1.6 * gold + 0.35 * spy + noise
    return pd.DataFrame({"GDX": gdx, "GLD": gold, "SPY": spy}, index=dates)


def test_factor_candidate_uses_lagged_normalized_hedge_weights(research):
    returns = _synthetic_returns()
    discovery = pd.Series(returns.index <= returns.index[119], index=returns.index)

    candidate = research["make_factor_candidate"](
        returns, "GDX", ("GLD", "SPY"), 30, 5, 20, discovery
    )

    assert candidate is not None
    active = candidate.leg_weights.dropna(how="all")
    assert np.allclose(active.abs().sum(axis=1), 1.0)
    changed = returns.copy()
    changed.loc[returns.index[100]:, "GDX"] += 10.0
    changed_candidate = research["make_factor_candidate"](
        changed, "GDX", ("GLD", "SPY"), 30, 5, 20, discovery
    )
    pd.testing.assert_series_equal(
        candidate.leg_weights.loc[returns.index[100]],
        changed_candidate.leg_weights.loc[returns.index[100]],
    )


def test_cointegration_and_pca_candidates_create_tradable_weights(research):
    rng = np.random.default_rng(11)
    dates = pd.bdate_range("2019-01-01", periods=220)
    common = np.cumsum(rng.normal(0.0002, 0.01, len(dates)))
    stationary = np.zeros(len(dates))
    for i in range(1, len(dates)):
        stationary[i] = 0.7 * stationary[i - 1] + rng.normal(0.0, 0.003)
    log_prices = pd.DataFrame({"GLD": common, "IAU": 0.98 * common + stationary}, index=dates)
    discovery = pd.Series(log_prices.index <= log_prices.index[149], index=dates)

    pair = research["make_cointegration_candidate"](
        log_prices, "IAU", "GLD", 40, 20, discovery
    )
    assert pair is not None
    assert np.allclose(pair.leg_weights.dropna().abs().sum(axis=1), 1.0)

    factor = rng.normal(0.0, 0.01, len(dates))
    basket = pd.DataFrame(
        {
            "GDX": 1.2 * factor + rng.normal(0, 0.003, len(dates)),
            "GDXJ": 1.5 * factor + rng.normal(0, 0.004, len(dates)),
            "SIL": 0.9 * factor + rng.normal(0, 0.003, len(dates)),
        },
        index=dates,
    )
    pca = research["make_pca_candidates"](
        basket, ("GDX", "GDXJ", "SIL"), 40, 5, 20, discovery
    )
    assert len(pca) == 3
    assert all(np.allclose(item.leg_weights.dropna(how="all").abs().sum(axis=1), 1.0) for item in pca)


def test_forward_convergence_is_purged_at_split_boundary(research):
    dates = pd.bdate_range("2022-01-03", periods=100)
    spread = pd.Series(np.sin(np.arange(100) / 5), index=dates)
    candidate = research["Candidate"](
        "toy", "cointegration", spread, spread, pd.DataFrame({"A": 0.5, "B": -0.5}, index=dates), {}
    )
    bounds = research["SplitBoundaries"](dates[59], dates[79], dates[-1])

    evidence = research["purged_forward_convergence"](candidate, (5, 10), "discovery", bounds)

    assert not evidence.empty
    assert (evidence["end_date"] <= bounds.discovery_end).all()


def test_overlap_control_bootstrap_and_holm_are_well_formed(research):
    dates = pd.bdate_range("2023-01-02", periods=30)
    evidence = pd.DataFrame({"abs_z": 3.0, "convergence": np.arange(30.0)}, index=dates)

    events = research["nonoverlapping_extreme_events"](evidence, horizon=5, threshold=2.0)
    positions = [dates.get_loc(date) for date in events.index]
    assert all(right - left >= 5 for left, right in zip(positions, positions[1:]))

    result = research["moving_block_mean_test"](
        pd.Series(np.linspace(-0.2, 0.8, 80)), repetitions=100, block_length=8, seed=3
    )
    assert {"mean", "ci_low", "ci_high", "pvalue"} == set(result)
    assert all(np.isfinite(value) for value in result.values())

    raw = pd.Series([0.01, 0.03, 0.20], index=["a", "b", "c"])
    adjusted = research["holm_adjust"](raw)
    assert (adjusted >= raw).all()
    assert adjusted.sort_values().is_monotonic_increasing


def test_discovery_evidence_reports_multiplicity_adjusted_gate(research):
    from dataclasses import replace

    dates = pd.bdate_range("2018-01-01", periods=180)
    spread = pd.Series(np.sin(np.arange(180) * np.pi / 2), index=dates)
    zscore = spread * 3.0
    candidate = research["Candidate"](
        "oscillator",
        "cointegration",
        spread,
        zscore,
        pd.DataFrame({"A": 0.5, "B": -0.5}, index=dates),
        {"stationarity_ok": True, "stable_weights": True},
    )
    bounds = research["SplitBoundaries"](dates[119], dates[149], dates[-1])
    config = replace(research["CONFIG"], bootstrap_repetitions=50)

    summary, buckets = research["discovery_evidence"]([candidate], bounds, config)

    assert {"raw_pvalue", "adjusted_pvalue", "passes", "rejection_reason"}.issubset(summary.columns)
    assert (summary["adjusted_pvalue"] >= summary["raw_pvalue"]).all()
    assert not buckets.empty


def _toy_candidate(namespace, zscore):
    dates = zscore.index
    return namespace["Candidate"](
        "toy",
        "factor_residual",
        zscore.rename("spread"),
        zscore,
        pd.DataFrame({"LONG": 0.5, "SHORT": -0.5}, index=dates),
        {"stationarity_ok": True, "stable_weights": True},
    )


def test_positions_wait_one_session_do_not_stack_and_exit_on_reversion(backtest):
    dates = pd.bdate_range("2024-01-01", periods=12)
    zscore = pd.Series(0.0, index=dates)
    zscore.iloc[2:5] = [2.2, 2.8, 0.4]
    candidate = _toy_candidate(backtest, zscore)
    split_mask = pd.Series(True, index=dates)

    positions = backtest["build_lagged_positions"](
        candidate, backtest["CONFIG"], split_mask
    )

    assert positions.iloc[:3].abs().sum(axis=1).eq(0.0).all()
    assert positions.iloc[3].abs().sum() == pytest.approx(1.0)
    assert positions.iloc[4].abs().sum() == pytest.approx(1.0)
    assert positions.iloc[5].abs().sum() == pytest.approx(0.0)
    pd.testing.assert_series_equal(positions.iloc[3], positions.iloc[4], check_names=False)


def test_signal_cannot_affect_pnl_before_second_following_close(backtest):
    dates = pd.bdate_range("2024-01-01", periods=8)
    zscore = pd.Series([0.0, 0.0, 2.5, 2.5, 0.0, 0.0, 0.0, 0.0], index=dates)
    candidate = _toy_candidate(backtest, zscore)
    positions = backtest["build_lagged_positions"](
        candidate, backtest["CONFIG"], pd.Series(True, index=dates)
    )
    returns = pd.DataFrame({"LONG": 0.01, "SHORT": -0.01}, index=dates)

    result = backtest["run_backtest"](positions, returns, backtest["CONFIG"])

    assert result.daily.loc[: dates[3], "gross_return"].eq(0.0).all()
    assert result.daily.loc[dates[4], "gross_return"] != 0.0


def test_turnover_transaction_cost_and_short_borrow_are_hand_calculated(backtest):
    dates = pd.bdate_range("2024-01-01", periods=3)
    positions = pd.DataFrame(
        {"LONG": [0.0, 0.5, 0.0], "SHORT": [0.0, -0.5, 0.0]}, index=dates
    )
    returns = pd.DataFrame(0.0, index=dates, columns=positions.columns)

    result = backtest["run_backtest"](positions, returns, backtest["CONFIG"])

    assert result.daily["turnover"].tolist() == pytest.approx([0.0, 1.0, 1.0])
    assert result.daily["transaction_cost"].sum() == pytest.approx(0.001)
    assert result.daily["borrow_cost"].sum() == pytest.approx(0.5 * 0.02 / 252)
    assert result.daily["net_return"].sum() == pytest.approx(
        -0.001 - 0.5 * 0.02 / 252
    )


def test_performance_metrics_match_toy_trade_ledger(backtest):
    dates = pd.bdate_range("2024-01-01", periods=3)
    daily = pd.DataFrame(
        {
            "gross_return": [0.01, -0.02, 0.03],
            "transaction_cost": [0.0, 0.0, 0.0],
            "borrow_cost": [0.0, 0.0, 0.0],
            "net_return": [0.01, -0.02, 0.03],
            "turnover": [0.0, 0.0, 0.0],
        },
        index=dates,
    )
    trades = pd.DataFrame({"holding_days": [2, 4], "net_pnl": [0.10, -0.05]})

    metrics = backtest["performance_metrics"](daily, trades)

    assert metrics["trade_count"] == 2
    assert metrics["average_holding_days"] == pytest.approx(3.0)
    assert metrics["hit_rate"] == pytest.approx(0.5)
    assert metrics["average_trade_pnl"] == pytest.approx(0.025)
    assert metrics["median_trade_pnl"] == pytest.approx(0.025)
    assert metrics["worst_trade"] == pytest.approx(-0.05)
    assert metrics["annualized_return"] == pytest.approx(0.02 / 3 * 252)


def test_volatility_regime_labels_use_only_prior_spy_returns(backtest):
    dates = pd.bdate_range("2024-01-01", periods=20)
    spy = pd.Series(np.linspace(-0.02, 0.02, 20), index=dates)
    changed = spy.copy()
    changed.iloc[15:] = 1.0

    labels = backtest["lagged_volatility_regime"](spy, lookback=5)
    changed_labels = backtest["lagged_volatility_regime"](changed, lookback=5)

    pd.testing.assert_series_equal(labels.loc[: dates[15]], changed_labels.loc[: dates[15]])


def test_robustness_grid_reports_only_predeclared_neighbor_values(backtest):
    from dataclasses import replace

    dates = pd.bdate_range("2024-01-01", periods=30)
    zscore = pd.Series(np.tile([0.0, 2.2, 2.3, 0.4, 0.0], 6), index=dates)
    candidate = _toy_candidate(backtest, zscore)
    returns = pd.DataFrame({"LONG": 0.001, "SHORT": -0.001}, index=dates)
    bounds = backtest["SplitBoundaries"](dates[9], dates[19], dates[-1])
    config = replace(
        backtest["CONFIG"],
        robustness_entry_z=(2.0,),
        robustness_exit_z=(0.5,),
        robustness_holding_days=(20,),
        robustness_cost_bps=(5.0,),
        robustness_borrow=(0.02,),
    )

    result = backtest["robustness_grid"](
        candidate, returns, bounds, "test", config
    )

    assert len(result) == 1
    assert result.loc[0, "entry_z"] == 2.0
    assert result.loc[0, "transaction_cost_bps"] == 5.0


def test_primary_selection_uses_statistical_gates_not_sharpe(backtest):
    evidence = pd.DataFrame(
        [
            {
                "candidate": "failed_high_sharpe",
                "passes": False,
                "adjusted_pvalue": 0.001,
                "mean_convergence": 0.05,
                "sharpe": 9.0,
            },
            {
                "candidate": "valid_weaker_pnl",
                "passes": True,
                "adjusted_pvalue": 0.02,
                "mean_convergence": 0.02,
                "sharpe": 0.5,
            },
            {
                "candidate": "valid_second",
                "passes": True,
                "adjusted_pvalue": 0.03,
                "mean_convergence": 0.04,
                "sharpe": 2.0,
            },
        ]
    )

    assert backtest["select_primary_candidate"](evidence) == "valid_weaker_pnl"
    assert backtest["select_primary_candidate"](evidence.assign(passes=False)) is None


def test_validation_gate_requires_significance_net_profit_and_trade_count(backtest):
    evidence = pd.Series({"slope": 0.1, "bootstrap_pvalue": 0.08})
    passing = backtest["BacktestResult"](
        pd.DataFrame(), pd.DataFrame(), {"net_return": 0.02, "trade_count": 15}
    )

    assert backtest["passes_validation"](evidence, passing)
    assert not backtest["passes_validation"](
        evidence, backtest["BacktestResult"](pd.DataFrame(), pd.DataFrame(), {"net_return": -0.01, "trade_count": 30})
    )
    assert not backtest["passes_validation"](
        pd.Series({"slope": 0.1, "bootstrap_pvalue": 0.11}), passing
    )


def test_final_classification_requires_test_and_robustness_gates(backtest):
    evidence = pd.Series({"slope": 0.2, "bootstrap_pvalue": 0.04})
    result = backtest["BacktestResult"](
        pd.DataFrame(), pd.DataFrame(), {"net_return": 0.03, "trade_count": 22}
    )
    robust = pd.DataFrame({"net_return": [0.01, 0.02], "mean_convergence": [0.1, 0.2]})

    assert backtest["classify_final_evidence"](evidence, result, robust) == "Robust statistical-arbitrage evidence"
    assert backtest["classify_final_evidence"](
        evidence, result, robust.assign(net_return=[0.01, -0.02])
    ) == "Interesting but weak evidence"
    assert backtest["classify_final_evidence"](None, None, None) == "No useful statistical arbitrage"


def test_negative_summary_contains_all_eight_required_fields(backtest):
    verdict = "No useful statistical arbitrage"

    summary = backtest["render_research_summary"](
        None, None, None, None, None, None, None, ["No candidate passed discovery"], verdict
    )

    for heading in (
        "1. Best representation",
        "2. Divergence definition",
        "3. Convergence evidence",
        "4. Trading rule",
        "5. Untouched out-of-sample result",
        "6. Robustness",
        "7. Main failure modes",
        "8. Verdict",
    ):
        assert heading in summary
    assert verdict in summary
