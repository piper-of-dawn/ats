from __future__ import annotations

import argparse
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


@dataclass(frozen=True)
class ChangedPath:
    status: str
    old_path: str | None
    new_path: str | None

    @property
    def status_code(self) -> str:
        return self.status[0]

    @property
    def label(self) -> str:
        if self.status_code == "R":
            return f"{self.old_path} -> {self.new_path}"
        return self.new_path or self.old_path or "<unknown>"


def parse_name_status(output: str) -> list[ChangedPath]:
    changes: list[ChangedPath] = []
    for raw_line in output.splitlines():
        if not raw_line.strip():
            continue

        parts = raw_line.split("\t")
        status = parts[0]
        code = status[0]

        if code == "R":
            if len(parts) != 3:
                raise ValueError(f"Unexpected rename status line: {raw_line!r}")
            changes.append(ChangedPath(status=status, old_path=parts[1], new_path=parts[2]))
            continue

        if len(parts) != 2:
            raise ValueError(f"Unexpected diff status line: {raw_line!r}")

        path = parts[1]
        if code == "A":
            changes.append(ChangedPath(status=status, old_path=None, new_path=path))
        elif code == "D":
            changes.append(ChangedPath(status=status, old_path=path, new_path=None))
        else:
            changes.append(ChangedPath(status=status, old_path=path, new_path=path))

    return changes


def run_git_text(repo_root: Path, args: list[str]) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout


def run_git_bytes(repo_root: Path, args: list[str]) -> bytes:
    result = subprocess.run(
        ["git", *args],
        cwd=repo_root,
        check=True,
        capture_output=True,
    )
    return result.stdout


def changed_paths_between(repo_root: Path, base_commit: str, target_commit: str) -> list[ChangedPath]:
    output = run_git_text(
        repo_root,
        ["diff", "--name-status", "--find-renames", base_commit, target_commit],
    )
    return parse_name_status(output)


def git_diff_stat(repo_root: Path, base_commit: str, target_commit: str) -> str:
    return run_git_text(repo_root, ["diff", "--stat", "--find-renames", base_commit, target_commit]).strip()


def write_commit_file(repo_root: Path, commit: str, relative_path: str, destination_root: Path) -> None:
    file_bytes = run_git_bytes(repo_root, ["show", f"{commit}:{relative_path}"])
    destination = destination_root / relative_path
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(file_bytes)


def build_changeset_report(
    base_commit: str,
    target_commit: str,
    changes: Iterable[ChangedPath],
    diff_stat: str,
) -> str:
    change_list = list(changes)
    lines = [
        "# Commit Change Set",
        "",
        f"- Base commit: `{base_commit}`",
        f"- Target commit: `{target_commit}`",
        f"- Changed paths: `{len(change_list)}`",
        "",
        "Graphify snapshot layout:",
        "- `before/` contains changed files as they existed at the base commit.",
        "- `after/` contains changed files as they existed at the target commit.",
        "- Deleted files only appear in `before/`.",
        "- Added files only appear in `after/`.",
        "",
        "## File status summary",
        "",
        "| Status | Path |",
        "| --- | --- |",
    ]

    for change in change_list:
        lines.append(f"| `{change.status}` | `{change.label}` |")

    lines.extend(
        [
            "",
            "## Diff stat",
            "",
            "```text",
            diff_stat or "(no diff stat output)",
            "```",
            "",
        ]
    )
    return "\n".join(lines)


def materialize_snapshot(
    repo_root: Path,
    base_commit: str,
    target_commit: str,
    output_dir: Path,
) -> tuple[Path, list[ChangedPath]]:
    changes = changed_paths_between(repo_root, base_commit, target_commit)
    if not changes:
        raise ValueError(f"No changed files found between {base_commit} and {target_commit}.")

    snapshot_dir = output_dir / "snapshot"
    before_dir = snapshot_dir / "before"
    after_dir = snapshot_dir / "after"
    before_dir.mkdir(parents=True, exist_ok=True)
    after_dir.mkdir(parents=True, exist_ok=True)

    for change in changes:
        if change.old_path is not None:
            write_commit_file(repo_root, base_commit, change.old_path, before_dir)
        if change.new_path is not None:
            write_commit_file(repo_root, target_commit, change.new_path, after_dir)

    report = build_changeset_report(
        base_commit=base_commit,
        target_commit=target_commit,
        changes=changes,
        diff_stat=git_diff_stat(repo_root, base_commit, target_commit),
    )
    (snapshot_dir / "CHANGESET.md").write_text(report, encoding="utf-8")
    return snapshot_dir, changes


def run_graphify(snapshot_dir: Path, output_dir: Path, graphify_bin: str, with_viz: bool, mode: str | None) -> None:
    command = [graphify_bin, "extract", str(snapshot_dir), "--out", str(output_dir)]
    if mode:
        command.extend(["--mode", mode])

    try:
        subprocess.run(command, cwd=output_dir, check=True, capture_output=True, text=True)
    except FileNotFoundError as exc:
        raise RuntimeError(
            f"Graphify executable '{graphify_bin}' was not found. Install it or pass --graphify-bin."
        ) from exc
    except subprocess.CalledProcessError as exc:
        stderr = (exc.stderr or "").strip()
        stdout = (exc.stdout or "").strip()
        detail = stderr or stdout or f"exit code {exc.returncode}"
        raise RuntimeError(f"Graphify failed: {detail}") from exc

    if not with_viz:
        graph_html = output_dir / "graphify-out" / "graph.html"
        if graph_html.exists():
            graph_html.unlink()


