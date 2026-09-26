import json
from pathlib import Path
import sys
from types import ModuleType


NOTEBOOK_PATH = Path(__file__).parents[1] / "notebooks" / "gold.ipynb"


def load_notebook_namespace(*tags: str) -> dict[str, object]:
    """Execute only tagged, network-free notebook cells for focused tests."""
    notebook = json.loads(NOTEBOOK_PATH.read_text())
    requested = set(tags)
    sources = []
    for cell in notebook["cells"]:
        cell_tags = set(cell.get("metadata", {}).get("tags", []))
        if cell.get("cell_type") == "code" and requested.intersection(cell_tags):
            sources.append("".join(cell.get("source", [])))

    module = ModuleType("gold_notebook_test")
    module.__file__ = str(NOTEBOOK_PATH)
    sys.modules[module.__name__] = module
    exec(compile("\n\n".join(sources), str(NOTEBOOK_PATH), "exec"), module.__dict__)
    return module.__dict__


def test_notebook_exposes_research_configuration():
    namespace = load_notebook_namespace("research-core")

    config = namespace["ResearchConfig"]()
    assert config.entry_z == 2.0
    assert config.exit_z == 0.5
    assert config.max_holding_days == 20
    assert config.transaction_cost_bps == 5.0
    assert config.annual_short_borrow == 0.02
    assert config.bootstrap_repetitions >= 2_000

    assert {"GLD", "GDX"}.issubset(namespace["TRADABLE_TICKERS"])
    assert {"SPY", "UUP"}.issubset(namespace["FACTOR_TICKERS"])
