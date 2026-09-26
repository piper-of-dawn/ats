# Gold and Precious-Metals Statistical-Arbitrage Research Design

## Objective

Replace `notebooks/gold.ipynb` with a readable, reproducible research notebook that determines whether a statistically defensible and economically tradable relative-value strategy exists in a liquid precious-metals universe.

The notebook must not manufacture a profitable result. Its final verdict must be one of:

1. **Robust statistical-arbitrage evidence** — convergence is repeatable, survives a genuinely untouched test period, and remains viable after reasonable costs.
2. **Interesting but weak evidence** — some convergence exists, but significance, stability, sample size, or economics are insufficient.
3. **No useful statistical arbitrage** — the apparent relationship fails out of sample, after costs, or under robustness testing.

Statistical significance is necessary but not sufficient. A candidate must also have an economic rationale, a stable effect shape, adequate trade count, and realistic net performance.

## Scope

The deliverable is a rebuilt `notebooks/gold.ipynb`. Existing notebook cells need not be preserved. The production ATS package will not be expanded unless notebook implementation reveals a small, independently useful defect that blocks the work; such a change would require separate approval.

The notebook will:

- use ATS's `EquityTicker` to fetch adjusted daily prices and ATS return helpers where their behavior is appropriate;
- use NumPy, Polars or pandas, SciPy, statsmodels, and Matplotlib already available in the project;
- keep model and backtest functions short, named for their purpose, documented, and separated from narrative cells;
- display the data cutoff, sample sizes, split boundaries, statistical results, assumptions, and rejected candidates;
- contain deterministic assertions for the research machinery and a fixed random seed for resampling;
- avoid external services other than the market-data source already used by `EquityTicker`.

## Research Universe

### Tradable candidates

The initial universe is deliberately narrow and economically related:

- bullion ETFs: `GLD`, `IAU`, `SLV`;
- mining ETFs: `GDX`, `GDXJ`, `SIL`;
- `SILJ` may be included only if its available history and missing-data profile are adequate.

This is not permission to search arbitrary Yahoo Finance tickers. Every tested relationship must fit one of these economic links:

- near-substitute bullion funds;
- senior versus junior miners;
- miners versus the metal exposure that drives their revenues;
- gold versus silver or gold miners versus silver miners where a common precious-metals factor is plausible.

### Explanatory factors

The notebook may use the following as explanatory or hedge instruments:

- `SPY` for broad equity exposure;
- `UUP` for US-dollar exposure;
- `IEF` and `TIP` for nominal-rate and inflation-linked-bond exposure.

An explanatory instrument enters a model only when its economic role is stated. A factor that improves in-sample fit but has no defensible role will be rejected.

### Data handling

- Fetch maximum available history with `EquityTicker.fetch_price_data(all_available_price_history=True)`.
- Preserve each instrument's actual history; do not forward-fill prices or returns.
- Use pair- or model-specific complete cases rather than forcing every candidate onto the shortest global history.
- Print start date, end date, observation count, and missingness for every instrument.
- Reject models with fewer than five years of usable daily observations or too little history for all split and warm-up requirements.
- State that the current-survivor ETF universe creates survivorship limitations; do not present the result as a universe-wide discovery.

## Chronological Research Design

For each eligible candidate sample, reserve the first 60% of observations for discovery, the next 20% for validation, and the final 20% for the untouched test. Boundaries are computed once from dates and printed before candidate results.

Forward-response observations that cross a split boundary are purged. Rolling models may use earlier observations as warm-up for a later split, but no outcome from the later split may influence model choice, normalization, thresholds, or hedge parameters.

The workflow is:

1. **Discovery:** diagnose integration, estimate effect shape, reject implausible candidates, and select one primary representation and trading rule.
2. **Validation:** test the frozen primary choice and nearby robustness values. Validation may reject the strategy but may not be used as an open-ended search area.
3. **Test:** freeze all choices, expose the final 20% once, and report the result regardless of outcome.

If an implementation defect is found after the test is exposed, it may be corrected and rerun only when the correction does not use test performance to change the model or parameters. The correction and rerun must be disclosed in the notebook.

## Candidate Families

The notebook will compare three conceptually different families. It will not mechanically enumerate all transformations or all ticker pairs.

### 1. Economically constrained cointegrating spreads

Candidate price relationships are restricted to pairs such as `GLD`/`IAU`, `GDX`/`GDXJ`, `GDX`/`GLD`, and `SIL`/`SLV`.

For log prices \(p_{A,t}\) and \(p_{B,t}\), a trailing relationship is:

\[
p_{A,s} = \alpha_t + \beta_t p_{B,s} + \varepsilon_s,
\qquad s \in [t-W, t-1].
\]

The tradable spread at date \(t\) is:

\[
S_t = p_{A,t} - \hat\alpha_t - \hat\beta_t p_{B,t},
\]

where every coefficient is estimated using data ending at \(t-1\). Candidate windows are limited to approximately one and two trading years (252 and 504 sessions). A pair is eligible only if:

