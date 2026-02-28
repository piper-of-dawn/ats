from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

_DASHBOARD_PATH = _ROOT / "dashboard" / "index.py"
_SPEC = spec_from_file_location("dashboard.index", _DASHBOARD_PATH)
if _SPEC is None or _SPEC.loader is None:
    raise ImportError(f"Unable to load dashboard app from {_DASHBOARD_PATH}")

_MODULE = module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)

app = _MODULE.app
run = _MODULE.run
