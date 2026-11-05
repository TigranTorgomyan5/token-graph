"""Is a project's graph.json older than its code?

A stale graph answers with old line numbers: the assistant then opens the wrong place
and reads the file again. The launcher asks `stale_reason()` before every answer and
refreshes the graph (or says it is stale) when this returns a reason.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

GIT_TIMEOUT = 10


def _git(root: Path, *args: str) -> str | None:
    try:
        result = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True,
                                encoding="utf-8", errors="replace", timeout=GIT_TIMEOUT, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout if result.returncode == 0 else None


def built_commit(graph: Path) -> str:
    """Commit the graph was built from ('' when unknown). Reads only the file's tail."""
    try:
        with graph.open("rb") as stream:
            stream.seek(0, 2)
            stream.seek(max(0, stream.tell() - 65536))
            tail = stream.read().decode("utf-8", "replace")
    except OSError:
        return ""
    marker = '"built_at_commit": "'
    at = tail.rfind(marker)
    if at < 0:
        try:
            return str(json.loads(graph.read_text(encoding="utf-8")).get("built_at_commit") or "")
        except (OSError, ValueError):
            return ""
    start = at + len(marker)
    return tail[start:tail.find('"', start)]


def _changed_since(root: Path, files: list[str], since: float) -> list[str]:
    changed = []
    for name in files:
        if name.startswith("graphify-out/"):
            continue
        try:
            if (root / name).stat().st_mtime > since:
                changed.append(name)
        except OSError:
            changed.append(name)  # deleted
    return changed


def stale_reason(root: Path, graph: Path) -> str | None:
    """Why the graph is behind the code, or None when it is current."""
    if not graph.is_file():
        return "no graph yet"
    graph_time = graph.stat().st_mtime
    if _git(root, "rev-parse", "--is-inside-work-tree") is not None:
        head = (_git(root, "rev-parse", "HEAD") or "").strip()
        built = built_commit(graph)
        if head and built and head != built:
            changed = _git(root, "diff", "--name-only", built, head)
            count = len(changed.split()) if changed is not None else None
            files = f", {count} files changed" if count is not None else ""
            return f"graph built at {built[:7]}, code is at {head[:7]}{files}"
        status = _git(root, "status", "--porcelain", "--untracked-files=normal") or ""
        edited = [line[3:].strip().strip('"').split(" -> ")[-1] for line in status.splitlines() if len(line) > 3]
        edited = _changed_since(root, edited, graph_time)
        return f"{len(edited)} files edited after the graph was built" if edited else None
    manifest = graph.with_name("manifest.json")
    try:
        files = list(json.loads(manifest.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return None
    edited = _changed_since(root, files, graph_time)
    return f"{len(edited)} files edited after the graph was built" if edited else None
