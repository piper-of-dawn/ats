# Gold Statistical-Arbitrage Research Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rebuild `notebooks/gold.ipynb` into an understandable, leakage-safe study that either identifies one statistically and economically defensible precious-metals relative-value strategy or reports a rigorous negative result.

**Architecture:** Keep all research implementation in tagged notebook code cells. A focused pytest file extracts the tagged, network-free core cells and exercises time-ordering, inference, candidate selection, and accounting with synthetic data; the remaining notebook cells acquire ATS data, run the chronological tournament, and save auditable outputs. Candidate promotion is deterministic and may return no strategy.

**Tech Stack:** Python 3.13, ATS `EquityTicker`, NumPy, pandas, Polars, SciPy, statsmodels, Matplotlib, pytest, Jupyter/nbconvert for execution.

**Spec:** `docs/superpowers/specs/2026-09-26-gold-stat-arb-research-design.md`

## Global Constraints

- Deliver the rebuilt `notebooks/gold.ipynb`; do not expand the production ATS package.
- Use ATS `EquityTicker.fetch_price_data(all_available_price_history=True)` for adjusted daily price acquisition.
- Use only the economically constrained tradable and explanatory universe in the spec.
- Use chronological 60% discovery, 20% validation, and 20% untouched-test partitions, purging forward labels at boundaries.
- Every rolling coefficient, component loading, dispersion estimate, and regime label available at date `t` must use data ending no later than `t-1`.
- A signal observed after close `t-1` may set a position at close `t`; that position first earns the close-`t` to close-`t+1` return.
- Base execution cost is 5 basis points per dollar of one-way turnover; base annualized short borrow is 2%.
- Use a fixed random seed and at least 2,000 moving-block bootstrap replications in saved final outputs.
- Adjust discovery multiplicity with Holm p-values; do not promote a candidate on an unadjusted p-value.
- Use entry `|Z| >= 2.0`, exit `|Z| <= 0.5` or sign crossing, maximum holding period 20 sessions, and no position stacking as the frozen base rule. Values 1.5/2.5, 0/0.5, and 10/40 are robustness checks only.
- At most one primary candidate reaches the untouched test. If no candidate passes, render the negative-result path without weakening gates.
- Do not claim that software validation or statistical significance guarantees future profitability.

## Review Focus

- A ticker with duplicate, non-positive, or missing prices must be diagnosed and excluded without fabricating zero returns; Task 2 tests this.
- A future observation changed after date `t` must not change a coefficient or z-score stamped at or before `t`; Task 2 tests this.
- A forward label that crosses discovery or validation boundaries must be absent; Task 3 tests this.
- A missing or rejected primary candidate must produce a complete negative-result summary rather than crash or silently choose the next-best Sharpe; Task 5 tests this.
- A position transition must incur turnover and borrow costs on the correct dates without earning pre-execution returns; Task 4 tests this.

---

### Task 1: Notebook test harness and research configuration

**Files:**
- Modify: `notebooks/gold.ipynb`
- Create: `tests/test_gold_notebook.py`

**Interfaces:**
- Consumes: notebook JSON and cells tagged `research-core`, `research-inference`, and `research-backtest`.
- Produces: `load_notebook_namespace(*tags: str) -> dict[str, object]` in the test file; notebook `ResearchConfig` and `SplitBoundaries` dataclasses; constants `TRADABLE_TICKERS`, `FACTOR_TICKERS`, and `RANDOM_SEED`.

- [ ] **Step 1: Write the failing notebook-contract test**

Create `tests/test_gold_notebook.py` with a JSON-based `load_notebook_namespace` that concatenates and executes code cells carrying requested metadata tags. Add `test_notebook_exposes_research_configuration` asserting `ResearchConfig().entry_z == 2.0`, `exit_z == 0.5`, `max_holding_days == 20`, `transaction_cost_bps == 5.0`, `annual_short_borrow == 0.02`, `bootstrap_repetitions >= 2000`, and that the required ticker constants contain `GLD`, `GDX`, `SPY`, and `UUP`.

- [ ] **Step 2: Run the contract test and verify RED**

Run: `uv run pytest tests/test_gold_notebook.py::test_notebook_exposes_research_configuration -q`

Expected: FAIL because the existing notebook has no tagged research configuration.

