import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from functools import lru_cache
from pathlib import Path
from time import sleep
from typing import Any

import requests
from tqdm import tqdm

from ats.secrets_parser import parse_flat_toml

ENV_TOML_PATH = Path(__file__).resolve().parents[2] / "env.toml"
API_ENDPOINTS = {
    "fundamentals": "https://api.polygon.io/vX/reference/financials?ticker={ticker}&order=desc&limit=10&sort=filing_date&apiKey={api_key}",
    "dividends": "https://api.massive.com/stocks/v1/dividends?ticker={ticker}&limit=100&sort=ticker.asc&apiKey={api_key}",
}


@lru_cache(maxsize=None)
def load_secrets(toml_path: str | Path = ENV_TOML_PATH) -> dict[str, Any]:
    return parse_flat_toml(toml_path)


def get_api_key(secret_name: str, toml_path: str | Path = ENV_TOML_PATH) -> str:
    return load_secrets(toml_path)[secret_name]


@lru_cache(maxsize=None)
def get_data(
    ticker: str,
    data_type: str = "fundamentals",
    api_key: str | None = None,
    secrets_toml_path: str | Path = ENV_TOML_PATH,
):
    if data_type not in API_ENDPOINTS:
        raise ValueError(f"Unknown data type: {data_type}")

    resolved_api_key = api_key or get_api_key(
        "polygon_io_api_key_1" if data_type == "fundamentals" else "polygon_io_api_key_2",
        secrets_toml_path,
    )
    url = API_ENDPOINTS[data_type].format(ticker=ticker, api_key=resolved_api_key)
    resp = requests.get(url)
    if resp.status_code != 200:
        print(resp)
        raise ValueError(resp)
    return resp.json()


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
    results = [None] * len(tickers)
    if output_path:
        Path(output_path).mkdir(parents=True, exist_ok=True)

    with tqdm(total=len(tickers), desc=f"Fetching {data_type}", unit="ticker") as pbar:
        for start in range(0, len(tickers), batch_size):
            batch = tickers[start : start + batch_size]
            with ThreadPoolExecutor(
                max_workers=min(max_workers, len(batch))
            ) as executor:
                futures = {
                    executor.submit(
                        get_data, ticker, data_type, api_key, secrets_toml_path
                    ): (
                        start + i,
                        ticker,
                    )
                    for i, ticker in enumerate(batch)
                }
                for future in as_completed(futures):
                    idx, ticker = futures[future]
                    try:
                        result = future.result()
                        results[idx] = result
                        if output_path:
                            with open(Path(output_path) / f"{ticker}.json", "w") as f:
                                json.dump(result, f)
                    except Exception:
                        results[idx] = ticker
                    pbar.update(1)
            if start + batch_size < len(tickers):
                sleep(batch_sleep)
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
