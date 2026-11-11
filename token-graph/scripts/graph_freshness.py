"""Is a project's graph.json older than its code?

A stale graph answers with old line numbers: the assistant then opens the wrong place
and reads the file again. The launcher asks `stale_reason()` before every answer and
refreshes the graph (or says it is stale) when this returns a reason.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

GIT_TIMEOUT = 10


class FreshnessUnknown(str):
    """A warning that must not be treated as evidence that a graph is stale."""


def _new_code_files(root: Path, indexed: set[str], since: float) -> list[str]:
    """Find code added after the graph was built, respecting the engine's ignore rules.

    Only files newer than the graph are considered. A file the engine could not index would otherwise
    be reported as new after every refresh, causing an update before every answer."""
    root = root.resolve()
    vendor = str(Path(__file__).resolve().parents[1] / 'vendor')
    if vendor not in sys.path:
        sys.path.insert(0, vendor)
    from graphify.detect import CODE_EXTENSIONS, ignored_predicate, _is_sensitive
    ignored = ignored_predicate(root)
    found = []
    for directory, dirs, files in os.walk(root, followlinks=False):
        parent = Path(directory)
        dirs[:] = [name for name in dirs if name != 'graphify-out' and not (parent / name).is_symlink() and not ignored(parent / name)]
        for name in files:
            path = parent / name
            if path.is_symlink() or path.suffix.lower() not in CODE_EXTENSIONS or ignored(path) or _is_sensitive(path):
                continue
            try:
                if path.stat().st_mtime <= since:
                    continue
            except OSError:
                continue
            relative = path.relative_to(root).as_posix()
            if relative not in indexed:
                found.append(relative)
    return found


def _change_note(count: int) -> str:
    return f"{count} file{'s' if count != 1 else ''} changed after the graph was built"


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
    """Return a stale reason, FreshnessUnknown if unverifiable, or None if current."""
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
        return _change_note(len(edited)) if edited else None
    manifest = graph.with_name("manifest.json")
    try:
        contents = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return FreshnessUnknown("graph manifest is missing or unreadable; freshness cannot be verified")
    if isinstance(contents, dict):
        files = list(contents)
    elif isinstance(contents, list) and all(isinstance(name, str) for name in contents):
        files = contents  # older engine manifests stored a plain path list
    else:
        return FreshnessUnknown("graph manifest is invalid; freshness cannot be verified")
    edited = _changed_since(root, files, graph_time)
    edited += _new_code_files(root, set(files), graph_time)
    return _change_note(len(edited)) if edited else None