- [ ] **Step 3: Rebuild the notebook skeleton and configuration**

Replace `notebooks/gold.ipynb` with valid notebook JSON containing the 13 ordered sections from the spec. In the `research-core` cell define frozen dataclasses `ResearchConfig` and `SplitBoundaries`, the ticker constants, exact base and robustness values from Global Constraints, and imports. Use Markdown to state the decision standard, data limitations, and non-guarantee plainly.

- [ ] **Step 4: Run the contract test and verify GREEN**

Run: `uv run pytest tests/test_gold_notebook.py::test_notebook_exposes_research_configuration -q`

Expected: PASS.

- [ ] **Step 5: Commit the notebook contract**

```bash
git add notebooks/gold.ipynb tests/test_gold_notebook.py
git commit -m "test: define gold research notebook contract"
```

### Task 2: Market-data preparation and lagged estimators

**Files:**
- Modify: `notebooks/gold.ipynb`
- Modify: `tests/test_gold_notebook.py`

**Interfaces:**
- Consumes: `ResearchConfig`, ATS `EquityTicker`, price frames indexed by date.
- Produces: `fetch_price_panel(tickers: tuple[str, ...], ticker_factory=EquityTicker) -> tuple[pd.DataFrame, pd.DataFrame]`; `validate_price_panel(prices: pd.DataFrame) -> pd.DataFrame`; `simple_and_log_returns(prices: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]`; `chronological_boundaries(index: pd.DatetimeIndex) -> SplitBoundaries`; `rolling_ols_lagged(y: pd.Series, x: pd.DataFrame, window: int, ridge_alpha: float = 0.0) -> tuple[pd.DataFrame, pd.Series]`; `lagged_robust_zscore(values: pd.Series, window: int) -> pd.Series`.

- [ ] **Step 1: Add failing synthetic tests for data validation and lagging**

Add:

```python
def test_price_validation_rejects_nonpositive_and_does_not_fill_missing_returns(core):
    dates = pd.date_range("2025-01-01", periods=5, freq="D")
    raw = pd.DataFrame({"GLD": [100.0, 0.0, 102.0, np.nan, 104.0]}, index=dates)
    clean = core["validate_price_panel"](raw)
    simple, log = core["simple_and_log_returns"](clean)
    assert clean.loc[dates[1], "GLD"] is np.nan or pd.isna(clean.loc[dates[1], "GLD"])
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
```

Use a 12-date synthetic panel. Assert invalid prices are `NaN`/excluded rather than converted to zero returns; mutate the response from a chosen timestamp onward and assert earlier coefficients are identical; mutate the current z-score input and assert the z-score denominator/center at that timestamp are unchanged; assert nonempty ordered split masks with proportions within one observation of 60/20/20.

- [ ] **Step 2: Run the new tests and verify RED**

Run: `uv run pytest tests/test_gold_notebook.py -q`

Expected: FAIL with the five missing function names.

- [ ] **Step 3: Implement acquisition, cleaning, splitting, and lagged estimation cells**

Implement the six interfaces in the `research-core` cell. `fetch_price_panel` must call `ticker_factory(symbol).fetch_price_data(all_available_price_history=True)`, convert ATS Polars output to a date-indexed pandas series, and return an audit frame with symbol, status, first date, last date, observations, and missing count. `rolling_ols_lagged` must fit `[t-window, t-1]`, include an intercept, reject ill-conditioned OLS unless `ridge_alpha > 0`, and stamp the resulting coefficients and current residual at `t`. `lagged_robust_zscore` must calculate its median and MAD from `[t-window, t-1]`, using trailing standard deviation only when MAD is zero.

- [ ] **Step 4: Run the focused tests and verify GREEN**

Run: `uv run pytest tests/test_gold_notebook.py -q`

Expected: PASS.

- [ ] **Step 5: Commit the data and estimator foundation**

```bash
git add notebooks/gold.ipynb tests/test_gold_notebook.py
git commit -m "feat: add leakage-safe gold research estimators"
```

### Task 3: Candidate construction and robust convergence inference

**Files:**
- Modify: `notebooks/gold.ipynb`
- Modify: `tests/test_gold_notebook.py`