- component log prices have integration behavior consistent with the proposed test;
- discovery-period residual stationarity is supported by Engle-Granger or ADF diagnostics with the regression-estimated critical-value caveat handled correctly;
- the hedge ratio and residual half-life are reasonably stable through time;
- validation does not show an obvious structural break;
- the relationship remains economically meaningful after costs.

ADF significance on already stationary returns will not be described as cointegration evidence.

### 2. Rolling factor-residual divergence

For a mining or metals ETF, estimate a trailing return model using a small economically selected factor set:

\[
r_{i,s} = \alpha_t + \boldsymbol\beta_t^\top \mathbf f_s + \varepsilon_{i,s},
\qquad s \in [t-W, t-1].
\]

The base factor sets are limited to:

- bullion return plus `SPY` for miners;
- bullion return, `SPY`, and `UUP` when dollar exposure is materially useful;
- `IEF`/`TIP` only when discovery diagnostics show stable incremental explanatory value.

No factor is selected from the test set. Rolling OLS is the default because its hedge weights are transparent. Ridge regression may replace OLS only if discovery-period conditioning is poor, with its penalty fixed inside discovery.

Define a cumulative residual displacement over \(K\) days:

\[
D_t^{(K)} = \sum_{j=0}^{K-1} \varepsilon_{t-j}.
\]

Standardize it using only prior residual displacements:

\[
Z_t = \frac{D_t - \operatorname{median}(D_{t-L:t-1})}
{1.4826\,\operatorname{MAD}(D_{t-L:t-1})},
\]

with a trailing standard deviation fallback if MAD is zero. Discovery considers only \(K\in\{5,10,20\}\), estimation windows \(W\in\{252,504\}\), and normalization windows \(L\in\{126,252\}\).

### 3. Rolling common-factor/PCA residuals

Within a coherent basket such as miners or precious-metals ETFs, estimate rolling principal components on standardized returns ending at \(t-1\). Retain the smallest number of components explaining at least 70% of discovery-period rolling variance, capped at two components. Reconstruct each asset's expected return and use its residual displacement as the divergence state.

PCA loadings and scaling must be trailing and lagged. The family is rejected if signs and exposures are too unstable to yield an interpretable hedge or if its apparent edge is driven by one instrument or subperiod.

## Convergence Evidence

For every promising divergence variable \(Z_t\), define signed convergence over horizon \(h\) so that positive values mean movement back toward equilibrium:

\[
C_{t,h} = -\operatorname{sign}(Z_t)\left(S_{t+h}-S_t\right)
\]

for price spreads, with the analogous future cumulative factor-residual return for residual strategies.

Use forward horizons \(h\in\{1,5,10,20,40\}\). The analysis must show:

- sample count and mean/median convergence by absolute-divergence bucket;
- a monotonic-slope estimate relating \(|Z_t|\) to subsequent signed convergence;
- extreme-versus-normal divergence comparisons;
- confidence intervals and p-values using heteroskedasticity/autocorrelation-consistent inference where suitable;
- moving-block bootstrap confidence intervals with a fixed seed and block length tied to the longest tested horizon;
- non-overlapping event samples as a sensitivity check against overlapping-forward-window inflation.

The discovery family of candidate/horizon tests must use Holm-adjusted p-values or a bootstrap maximum-statistic correction. A candidate is not promoted because one unadjusted p-value happens to fall below 0.05.

A promising candidate should exhibit an interpretable increase in convergence as \(|Z_t|\) rises. A result confined to one threshold or one sparse bucket is weak evidence even if its aggregate backtest is profitable.

## Candidate Selection

Selection is hierarchical rather than based on the highest Sharpe ratio:

1. Economic relationship and sufficient history.
2. Correct integration or stationarity behavior for the representation.
3. Stable trailing parameters and no obvious structural break.
4. Monotonic convergence shape and multiplicity-adjusted discovery evidence.
5. At least 30 discovery events at a usable threshold.
6. Validation persistence after costs.

At most one primary strategy is taken to the untouched test. A conceptually distinct secondary strategy may be reported, but it will not be combined with the primary strategy merely to improve statistics.

If no candidate satisfies the gates, the notebook stops the strategy-selection path and reports weak or negative evidence. It must not loosen criteria until something passes.

## Trading Rule and Execution

The exact rule will be frozen from discovery and validation, but every eligible rule follows these constraints:

- **Entry:** take the convergence trade when \(|Z_t|\) crosses a discovery-selected threshold from below. Candidate thresholds are limited to 1.5, 2.0, and 2.5.
- **Direction:** short the positive spread/residual and buy the negative spread/residual.
- **Exit:** close when \(|Z_t|\) falls below a fixed exit band of 0 or 0.5, when the spread changes sign, or at a maximum holding period of 10, 20, or 40 sessions. The precise combination is selected before test exposure.
- **No stacking:** one active position per strategy. A new event cannot add leverage while a trade is open.
- **Hedge sizing:** use the lagged rolling coefficients or PCA hedge. Normalize each target portfolio so the sum of absolute leg weights is 1.0.
- **Timing:** a signal available after close \(t-1\) may set positions at close \(t\); those positions first earn the close-\(t\) to close-\(t+1\) return. This explicit lag prevents same-close execution leakage.
- **Costs:** deduct 5 basis points per dollar of one-way turnover in the base case, with 2 and 10 basis-point sensitivities.
- **Short borrow:** deduct a base annualized 2% charge on short market value, with 0% and 5% sensitivities.
- **Exposure:** report gross and net exposure. Do not hide residual market, dollar, duration, or bullion beta.

