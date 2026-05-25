# GRAPH_REPORT

- Base commit: `b2f0728`
- Target commit: `5e15d3e`
- Community count: `9`
- God nodes: `10`
- Surprising edges: `5`

## God Nodes

- `EquityTicker` (`ats_ticker_equityticker`), degree `16`
- `_connect()` (`dataio_supabase_integration_connect`), degree `12`
- `get_nav_context()` (`dashboard_data_get_nav_context`), degree `10`
- `fetch_rows_for_date()` (`dataio_supabase_integration_fetch_rows_for_date`), degree `7`
- `main()` (`fundamentals_combined_score_main`), degree `6`
- `get_table_columns()` (`dataio_supabase_integration_get_table_columns`), degree `6`
- `weights()` (`fundamentals_analyst_ratings_weights`), degree `5`
- `CBS()` (`fundamentals_analyst_ratings_cbs`), degree `5`
- `fetch_table()` (`dataio_supabase_integration_fetch_table`), degree `5`
- `get_dashboard_context()` (`dashboard_data_get_dashboard_context`), degree `5`

## Surprising Connections

- `main()` -> `run_cbs_parallel()` via `calls` (`EXTRACTED`)
  Why: connects across different repos/directories; peripheral node `run_cbs_parallel()` unexpectedly reaches hub `main()`
  Files: `after/src/ats/fundamentals/combined_score.py`, `before/src/ats/fundamentals/analyst_ratings.py`
- `get_dashboard_context()` -> `_join_ratings()` via `calls` (`EXTRACTED`)
  Why: connects across different repos/directories; peripheral node `_join_ratings()` unexpectedly reaches hub `get_dashboard_context()`
  Files: `after/dashboard/data.py`, `before/dashboard/data.py`
- `main()` -> `fetch_rows_for_date()` via `calls` (`INFERRED`)
  Why: inferred connection - not explicitly stated in source; bridges separate communities
  Files: `after/src/ats/fundamentals/combined_score.py`, `after/src/ats/dataIO/supabase_integration.py`
- `loadDashboardSequentially()` -> `waitForChartLibraries()` via `calls` (`EXTRACTED`)
  Why: connects across different repos/directories
  Files: `before/dashboard/static/js/dashboard.js`, `after/dashboard/static/js/dashboard.js`
- `loadDashboardSequentially()` -> `loadComponentMarkup()` via `calls` (`EXTRACTED`)
  Why: connects across different repos/directories
  Files: `before/dashboard/static/js/dashboard.js`, `after/dashboard/static/js/dashboard.js`

## Communities

### Community 0
- Cohesion: `0.19`
- Members: `after_src_ats_ticker_py`, `ats_ticker_equityticker`, `ats_ticker_equityticker_compute_beta`, `ats_ticker_equityticker_fetch_price_data`, `ats_ticker_equityticker_get_idiosyncratic_returns`, `ats_ticker_equityticker_get_long_term_momentum_signal`, `ats_ticker_equityticker_get_ltm_series`, `ats_ticker_equityticker_get_short_term_momentum_signal`, `ats_ticker_equityticker_get_stm_series`, `ats_ticker_equityticker_init`, `ats_ticker_equityticker_join_with_market_index`, `ats_ticker_equityticker_make_log_returns`, ... (+7 more)

### Community 1
- Cohesion: `0.25`
- Members: `after_src_ats_dataio_supabase_integration_py`, `dataio_supabase_integration_batch_insert`, `dataio_supabase_integration_batch_insert_polars_df`, `dataio_supabase_integration_connect`, `dataio_supabase_integration_create_relation`, `dataio_supabase_integration_delete_all_rows`, `dataio_supabase_integration_delete_rows_by_values`, `dataio_supabase_integration_empty_table_frame`, `dataio_supabase_integration_fetch_recent_dates`, `dataio_supabase_integration_fetch_rows_for_date`, `dataio_supabase_integration_fetch_table`, `dataio_supabase_integration_get_conn_params`, ... (+7 more)

### Community 2
- Cohesion: `0.17`
- Members: `after_dashboard_static_js_dashboard_js`, `js_dashboard_applydisplaylimit`, `js_dashboard_comparesortvalues`, `js_dashboard_gettheme`, `js_dashboard_hydrateslot`, `js_dashboard_inittablesortcontrols`, `js_dashboard_initthemetoggle`, `js_dashboard_loadcomponentmarkup`, `js_dashboard_loaddashboardsequentially`, `js_dashboard_observelazyslots`, `js_dashboard_parsesortvalue`, `js_dashboard_rendererrorstate`, ... (+7 more)

### Community 3
- Cohesion: `0.22`
- Members: `after_dashboard_data_py`, `dashboard_data_compute_max_drawdown`, `dashboard_data_compute_max_upside`, `dashboard_data_fetch_rows_for_selected_date`, `dashboard_data_find_column_name`, `dashboard_data_format_chart_label`, `dashboard_data_format_currency`, `dashboard_data_format_header_date`, `dashboard_data_format_percent`, `dashboard_data_format_raw_date`, `dashboard_data_format_signed_currency`, `dashboard_data_format_signed_percent`, ... (+6 more)

### Community 4
- Cohesion: `0.18`
- Members: `after_src_ats_helpers_py`, `after_tests_test_helpers_py`, `ats_helpers_calibrate`, `ats_helpers_compute_ema_signal`, `ats_helpers_compute_garch_sigma2`, `ats_helpers_ema_volatility`, `ats_helpers_garch_mle_calibration`, `ats_helpers_garch_volatility`, `ats_helpers_validate_garch_params`, `tests_test_helpers_test_calibrate_decorator_accepts_custom_calibration_function`, `tests_test_helpers_test_compute_ema_signal_accepts_garch_none_args`, `tests_test_helpers_test_compute_ema_signal_defaults_to_ema_volatility`, ... (+2 more)

### Community 5
- Cohesion: `0.27`
- Members: `after_src_ats_fundamentals_analyst_price_targets_py`, `after_src_ats_fundamentals_combined_score_py`, `fundamentals_analyst_price_targets_median_centered_score`, `fundamentals_analyst_ratings_run_cbs_parallel`, `fundamentals_combined_score_add_metric_quantiles`, `fundamentals_combined_score_add_weighted_combined_score`, `fundamentals_combined_score_compute_combined_score`, `fundamentals_combined_score_main`, `fundamentals_combined_score_metric_quantile_name`, `fundamentals_combined_score_rationale_66`, `fundamentals_combined_score_resolve_metric_columns`

### Community 6
- Cohesion: `0.56`
- Members: `after_src_ats_fundamentals_analyst_ratings_py`, `fundamentals_analyst_ratings_agreement`, `fundamentals_analyst_ratings_c_t`, `fundamentals_analyst_ratings_cbs`, `fundamentals_analyst_ratings_direction`, `fundamentals_analyst_ratings_mu_t`, `fundamentals_analyst_ratings_sample_confidence`, `fundamentals_analyst_ratings_stability`, `fundamentals_analyst_ratings_weights`

### Community 7
- Cohesion: `0.67`
- Members: `after_src_ats_dataio_utils_py`, `dataio_utils_build_metric_pivot_frame`, `dataio_utils_with_parallel_runner`

### Community 8
- Cohesion: `1.0`
- Members: `after_jenkins_casc_jobs_ats_jobs_groovy`