**Interfaces:**
- Consumes: cleaned prices/returns, lagged estimators, `SplitBoundaries`.
- Produces: `Candidate` frozen dataclass with typed fields `name: str`, `family: str`, `spread: pd.Series`, `zscore: pd.Series`, `leg_weights: pd.DataFrame`, and `diagnostics: dict[str, object]`; `make_cointegration_candidate(log_prices: pd.DataFrame, left: str, right: str, estimation_window: int, normalization_window: int, discovery_mask: pd.Series) -> Candidate | None`; `make_factor_candidate(log_returns: pd.DataFrame, target: str, factors: tuple[str, ...], estimation_window: int, accumulation_window: int, normalization_window: int, discovery_mask: pd.Series, ridge_alpha: float = 0.0) -> Candidate | None`; `make_pca_candidates(log_returns: pd.DataFrame, assets: tuple[str, ...], estimation_window: int, accumulation_window: int, normalization_window: int, discovery_mask: pd.Series) -> list[Candidate]`; `purged_forward_convergence(candidate: Candidate, horizons: tuple[int, ...], split: str, boundaries: SplitBoundaries) -> pd.DataFrame`; `nonoverlapping_extreme_events(evidence: pd.DataFrame, horizon: int, threshold: float) -> pd.DataFrame`; `moving_block_mean_test(values: pd.Series, repetitions: int, block_length: int, seed: int) -> dict[str, float]`; `holm_adjust(pvalues: pd.Series) -> pd.Series`; `discovery_evidence(candidates: list[Candidate], boundaries: SplitBoundaries, config: ResearchConfig) -> tuple[pd.DataFrame, pd.DataFrame]`.

- [ ] **Step 1: Add failing candidate and inference tests**

Add tests using seeded synthetic cointegrated prices, a target with known factor exposure plus a transient residual displacement, and a two-factor return panel. Assert candidate coefficients are lagged, leg weights have absolute sum 1 when present, future convergence labels never cross their requested split boundary, non-overlapping events are at least `horizon` rows apart, `holm_adjust` is monotone and never below raw p-values, and `moving_block_mean_test` returns finite `mean`, `ci_low`, `ci_high`, and one-sided `pvalue`.

- [ ] **Step 2: Run candidate tests and verify RED**

Run: `uv run pytest tests/test_gold_notebook.py -q`

Expected: FAIL because the candidate and inference interfaces do not exist.

- [ ] **Step 3: Implement the three candidate families**

In `research-core`, implement the restricted pair list and the exact windows from the spec. Cointegration candidates use lagged rolling log-price regressions and discovery-only Engle-Granger diagnostics. Factor candidates use lagged OLS, with a declared ridge fallback only for poor conditioning, then `K`-day residual sums and lagged robust z-scores. PCA candidates fit trailing standardized returns, cap retained components at two, require at least 70% explained variance, and convert reconstruction residuals into normalized tradable leg weights.

- [ ] **Step 4: Implement convergence evidence and multiplicity control**

In `research-inference`, implement the remaining interfaces. Produce horizon results for 1, 5, 10, 20, and 40 sessions, absolute-divergence buckets, monotonic-slope HAC inference, extreme-event moving-block inference with block length 40, non-overlapping sensitivity results, and Holm-adjusted discovery p-values. Return explicit rejection reasons for insufficient observations, unstable weights, failed stationarity, fewer than 30 discovery events, nonpositive monotonic slope, or adjusted `p >= 0.05`.

- [ ] **Step 5: Run focused tests and verify GREEN**

Run: `uv run pytest tests/test_gold_notebook.py -q`

Expected: PASS.

- [ ] **Step 6: Commit candidate research machinery**

```bash
git add notebooks/gold.ipynb tests/test_gold_notebook.py
git commit -m "feat: evaluate precious metals convergence candidates"
```

### Task 4: Trading state machine, costs, and performance metrics

**Files:**
- Modify: `notebooks/gold.ipynb`
- Modify: `tests/test_gold_notebook.py`

