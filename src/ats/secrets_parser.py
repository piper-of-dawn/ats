from __future__ import annotations

from pathlib import Path
from typing import Any

import tomllib


def parse_flat_toml(path: str | Path) -> dict[str, Any]:
    """Parse a flat TOML file into a flat dictionary."""
    with Path(path).open("rb") as handle:
        parsed = tomllib.load(handle)

    for key, value in parsed.items():
        if isinstance(value, dict):
            raise ValueError(f"Nested TOML table found for key {key!r}")

    return parsed
