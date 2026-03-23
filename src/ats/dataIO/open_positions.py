import re
import subprocess
from dataclasses import asdict
from dataclasses import dataclass
from dataclasses import field
from decimal import Decimal
from pathlib import Path


@dataclass(slots=True)
class Position:
    Ticker: str
    ISIN: str
    Currency: str
    Value: Decimal
    Country: str = field(init=False)

    def __post_init__(self) -> None:
        self.Country = self.extract_country_from_isin()

    def extract_country_from_isin(self) -> str:
        isin = self.ISIN.strip().upper()
        if len(isin) < 2:
            return ""
        return isin[:2]

    def to_dict(self) -> dict[str, str | float]:
        data = asdict(self)
        return {
            key: float(value) if isinstance(value, Decimal) else value
            for key, value in data.items()
        }


_POSITION_ROW_RE = re.compile(
    r"^\s*(?P<ticker>\S+)\s+(?P<isin>[A-Z0-9]{12})\s+(?P<currency>[A-Z]{3})\s+.*?(?P<value>€?-?[\d,]+(?:\.\d{1,2})?)\s*$"
)


def _resolve_pdf_path(pdf_path: str | Path) -> Path:
    pdf_path = Path(pdf_path).expanduser()
    if not pdf_path.exists() and not pdf_path.is_absolute():
        fallback = Path("output") / pdf_path
        if fallback.exists():
            pdf_path = fallback
    if not pdf_path.exists():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")
    return pdf_path


def _run_pdftotext(pdf_path: Path, *args: str) -> str:
    try:
        return subprocess.run(
            ["pdftotext", *args, str(pdf_path), "-"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
    except subprocess.CalledProcessError as exc:
        stderr = (exc.stderr or "").strip()
        raise ValueError(f"pdftotext failed for {pdf_path}: {stderr or exc}") from exc


def parse_open_positions(pdf_path: str | Path) -> list[Position]:
    pdf_path = _resolve_pdf_path(pdf_path)
    if pdf_path.suffix.lower() != ".pdf":
        return []

    text = _run_pdftotext(pdf_path, "-layout", "-nopgbrk")

    positions: list[Position] = []
    in_invest_open_positions_summary = False
    in_open_positions = False

    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        stripped = line.strip()

        if "Invest account - open positions summary" in stripped:
            in_invest_open_positions_summary = True
            in_open_positions = False
            continue

        if not in_invest_open_positions_summary:
            continue

        if stripped == "Open positions":
            in_open_positions = True
            continue

        if in_open_positions and (
            "Invest account - share lending summary" in stripped
            or "Crypto account - open positions summary" in stripped
        ):
            break

        if not in_open_positions:
            continue

        if not stripped or stripped in {"CUSTOMER ID", "CUSTOMER NAME", "No data available"}:
            continue
        if stripped.startswith("INSTRUMENT ") or stripped.startswith("INSTRUMENT\t"):
            continue
        if stripped.startswith("CUSTOMER ID") or stripped.startswith("ACCOUNT "):
            continue
        if re.fullmatch(r"\d+/\d+", stripped):
            continue

        match = _POSITION_ROW_RE.match(line)
        if not match:
            continue

        positions.append(
            Position(
                Ticker=match.group("ticker"),
                ISIN=match.group("isin"),
                Currency=match.group("currency"),
                Value=Decimal(match.group("value").replace("€", "").replace(",", "")),
            )
        )

    return positions
