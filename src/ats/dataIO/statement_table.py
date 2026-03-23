import re
import subprocess
from dataclasses import asdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path


@dataclass(slots=True)
class StatementTable:
    date: date
    account_type: str
    account_id: str | None
    deposits: Decimal | None
    withdrawals: Decimal | None
    realised_return: Decimal | None
    open_return: Decimal | None
    open_return_change: Decimal | None
    dividends: Decimal | None
    interest_on_cash: Decimal | None
    cashback: Decimal | None
    fx_fee: Decimal | None
    third_party_fees: Decimal | None
    account_value: Decimal | None

    def to_dict(self) -> dict[str, date | str | float | None]:
        data = asdict(self)
        return {
            key: float(value) if isinstance(value, Decimal) else value
            for key, value in data.items()
        }

    @classmethod
    def from_rows(
        cls, table_date: date, account_type: str, account_id: str | None, rows: dict[str, str]
    ) -> "StatementTable":
        amount = lambda key: (
            Decimal(rows[key].replace("€", "").replace(",", "").strip()) if key in rows else None
        )
        return cls(
            date=table_date,
            account_type=account_type,
            account_id=account_id,
            deposits=amount("Deposits"),
            withdrawals=amount("Withdrawals"),
            realised_return=amount("Realised return"),
            open_return=amount("Open return"),
            open_return_change=amount("Open return change"),
            dividends=amount("Dividends"),
            interest_on_cash=amount("Interest on cash"),
            cashback=amount("Cashback"),
            fx_fee=amount("FX Fee"),
            third_party_fees=amount("Third-party fees"),
            account_value=amount("Account value"),
        )


def parse_statement_table(pdf_path: str | Path) -> StatementTable | None:
    pdf_path = Path(pdf_path).expanduser()
    if not pdf_path.exists() and not pdf_path.is_absolute():
        fallback = Path("output") / pdf_path
        if fallback.exists():
            pdf_path = fallback
    if not pdf_path.exists():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")
    if pdf_path.suffix.lower() != ".pdf":
        return None
    date_match = re.search(r"(\d{4}-\d{2}-\d{2})", pdf_path.name)
    if not date_match:
        return None
    try:
        text = subprocess.run(
            ["pdftotext", "-f", "1", "-l", "1", "-layout", "-nopgbrk", str(pdf_path), "-"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
    except subprocess.CalledProcessError as exc:
        stderr = (exc.stderr or "").strip()
        raise ValueError(f"pdftotext failed for {pdf_path}: {stderr or exc}") from exc
    lines = [line.rstrip() for line in text.splitlines()]
    split_left = lambda line: next(part for part in re.split(r"\s{2,}", line.strip()) if part)
    account_type = split_left(next(line for line in lines if line.strip().startswith("Trading 212 ")))
    account_id = next(
        (split_left(line).split(":", 1)[1].strip() for line in lines if line.strip().startswith("Account ID:")),
        None,
    )
    aliases = {
        "Deposits": ("Deposits",),
        "Withdrawals": ("Withdrawals",),
        "Realised return": ("Realised return", "Realised P/L", "Closed result"),
        "Open return": ("Open return", "Unrealised P/L", "Open result"),
        "Open return change": ("Open return change",),
        "Dividends": ("Dividends",),
        "Interest on cash": ("Interest on cash",),
        "Cashback": ("Cashback",),
        "FX Fee": ("FX Fee",),
        "Third-party fees": ("Third-party fees", "Government taxes and levies"),
        "Account value": ("Account value",),
    }
    rows = {}
    for target, labels in aliases.items():
        line = next(
            (line for line in lines for label in labels if re.match(rf"^\s*{re.escape(label)}\s+€", line)),
            None,
        )
        if not line:
            continue
        value_match = re.search(r"(€-?[\d,]+\.\d{2})", line)
        if not value_match:
            raise ValueError(f"Missing value for row: {target}")
        rows[target] = value_match.group(1)
    return StatementTable.from_rows(date.fromisoformat(date_match.group(1)), account_type, account_id, rows)


def parse_statement_tables(
    directory: str | Path = "output",
    *,
    debug: bool = True,
    continue_on_error: bool = True,
) -> list[StatementTable]:
    directory = Path(directory).expanduser()
    if not directory.exists():
        raise FileNotFoundError(f"Directory not found: {directory}")
    if not directory.is_dir():
        raise NotADirectoryError(f"Not a directory: {directory}")

    pdf_paths = sorted(path for path in directory.iterdir() if path.is_file() and path.suffix.lower() == ".pdf")
    if debug:
        print(f"Scanning {directory} ({len(pdf_paths)} PDF files)")

    tables: list[StatementTable] = []
    for index, pdf_path in enumerate(pdf_paths, start=1):
        if debug:
            print(f"[{index}/{len(pdf_paths)}] Parsing {pdf_path.name}")
        try:
            table = parse_statement_table(pdf_path)
        except Exception as exc:
            if debug:
                print(f"[{index}/{len(pdf_paths)}] Failed {pdf_path.name}: {exc}")
            if not continue_on_error:
                raise
            continue

        tables.append(table)
        if debug:
            print(
                f"[{index}/{len(pdf_paths)}] OK {table.date} {table.account_type} "
                f"value={table.account_value}"
            )

    if debug:
        print(f"Parsed {len(tables)} of {len(pdf_paths)} PDF files")
    return tables