**Interfaces:**
- Consumes: one `Candidate`, simple returns, `ResearchConfig`, a split mask.
- Produces: `Trade` frozen dataclass; `BacktestResult` frozen dataclass containing `daily: pd.DataFrame`, `trades: pd.DataFrame`, and `metrics: dict[str, float]`; `build_lagged_positions(candidate: Candidate, config: ResearchConfig, split_mask: pd.Series) -> pd.DataFrame`; `run_backtest(positions: pd.DataFrame, simple_returns: pd.DataFrame, config: ResearchConfig) -> BacktestResult`; `performance_metrics(daily: pd.DataFrame, trades: pd.DataFrame) -> dict[str, float]`; `robustness_grid(candidate: Candidate, returns: pd.DataFrame, boundaries: SplitBoundaries, split: str, config: ResearchConfig) -> pd.DataFrame`; `regime_performance(daily: pd.DataFrame, spy_returns: pd.Series, volatility_lookback: int = 63) -> pd.DataFrame`.

- [ ] **Step 1: Add failing lifecycle and accounting tests**

Add deterministic tests asserting: no position exists before the session after a threshold crossing; a signal at `t` cannot affect P&L before `t+2`; no entry stacks while active; positions exit at `|Z| <= 0.5`, sign crossing, or day 20; absolute leg weights sum to 1; turnover equals the absolute change in leg weights; 5 bps costs apply to one-way turnover; annualized 2% borrow applies only to short market value; trade count, holding period, drawdown, hit rate, and tail statistics match a hand-calculated toy ledger; and changing future `SPY` returns cannot alter an earlier high/low-volatility regime label.

- [ ] **Step 2: Run backtest tests and verify RED**

Run: `uv run pytest tests/test_gold_notebook.py -q`

Expected: FAIL because trading interfaces do not exist.

- [ ] **Step 3: Implement the trading state machine and ledger**

Implement the four interfaces in `research-backtest`. Keep entry/exit logic in one state-machine function. Store gross return, turnover, transaction cost, borrow cost, net return, gross exposure, and net exposure as separate daily columns. Build a trade ledger with entry/exit dates, direction, duration, gross P&L, each cost, and net P&L.

- [ ] **Step 4: Implement metrics and local robustness**

Calculate all metrics named in the spec, returning `NaN` with an explanatory display note where sample size makes annualization or expected shortfall misleading. `robustness_grid` varies only adjacent predeclared values and never feeds results back into candidate choice. `regime_performance` defines stress from lagged 63-session `SPY` volatility and reports both high/low-volatility and calendar subperiod results.

- [ ] **Step 5: Run focused tests and verify GREEN**

Run: `uv run pytest tests/test_gold_notebook.py -q`

Expected: PASS.

- [ ] **Step 6: Commit the executable backtest**

```bash
git add notebooks/gold.ipynb tests/test_gold_notebook.py
git commit -m "feat: add cost-aware relative value backtest"
```

### Task 5: Deterministic tournament, validation gate, and verdict rendering

**Files:**
- Modify: `notebooks/gold.ipynb`
- Modify: `tests/test_gold_notebook.py`

**Interfaces:**
- Consumes: discovery evidence, candidate objects, validation backtests, and test backtest.
- Produces: `select_primary_candidate(evidence: pd.DataFrame) -> str | None`; `passes_validation(evidence: pd.Series, backtest: BacktestResult) -> bool`; `classify_final_evidence(test_evidence: pd.Series | None, test_backtest: BacktestResult | None, robustness: pd.DataFrame | None) -> str`; `render_research_summary(primary_name: str | None, divergence_definition: str | None, discovery: pd.Series | None, validation: pd.Series | None, test_evidence: pd.Series | None, test_backtest: BacktestResult | None, robustness: pd.DataFrame | None, failure_modes: list[str], verdict: str) -> str`.

- [ ] **Step 1: Add failing selection and negative-path tests**

Assert selection requires every discovery gate, ranks passing candidates by adjusted p-value then convergence effect rather than Sharpe, and returns `None` when none pass. Assert validation requires same-sign monotonic convergence, one-sided block-bootstrap `p < 0.10`, positive net return, and at least 15 completed trades. Assert “Robust statistical-arbitrage evidence” requires test convergence `p < 0.05`, positive net return after base costs, at least 20 test trades, and no sign reversal across the central robustness neighborhood; otherwise classification is weak or no useful statistical arbitrage. Assert the `None` path renders all eight requested summary fields without accessing test results.

- [ ] **Step 2: Run selection tests and verify RED**

Run: `uv run pytest tests/test_gold_notebook.py -q`

Expected: FAIL because selection and summary interfaces do not exist.

