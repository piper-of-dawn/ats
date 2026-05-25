import polars as pl
import json
from pathlib import Path


class FilterableDF(pl.DataFrame):
    """Base Polars DataFrame subclass with common filter methods."""

    _cls = None  # Subclasses must set this to their own class

    def filter_ticker(self, ticker: str):
        """Filter by ticker symbol."""
        filtered = super().filter(pl.col("ticker") == ticker)
        return self._cls(filtered)

    def filter_date_range(self, start_date: str, end_date: str, date_col: str = "date"):
        """Filter by date range (inclusive)."""
        filtered = super().filter(
            (pl.col(date_col) >= start_date) & (pl.col(date_col) <= end_date)
        )
        return self._cls(filtered)


class FundamentalsDF(FilterableDF):
    """Polars DataFrame subclass for fundamentals data."""

    _cls = None

    def __init__(self, data):
        super().__init__(data)
        FundamentalsDF._cls = FundamentalsDF

    def filter_ticker_date(self, ticker: str, date: str) -> "FundamentalsDF":
        """Filter by ticker and date."""
        filtered = super().filter((pl.col("ticker") == ticker) & (pl.col("date") == date))
        return FundamentalsDF(filtered)

    def filter_date(self, date: str) -> "FundamentalsDF":
        """Filter by date."""
        filtered = super().filter(pl.col("date") == date)
        return FundamentalsDF(filtered)


def _get_value(d: dict, key: str, default=None):
    """Safely get value from nested metric dict."""
    return d.get(key, {}).get('value', default)


def pnl_metrics(result: dict) -> dict:
    """Extract key P&L metrics from result. Returns None for missing metrics."""
    income_stmt = result['financials'].get('income_statement', {})

    return {
        'revenues': _get_value(income_stmt, 'revenues'),
        'operating_expenses': _get_value(income_stmt, 'operating_expenses'),
        'operating_income': _get_value(income_stmt, 'operating_income_loss'),
        'income_before_tax': _get_value(income_stmt, 'income_loss_from_continuing_operations_before_tax'),
        'income_tax': _get_value(income_stmt, 'income_tax_expense_benefit'),
        'net_income': _get_value(income_stmt, 'net_income_loss'),
        'diluted_eps': _get_value(income_stmt, 'diluted_earnings_per_share'),
        'basic_eps': _get_value(income_stmt, 'basic_earnings_per_share'),
    }


def balance_sheet_metrics(result: dict) -> dict:
    """Extract key balance sheet metrics from result. Returns None for missing metrics."""
    balance_sheet = result['financials'].get('balance_sheet', {})

    return {
        'total_assets': _get_value(balance_sheet, 'assets'),
        'current_assets': _get_value(balance_sheet, 'assets_current'),
        'total_liabilities': _get_value(balance_sheet, 'liabilities'),
        'current_liabilities': _get_value(balance_sheet, 'liabilities_current'),
        'long_term_debt': _get_value(balance_sheet, 'long_term_debt'),
        'shareholders_equity': _get_value(balance_sheet, 'equity_attributable_to_parent'),
        'accounts_payable': _get_value(balance_sheet, 'accounts_payable'),
    }


def cashflow_metrics(result: dict) -> dict:
    """Extract key cash flow metrics from result. Returns None for missing metrics."""
    cash_flow = result['financials'].get('cash_flow_statement', {})

    operating_cf = _get_value(cash_flow, 'net_cash_flow_from_operating_activities')
    investing_cf = _get_value(cash_flow, 'net_cash_flow_from_investing_activities')

    return {
        'operating_cashflow': operating_cf,
        'investing_cashflow': investing_cf,
        'financing_cashflow': _get_value(cash_flow, 'net_cash_flow_from_financing_activities'),
        'free_cashflow': (operating_cf - investing_cf) if (operating_cf is not None and investing_cf is not None) else None,
    }


def json_to_df(json_file_path: str) -> pl.DataFrame:
    """
    Convert a single fundamental data JSON to a flat dataframe.
    Extracts PnL, balance sheet, and cashflow metrics.
    """
    with open(json_file_path) as f:
        data = json.load(f)

    result = data['results'][0]

    # Combine all metrics into one dict
    metrics = {
        'date': result['end_date'],
        'ticker': result['tickers'][0] if result.get('tickers') else None,
        'company_name': result.get('company_name'),
    }

    # Union all metric dicts
    metrics.update(pnl_metrics(result))
    metrics.update(balance_sheet_metrics(result))
    metrics.update(cashflow_metrics(result))

    return pl.DataFrame([metrics])


def load_all(json_to_df_func, data_dir: str | Path, df_cls, sort_cols: list):
    """Generic loader for all JSON files in a directory."""
    import warnings
    data_dir = Path(data_dir)
    dfs = [json_to_df_func(f) for f in data_dir.glob('*.json')]

    # Filter out empty dataframes
    dfs = [df for df in dfs if df.height > 0]

    if not dfs:
        warnings.warn(f"No data found in {data_dir}")
        return df_cls(pl.DataFrame())

    combined = pl.concat(dfs).sort(sort_cols)
    return df_cls(combined)


def load_all_fundamentals(data_dir: str | Path = None) -> FundamentalsDF:
    """Load and combine fundamental data from all JSON files in directory."""
    if data_dir is None:
        data_dir = Path('/home/karma/ats_python/notebooks/DATA/fundamental_data_largecap')

    return load_all(json_to_df, data_dir, FundamentalsDF, ['ticker', 'date'])
