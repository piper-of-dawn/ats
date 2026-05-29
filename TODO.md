# TODO

## Trading Signal Backlog

- Add `eps_revision_score` from Yahoo `get_eps_revisions()`.
- Add `upgrade_downgrade_score` from Yahoo `get_upgrades_downgrades()`.
- Add `earnings_surprise_score` from Yahoo `get_earnings_history()`.
- Add `insider_flow_score` from Yahoo insider purchase/transaction methods.
- Add `short_term_reversal` from price history.
- Add `realized_volatility` from price history.
- Consider `days_to_next_earnings` as a risk filter, not a primary alpha signal.

Avoid factors that are strongly affected by accounting treatment or reporting methods, such as ROE, margins, debt ratios, free cash flow metrics, enterprise value ratios, book value ratios, and balance-sheet-heavy metrics.
