from datetime import date
from decimal import Decimal

from ats.dataIO.statement_table import StatementTable
from ats.gmail_sync import build_fund_nav_rows, get_candidate_pdfs, get_latest_pdf


def test_get_candidate_pdfs_filters_out_existing_dates(tmp_path):
    older = tmp_path / "2026-03-15_old.pdf"
    newer = tmp_path / "2026-03-16_new.pdf"
    no_date = tmp_path / "statement.pdf"
    for path in (older, newer, no_date):
        path.write_bytes(b"")

    candidate_pdfs = get_candidate_pdfs(tmp_path, "2026-03-15")

    assert candidate_pdfs == [newer]


def test_get_latest_pdf_picks_most_recent_dated_statement(tmp_path):
    older = tmp_path / "2026-03-15_old.pdf"
    newer = tmp_path / "2026-03-16_new.pdf"
    no_date = tmp_path / "statement.pdf"
    for path in (older, newer, no_date):
        path.write_bytes(b"")

    latest_pdf = get_latest_pdf(tmp_path)

    assert latest_pdf == newer


def test_build_fund_nav_rows_keeps_latest_value_per_date():
    statements = [
        StatementTable(
            date=date(2026, 3, 15),
            account_type="Trading 212 Invest",
            account_id="1",
            deposits=None,
            withdrawals=None,
            realised_return=None,
            open_return=None,
            open_return_change=None,
            dividends=None,
            interest_on_cash=None,
            cashback=None,
            fx_fee=None,
            third_party_fees=None,
            account_value=Decimal("100.00"),
        ),
        StatementTable(
            date=date(2026, 3, 15),
            account_type="Trading 212 Invest",
            account_id="1",
            deposits=None,
            withdrawals=None,
            realised_return=None,
            open_return=None,
            open_return_change=None,
            dividends=None,
            interest_on_cash=None,
            cashback=None,
            fx_fee=None,
            third_party_fees=None,
            account_value=Decimal("101.50"),
        ),
        StatementTable(
            date=date(2026, 3, 16),
            account_type="Trading 212 Invest",
            account_id="1",
            deposits=None,
            withdrawals=None,
            realised_return=None,
            open_return=None,
            open_return_change=None,
            dividends=None,
            interest_on_cash=None,
            cashback=None,
            fx_fee=None,
            third_party_fees=None,
            account_value=Decimal("102.00"),
        ),
    ]

    rows = build_fund_nav_rows(statements)

    assert rows == [
        (date(2026, 3, 15), 101.5),
        (date(2026, 3, 16), 102.0),
    ]
