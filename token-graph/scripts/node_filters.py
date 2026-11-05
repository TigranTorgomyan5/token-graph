"""Which graph nodes are tests or generated code, and a graph copy without them.

The same path rules as TEST_PATH / CSHARP_TEST_FILE / GENERATED_PATH in
assets/project-explorer.html; change both together, or the page and the
answers disagree about what "Hide tests" means.
"""
from __future__ import annotations
import json
import os
import tempfile
import time
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


def is_external(node: dict) -> bool:
    """A name the code imports or references but the project does not define (json, os, Path,
    ILogger ...): the parser saw it, but it has no source file, so it only crowds an answer."""
    if _file(node):
        return False
    # AST references to unknown names, and the modules the engine itself marks external.
    # Concept nodes from document mapping have no file either and are kept.
    return node.get("_origin") == "ast" or node.get("external") is True or node.get("type") == "external"


def with_range(node: dict) -> dict:
    """`L1200-1236` instead of `L1200` when line_ranges.py recorded the symbol's last line."""
    end = node.get("end_line")
    match = re.fullmatch(r"L(\d+)", str(node.get("source_location") or ""))
    if isinstance(end, int) and match and end > int(match.group(1)):
        return dict(node, source_location=f"L{match.group(1)}-{end}")
    return node


def scoped_graph(graph_file: Path, *, tests: bool, generated: bool,
                 external: bool = True, line_ranges: bool = False) -> Path:
    """graph.json itself when nothing changes; else a cached answer copy of it.

    The copy drops test, generated and/or external nodes and, with `line_ranges`, shows
    each symbol's location as a line range. It sits next to graph.json
    (graph.scope-<variant>.json) and is rewritten whenever graph.json is newer, so
    answers never come from a stale graph.
    """
    if tests and generated and external and not line_ranges:
        return graph_file
    variant = "-".join(name for name, on in (("notests", not tests), ("nogen", not generated),
                                               ("noext", not external), ("ranges", line_ranges)) if on)
    scoped = graph_file.with_name(f"graph.scope-{variant}.json")
    if scoped.is_file() and scoped.stat().st_mtime >= graph_file.stat().st_mtime:
        return scoped
    data = json.loads(graph_file.read_text(encoding="utf-8"))
    drop = {n["id"] for n in data.get("nodes", []) if "id" in n and ((not tests and is_test(n)) or (not generated and is_generated(n))
                                                                     or (not external and is_external(n)))}
    ranged = line_ranges and any("end_line" in n for n in data.get("nodes", []))
    if not drop and not ranged:
        return graph_file
    key = "links" if "links" in data else "edges"
    data["nodes"] = [with_range(n) if ranged else n for n in data.get("nodes", []) if n.get("id") not in drop]
    data[key] = [l for l in data.get(key, []) if l.get("source") not in drop and l.get("target") not in drop]
    if isinstance(data.get("hyperedges"), list):
        data["hyperedges"] = [h for h in data["hyperedges"] if not drop.intersection(map(str, h.get("nodes", h.get("members", [])) if isinstance(h, dict) else []))]
    fd, name = tempfile.mkstemp(prefix=scoped.stem + "-", suffix=".tmp", dir=scoped.parent)
    temp = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(data, stream, ensure_ascii=False)
        for attempt in range(20):
            try:
                temp.replace(scoped)
                break
            except PermissionError:
                # Windows refuses the rename while another request is replacing or reading the
                # same cache file. An equally fresh copy written by that request is just as good.
                if scoped.is_file() and scoped.stat().st_mtime >= graph_file.stat().st_mtime:
                    break
                if attempt == 19:
                    raise
                time.sleep(0.05)
    finally:
        temp.unlink(missing_ok=True)
    return scoped
