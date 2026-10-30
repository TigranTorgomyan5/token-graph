"""Which graph nodes are tests or generated code, and a graph copy without them.

The same path rules as TEST_PATH / CSHARP_TEST_FILE / GENERATED_PATH in
assets/project-explorer.html; change both together, or the page and the
answers disagree about what "Hide tests" means.
"""
from __future__ import annotations
import json
import os
import tempfile
from pathlib import Path
import re

TEST_PATH = re.compile(
    r"(^|/)(tests?|__tests__|specs?|testdata|test-data|fixtures)(/|$)"
    r"|(^|/)[^/]+\.(unit|integration|functional|e2e|acceptance)?tests?(/|$)"
    r"|_test\.go$|(^|/)test_[^/]*\.py$|_test\.py$|\.(test|spec)\.[cm]?[jt]sx?$",
    re.I,
)
CSHARP_TEST_FILE = re.compile(r"[a-z0-9]Tests?\.cs$")  # case-sensitive: FooTests.cs, not Latest.cs
GENERATED_PATH = re.compile(
    r"(^|/)(gen|generated|__generated__)(/|$)|\.pb(\.gw)?\.go$|_gen\.go$|_pb2(_grpc)?\.pyi?$"
    r"|\.g(\.i)?\.cs$|\.designer\.cs$|\.generated\.\w+$|(^|/)migrations/[^/]+\.cs$|(^|/)obj/",
    re.I,
)


def _file(node: dict) -> str:
    return str(node.get("source_file") or "").replace("\\", "/")


def is_test(node: dict) -> bool:
    file = _file(node)
    return bool(file) and bool(TEST_PATH.search(file) or CSHARP_TEST_FILE.search(file))


def is_generated(node: dict) -> bool:
    file = _file(node)
    return bool(file) and bool(GENERATED_PATH.search(file))


def scoped_graph(graph_file: Path, *, tests: bool, generated: bool) -> Path:
    """graph.json itself when nothing is excluded; else a cached copy without test and/or generated nodes.

    The copy sits next to graph.json (graph.scope-<variant>.json) and is rewritten
    whenever graph.json is newer, so answers never come from a stale graph.
    """
    if tests and generated:
        return graph_file
    variant = "-".join(name for name, keep in (("notests", tests), ("nogen", generated)) if not keep)
    scoped = graph_file.with_name(f"graph.scope-{variant}.json")
    if scoped.is_file() and scoped.stat().st_mtime >= graph_file.stat().st_mtime:
        return scoped
    data = json.loads(graph_file.read_text(encoding="utf-8"))
    drop = {n["id"] for n in data.get("nodes", []) if "id" in n and ((not tests and is_test(n)) or (not generated and is_generated(n)))}
    if not drop:
        return graph_file
    key = "links" if "links" in data else "edges"
    data["nodes"] = [n for n in data.get("nodes", []) if n.get("id") not in drop]
    data[key] = [l for l in data.get(key, []) if l.get("source") not in drop and l.get("target") not in drop]
    if isinstance(data.get("hyperedges"), list):
        data["hyperedges"] = [h for h in data["hyperedges"] if not drop.intersection(map(str, h.get("nodes", h.get("members", [])) if isinstance(h, dict) else []))]
    fd, name = tempfile.mkstemp(prefix=scoped.stem + "-", suffix=".tmp", dir=scoped.parent)
    temp = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(data, stream, ensure_ascii=False)
        temp.replace(scoped)
    finally:
        temp.unlink(missing_ok=True)
    return scoped
