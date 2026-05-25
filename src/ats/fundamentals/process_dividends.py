import polars as pl
import json
from pathlib import Path
from datetime import datetime
from .process_fundamentals import FilterableDF, load_all


class DividendsDF(FilterableDF):
    """Polars DataFrame subclass for dividends data."""

    _cls = None

    def __init__(self, data):
        super().__init__(data)
        DividendsDF._cls = DividendsDF


def json_to_dividend_metrics(json_file_path: str) -> dict:
    """
    Convert dividend JSON to a dict with aggregated metrics.
    Returns latest and aggregated dividend data.
    """
    with open(json_file_path) as f:
        data = json.load(f)

    if not data.get('results'):
        return None

    results = data['results']
    ticker = results[0]['ticker']

    # Sort by pay_date to get most recent
    sorted_results = sorted(results, key=lambda x: x['pay_date'], reverse=True)
    latest = sorted_results[0]

    # Calculate annual dividend (sum of last 4 quarterly payments or last year)
    today = datetime.now().date()
    one_year_ago = today.replace(year=today.year - 1)

    last_year_dividends = [
        r for r in results
        if one_year_ago.isoformat() <= r['pay_date'] <= today.isoformat()
    ]
    annual_dividend = sum(r['cash_amount'] for r in last_year_dividends)

    return {
        'ticker': ticker,
        'latest_pay_date': latest['pay_date'],
        'latest_cash_amount': latest['cash_amount'],
        'latest_record_date': latest.get('record_date'),
        'latest_ex_dividend_date': latest.get('ex_dividend_date'),
        'frequency': latest.get('frequency'),
        'annual_dividend': annual_dividend,
        'total_dividend_records': len(results),
        'currency': latest.get('currency', 'USD'),
        'distribution_type': latest.get('distribution_type'),
    }


def json_to_df(json_file_path: str) -> pl.DataFrame:
    """
    Convert dividend JSON to flat dataframe with all dividend records.
    """
    with open(json_file_path) as f:
        data = json.load(f)

    if not data.get('results'):
        return pl.DataFrame()

    results = data['results']
    records = []

    for r in results:
        records.append({
            'ticker': r.get('ticker'),
            'record_date': r.get('record_date'),
            'pay_date': r.get('pay_date'),
            'declaration_date': r.get('declaration_date'),
            'ex_dividend_date': r.get('ex_dividend_date'),
            'frequency': r.get('frequency'),
            'cash_amount': r.get('cash_amount'),
            'split_adjusted_cash_amount': r.get('split_adjusted_cash_amount') or r.get('cash_amount'),
            'currency': r.get('currency', 'USD'),
            'distribution_type': r.get('distribution_type'),
        })

    return pl.DataFrame(records)


def load_all_dividends(data_dir: str | Path = None) -> DividendsDF:
    """Load and combine dividend data from all JSON files in directory."""
    if data_dir is None:
        data_dir = Path('/home/karma/ats_python/notebooks/DATA/dividends_data_midcap')

    return load_all(json_to_df, data_dir, DividendsDF, ['ticker', 'pay_date'])


def load_dividend_metrics(data_dir: str | Path = None) -> pl.DataFrame:
    """Load aggregated dividend metrics from all JSON files in directory."""
    if data_dir is None:
        data_dir = Path('/home/karma/ats_python/notebooks/DATA/dividends_data_midcap')

    data_dir = Path(data_dir)
    metrics = [
        m for f in data_dir.glob('*.json')
        if (m := json_to_dividend_metrics(f))
    ]

    return pl.DataFrame(metrics).sort('ticker')