The backtest will compute costs from changes in actual leg weights, including entry, re-hedging, and exit. Gross performance, transaction costs, borrow costs, and net performance must be shown separately.

## Performance Evaluation

For discovery, validation, test, and the full chronologically simulated path, report:

- number of completed trades;
- average and median holding period;
- annualized return and volatility;
- Sharpe and Sortino ratios;
- maximum drawdown;
- hit rate;
- average and median trade P&L;
- worst trade, 5th-percentile trade P&L, and expected shortfall where sample size permits;
- turnover;
- gross, transaction-cost, borrow-cost, and net returns;
- performance by major subperiod and by predefined volatility/stress regime.

Annualized metrics must not be emphasized when the test contains too few trades. Confidence intervals or bootstrap distributions should accompany the main performance estimates.

## Robustness and Stability

Robustness analysis is local rather than a second optimization exercise. Around the frozen rule, test:

- the adjacent estimation window;
- adjacent accumulation and holding horizons;
- adjacent entry and exit thresholds;
- 2, 5, and 10 basis-point transaction costs;
- 0%, 2%, and 5% annualized short-borrow costs;
- early and late subperiods;
- high- and low-volatility regimes defined using lagged information;
- equity-stress versus normal periods using an ex ante rolling `SPY` volatility rule.

The notebook should prefer a broad region of acceptable outcomes. A narrow isolated optimum, sign reversal, collapsing trade count, or dependence on one crisis period is a failure mode.

Parameter stability will be displayed with compact plots of rolling hedge ratios, residual scale, rolling correlations, and cumulative net P&L. Plots must answer a stated statistical question; decorative charts are excluded.

## Leakage and Research-Safety Controls

The notebook will state and enforce these invariants:

- rolling estimates at time \(t\) use data ending no later than \(t-1\);
- normalization is trailing, never full-sample;
- factor selection and parameter selection never use the test set;
- forward labels crossing split boundaries are purged;
- execution positions are lagged relative to signals;
- overlapping observations are not treated as independent in inference;
- the test is not repeatedly queried during development;
- candidate multiplicity is adjusted rather than ignored;
- missing prices are not imputed into fake zero returns.

Assertions will verify split ordering, absence of future timestamps in rolling fits, position lagging, gross-exposure normalization, cost arithmetic, and the absence of overlapping event dates in the non-overlapping sensitivity sample.

## Notebook Structure

The rebuilt notebook will use these sections:

1. Research question and decision standard.
2. Imports, deterministic configuration, and assumptions.
3. ATS API audit and data acquisition.
4. Data quality and chronological splits.
5. Reusable leakage-safe research functions.
6. Deterministic self-tests of those functions.
7. Discovery diagnostics for the three candidate families.
8. Convergence shape, robust inference, and candidate rejection table.
9. Frozen strategy specification.
10. Validation results and local robustness.
11. Untouched out-of-sample test.
12. Trading performance, regimes, and failure modes.
13. Concise final research summary.

The final summary must state:

1. best representation found;
2. exact mathematical divergence definition;
3. evidence that divergence predicts convergence;
4. exact trading rule;
5. untouched out-of-sample results after costs;
6. robustness results;
7. main failure modes and limitations;
8. one of the three permitted evidence verdicts.

## Error Handling and Reproducibility

- A missing non-core ticker is logged and excluded; missing core data aborts the relevant candidate family with a clear explanation.
- Empty downloads, duplicate dates, non-positive prices, non-finite returns, insufficient warm-up, singular regressions, and zero dispersion receive explicit checks.
- Singular or ill-conditioned models are rejected or use the predeclared ridge fallback; they are never silently accepted.
- Bootstrap random state is fixed and printed.
- Expensive bootstrap counts may have a clearly labeled fast development value, but final saved outputs must use at least 2,000 replications.
- The notebook should execute top-to-bottom in the repository's `uv` environment and retain its final outputs for audit.

## Validation Before Completion

Before the notebook is described as complete:

1. Run the notebook top-to-bottom in a clean kernel.
2. Confirm every deterministic self-test passes.
3. Confirm all model coefficients and signals obey the lag invariant.
4. Run `uv run pytest -q` and report every failure, including unrelated pre-existing failures.
5. Inspect the saved notebook for tracebacks, stale outputs, contradictory conclusions, and accidental test-set tuning.
6. Check that the narrative verdict agrees with the displayed test and cost results.

Passing software checks does not establish a profitable strategy. The empirical verdict is determined only by the saved out-of-sample evidence.
