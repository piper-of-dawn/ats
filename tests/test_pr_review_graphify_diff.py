import importlib.util
import sys
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "pr_review_utils" / "graphify_diff.py"
SPEC = importlib.util.spec_from_file_location("graphify_diff", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC is not None and SPEC.loader is not None
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)

ChangedPath = MODULE.ChangedPath
build_graph_report = MODULE.build_graph_report
build_changeset_report = MODULE.build_changeset_report
parse_name_status = MODULE.parse_name_status


def test_parse_name_status_handles_common_git_status_lines():
    output = "\n".join(
        [
            "M\tsrc/ats/jobs.py",
            "A\tdashboard/static/js/new-chart.js",
            "D\tsrc/ats/old_module.py",
            "R100\tsrc/ats/old.py\tsrc/ats/new.py",
        ]
    )

    changes = parse_name_status(output)

    assert changes == [
        ChangedPath(status="M", old_path="src/ats/jobs.py", new_path="src/ats/jobs.py"),
        ChangedPath(
            status="A",
            old_path=None,
            new_path="dashboard/static/js/new-chart.js",
        ),
        ChangedPath(status="D", old_path="src/ats/old_module.py", new_path=None),
        ChangedPath(
            status="R100",
            old_path="src/ats/old.py",
            new_path="src/ats/new.py",
        ),
    ]


def test_build_changeset_report_describes_snapshot_layout():
    report = build_changeset_report(
        base_commit="abc123",
        target_commit="def456",
        changes=[
            ChangedPath(status="M", old_path="src/ats/jobs.py", new_path="src/ats/jobs.py"),
            ChangedPath(status="R089", old_path="a.py", new_path="b.py"),
        ],
        diff_stat="2 files changed, 10 insertions(+), 3 deletions(-)",
    )

    assert "Base commit: `abc123`" in report
    assert "Target commit: `def456`" in report
    assert "`before/` contains changed files as they existed at the base commit." in report
    assert "| `R089` | `a.py -> b.py` |" in report
    assert "2 files changed, 10 insertions(+), 3 deletions(-)" in report


def test_build_graph_report_prioritizes_markdown_summary():
    report = build_graph_report(
        {
            "communities": {"0": ["node_a", "node_b"], "1": ["node_c"]},
            "cohesion": {"0": 0.42, "1": 1.0},
            "gods": [{"id": "node_a", "label": "NodeA", "degree": 7}],
            "surprises": [
                {
                    "source": "NodeA",
                    "target": "NodeC",
                    "relation": "calls",
                    "confidence": "EXTRACTED",
                    "why": "crosses module boundaries",
                    "source_files": ["after/foo.py", "before/bar.py"],
                }
            ],
        },
        base_commit="abc123",
        target_commit="def456",
    )

    assert report.startswith("# GRAPH_REPORT")
    assert "Base commit: `abc123`" in report
    assert "## God Nodes" in report
    assert "`NodeA` (`node_a`), degree `7`" in report
    assert "## Surprising Connections" in report
    assert "`NodeA` -> `NodeC` via `calls` (`EXTRACTED`)" in report
    assert "Files: `after/foo.py`, `before/bar.py`" in report
    assert "## Communities" in report
    assert "### Community 0" in report
