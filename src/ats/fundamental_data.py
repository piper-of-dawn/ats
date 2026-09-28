import json
from functools import lru_cache
from pathlib import Path
from typing import Any

import os

from ats.dataIO import massive
from tqdm import tqdm

from ats.secrets_parser import parse_flat_toml

ENV_TOML_PATH = Path(__file__).resolve().parents[2] / "env.toml"
API_ENDPOINTS = {
    "fundamentals": "/vX/reference/financials",
    "dividends": "/stocks/v1/dividends",
}


@lru_cache(maxsize=None)
def load_secrets(toml_path: str | Path = ENV_TOML_PATH) -> dict[str, Any]:
    return parse_flat_toml(toml_path)


def get_api_key(secret_name: str, toml_path: str | Path = ENV_TOML_PATH) -> str:
    return load_secrets(toml_path)[secret_name]


def get_data(
    ticker: str,
    data_type: str = "fundamentals",
    api_key: str | None = None,
    secrets_toml_path: str | Path = ENV_TOML_PATH,
):
    if data_type not in API_ENDPOINTS:
        raise ValueError(f"Unknown data type: {data_type}")

    resolved_api_key = api_key or os.environ.get("POLYGON_IO") or get_api_key(
        "polygon_io_api_key_1" if data_type == "fundamentals" else "polygon_io_api_key_2",
        secrets_toml_path,
    )
    params = {"ticker": ticker}
    if data_type == "fundamentals":
        params.update(order="desc", limit=10, sort="filing_date")
    else:
        params.update(limit=100, sort="ticker.asc")
    return massive.request_json(API_ENDPOINTS[data_type], params, api_key=resolved_api_key)



def get_fundamentals(ticker: str, secrets_toml_path: str | Path = ENV_TOML_PATH):
    return get_data(ticker, "fundamentals", secrets_toml_path=secrets_toml_path)


def get_dividends(ticker: str, secrets_toml_path: str | Path = ENV_TOML_PATH):
    return get_data(ticker, "dividends", secrets_toml_path=secrets_toml_path)


def save_fundamentals(
    ticker: str, path: str, secrets_toml_path: str | Path = ENV_TOML_PATH
):
    with open(path, "w") as f:
        json.dump(get_fundamentals(ticker, secrets_toml_path=secrets_toml_path), f)


def save_dividends(
    ticker: str, path: str, secrets_toml_path: str | Path = ENV_TOML_PATH
):
    with open(path, "w") as f:
        json.dump(get_dividends(ticker, secrets_toml_path=secrets_toml_path), f)


def get_data_parallel(
    tickers,
    data_type: str = "fundamentals",
    max_workers=5,
    batch_size=5,
    batch_sleep=61,
    output_path=None,
    api_key: str | None = None,
    secrets_toml_path: str | Path = ENV_TOML_PATH,
):
    """Compatibility entrypoint: sequential; pacing is owned by Massive's helper.

    max_workers, batch_size and batch_sleep are retained but ignored.
    Failed items retain the legacy ticker-string sentinel.
    """
    results = []
    if output_path:
        Path(output_path).mkdir(parents=True, exist_ok=True)
    for ticker in tqdm(tickers, desc=f"Fetching {data_type}", unit="ticker"):
        try:
            result = get_data(ticker, data_type, api_key, secrets_toml_path)
            if output_path:
                with open(Path(output_path) / f"{ticker}.json", "w") as f:
                    json.dump(result, f)
            results.append(result)
        except Exception:
            results.append(ticker)
    return results



def get_fundamentals_parallel(
    tickers,
    max_workers=5,
    batch_size=5,
    batch_sleep=61,
    output_path=None,
    secrets_toml_path: str | Path = ENV_TOML_PATH,
):
    return get_data_parallel(
        tickers,
        "fundamentals",
        max_workers,
        batch_size,
        batch_sleep,
        output_path,
        secrets_toml_path=secrets_toml_path,
    )


def get_dividends_parallel(
    tickers,
    max_workers=5,
    batch_size=5,
    batch_sleep=61,
    output_path=None,
    secrets_toml_path: str | Path = ENV_TOML_PATH,
):
    return get_data_parallel(
        tickers,
        "dividends",
        max_workers,
        batch_size,
        batch_sleep,
        output_path,
        secrets_toml_path=secrets_toml_path,
    )