- [ ] **Step 3: Implement deterministic selection, validation, and verdict functions**

Implement the four interfaces in `research-inference`. Use no fallback that relaxes gates. The summary must name rejected families, exact selected divergence and rule when one exists, discovery/validation/test evidence, costs, robustness, limitations, and exactly one permitted verdict.

- [ ] **Step 4: Run focused tests and verify GREEN**

Run: `uv run pytest tests/test_gold_notebook.py -q`

Expected: PASS.

- [ ] **Step 5: Commit the no-forced-result research path**

```bash
git add notebooks/gold.ipynb tests/test_gold_notebook.py
git commit -m "feat: add deterministic stat arb verdict gates"
```

### Task 6: Assemble and execute the empirical notebook

**Files:**
- Modify: `notebooks/gold.ipynb`

**Interfaces:**
- Consumes: all tested notebook interfaces and live ATS market data.
- Produces: saved notebook outputs containing data audit, splits, candidate diagnostics, discovery selection, validation, a single untouched-test evaluation when eligible, robustness, plots, metrics, and final eight-part research summary.

- [ ] **Step 1: Add the top-to-bottom research orchestration cells**

Fetch the declared universe, print data audit and split boundaries, build only the restricted candidate families, run discovery evidence, select at most one primary candidate, run the validation gate, and run the untouched test only for a validated primary. Make the negative path explicit. Add only plots that answer integration/stability, convergence-shape, hedge-stability, cost, drawdown, or regime questions.

- [ ] **Step 2: Execute the notebook in a clean kernel**

Run: `uv run --with jupyter jupyter nbconvert --to notebook --execute --inplace --ExecutePreprocessor.timeout=1800 notebooks/gold.ipynb`

Expected: exit code 0, no traceback, final outputs produced with at least 2,000 bootstrap repetitions. If market-data network access is sandboxed, rerun the same command with the required network approval; do not substitute stale data silently.

- [ ] **Step 3: Audit saved results before interpretation**

Run a read-only notebook audit that asserts: every code cell has an execution count, no output contains `Traceback`, the printed test dates begin after validation, exactly zero or one primary candidate is tested, and the final verdict text is present. Manually compare the stated rule and metrics with the saved tables.

- [ ] **Step 4: Run the focused and complete test suites**

Run: `uv run pytest tests/test_gold_notebook.py -q`

Expected: PASS.

Run: `uv run pytest -q`

Expected: all tests PASS; report every failure by name if the repository has a pre-existing or unrelated failure.

- [ ] **Step 5: Inspect the final diff and notebook readability**

Run: `git diff --check HEAD~1 -- notebooks/gold.ipynb tests/test_gold_notebook.py` and inspect every Markdown heading, table, equation, plot title, and final conclusion for consistency. Confirm no secret, token, temporary cache, or generated environment file is staged.

- [ ] **Step 6: Commit the executed research notebook**

```bash
git add notebooks/gold.ipynb tests/test_gold_notebook.py
git commit -m "research: evaluate gold relative value strategies"
```

### Task 7: Final verification and handoff

**Files:**
- Verify: `notebooks/gold.ipynb`
- Verify: `tests/test_gold_notebook.py`
- Verify: `docs/superpowers/specs/2026-09-26-gold-stat-arb-research-design.md`

**Interfaces:**
- Consumes: committed implementation and saved empirical outputs.
- Produces: evidence-backed completion report distinguishing software checks from the empirical strategy verdict.

- [ ] **Step 1: Verify repository and artifact state**

Run: `git status --short`, `git log -7 --oneline`, and `git diff HEAD^ --check`. Confirm only intended artifacts were committed and all unrelated pre-existing worktree changes remain untouched.

- [ ] **Step 2: Re-read the final result as a skeptical reviewer**

Confirm the untouched-test result, costs, trade count, adjusted significance, monotonic relationship, robustness neighborhood, and limitations support the exact verdict. Downgrade the prose if any gate is not met; never upgrade a gate after seeing test performance.

- [ ] **Step 3: Report completion with exact validation boundaries**

Link the notebook and focused test. State the selected representation and rule only if one passed, quote the saved out-of-sample net metrics, give the permitted evidence verdict, list executed verification commands, and disclose any failed fetch, rejected family, test failure, or insufficient sample.