def _format_community_members(members: list[str], limit: int = 12) -> str:
    if not members:
        return "_No nodes listed._"

    shown = ", ".join(f"`{member}`" for member in members[:limit])
    remaining = len(members) - limit
    if remaining > 0:
        shown = f"{shown}, ... (+{remaining} more)"
    return shown


def build_graph_report(analysis: dict, base_commit: str, target_commit: str) -> str:
    communities = analysis.get("communities", {})
    cohesion = analysis.get("cohesion", {})
    gods = analysis.get("gods", [])
    surprises = analysis.get("surprises", [])

    lines = [
        "# GRAPH_REPORT",
        "",
        f"- Base commit: `{base_commit}`",
        f"- Target commit: `{target_commit}`",
        f"- Community count: `{len(communities)}`",
        f"- God nodes: `{len(gods)}`",
        f"- Surprising edges: `{len(surprises)}`",
        "",
        "## God Nodes",
        "",
    ]

    if gods:
        for node in gods:
            label = node.get("label", node.get("id", "<unknown>"))
            node_id = node.get("id", "<unknown>")
            degree = node.get("degree", "?")
            lines.append(f"- `{label}` (`{node_id}`), degree `{degree}`")
    else:
        lines.append("_No god nodes found._")

    lines.extend(["", "## Surprising Connections", ""])
    if surprises:
        for item in surprises:
            source = item.get("source", "<unknown>")
            target = item.get("target", "<unknown>")
            relation = item.get("relation", "<unknown>")
            confidence = item.get("confidence", "<unknown>")
            why = item.get("why", "").strip() or "No explanation provided."
            files = item.get("source_files", [])
            file_summary = ", ".join(f"`{path}`" for path in files) if files else "_No file list_"
            lines.append(f"- `{source}` -> `{target}` via `{relation}` (`{confidence}`)")
            lines.append(f"  Why: {why}")
            lines.append(f"  Files: {file_summary}")
    else:
        lines.append("_No surprising connections found._")

    lines.extend(["", "## Communities", ""])
    if communities:
        for community_id in sorted(communities, key=lambda value: int(value) if str(value).isdigit() else str(value)):
            members = communities[community_id]
            score = cohesion.get(community_id, "?")
            lines.append(f"### Community {community_id}")
            lines.append(f"- Cohesion: `{score}`")
            lines.append(f"- Members: {_format_community_members(members)}")
            lines.append("")
    else:
        lines.append("_No communities found._")

    return "\n".join(lines).rstrip() + "\n"


def write_graph_report(output_dir: Path, base_commit: str, target_commit: str) -> Path:
    graphify_out = output_dir / "graphify-out"
    analysis_path = graphify_out / ".graphify_analysis.json"
    report_path = graphify_out / "GRAPH_REPORT.md"

    if not analysis_path.exists():
        raise RuntimeError(f"Graphify analysis file was not created at {analysis_path}.")

    analysis = json.loads(analysis_path.read_text(encoding="utf-8"))
    report_text = build_graph_report(analysis, base_commit=base_commit, target_commit=target_commit)
    report_path.write_text(report_text, encoding="utf-8")
    return report_path


def default_output_dir(repo_root: Path, base_commit: str, target_commit: str) -> Path:
    base_short = base_commit[:12]
    target_short = target_commit[:12]
    return repo_root / "graphify-diff-out" / f"{base_short}__{target_short}"


def parse_args() -> argparse.Namespace:
    repo_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(
        description="Build a Graphify snapshot for the files changed between two commits."
    )
    parser.add_argument("base_commit", help="Older/base commit.")
    parser.add_argument("target_commit", help="Newer/target commit.")
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=repo_root,
        help=f"Git repository root. Defaults to {repo_root}.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        help="Directory where the before/after snapshot and graphify-out artifacts will be written.",
    )
    parser.add_argument(
        "--graphify-bin",
        default="graphify",
        help="Graphify executable name or absolute path.",
    )
    parser.add_argument(
        "--mode",
        default=None,
        help="Optional Graphify extraction mode, for example 'deep'.",
    )
    parser.add_argument(
        "--with-viz",
        action="store_true",
        help="Generate graph.html in addition to GRAPH_REPORT.md and graph.json.",
    )
    parser.add_argument(
        "--skip-graphify",
        action="store_true",
        help="Only materialize the before/after snapshot without invoking Graphify.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    repo_root = args.repo_root.resolve()
    output_dir = (args.output_dir or default_output_dir(repo_root, args.base_commit, args.target_commit)).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    snapshot_dir, changes = materialize_snapshot(
        repo_root=repo_root,
        base_commit=args.base_commit,
        target_commit=args.target_commit,
        output_dir=output_dir,
    )

    if args.skip_graphify:
        print(f"Snapshot created at: {snapshot_dir}")
        print(f"Changed files captured: {len(changes)}")
        return

    run_graphify(
        snapshot_dir=snapshot_dir,
        output_dir=output_dir,
        graphify_bin=args.graphify_bin,
        with_viz=args.with_viz,
        mode=args.mode,
    )
    report_path = write_graph_report(output_dir, base_commit=args.base_commit, target_commit=args.target_commit)
    print(f"Snapshot created at: {snapshot_dir}")
    print(f"Graphify artifacts created at: {output_dir / 'graphify-out'}")
    print(f"Primary report created at: {report_path}")
    print(f"Changed files captured: {len(changes)}")


if __name__ == "__main__":
    main()
