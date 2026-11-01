#!/usr/bin/env python3
"""Deterministic, read-only evidence retrieval over an existing Token Graph graph."""
from __future__ import annotations

import fnmatch
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

SCRIPTS = Path(__file__).resolve().parent
TOKEN_GRAPH = SCRIPTS / "token_graph.py"
ASK_ENGINE = SCRIPTS / "ask_engine.py"
MAX_LIMIT = 100
MAX_SOURCE_BYTES = 1_000_000
MAX_SNIPPET_LINES = 12
DOC_SUFFIXES = {".md", ".mdx", ".rst", ".adoc"}
SKIP_DIRS = {".git", ".hg", ".svn", ".idea", ".vscode", "node_modules", "vendor", "graphify-out",
             ".runtime", ".venv", "venv", "dist", "build", "target", "coverage", "__pycache__"}
CALL_RELATIONS = {"calls", "indirect_call"}
REFERENCE_RELATIONS = {"references"}
IMPORT_RELATIONS = {"imports", "imports_from", "dynamic_import", "re_exports", "depends_on", "uses", "inherits", "extends", "implements"}

try:
    from node_filters import is_generated, is_test
except ImportError:  # pragma: no cover - direct package import fallback
    from .node_filters import is_generated, is_test


def classify_source(source_file: str) -> str:
    """Classify a graph path without inferring the role of its contents."""
    path = source_file.replace("\\", "/").strip("/")
    if not path:
        return "other"
    parts = [part.casefold() for part in path.split("/") if part]
    name = parts[-1] if parts else ""
    if is_generated({"source_file": path}):
        return "generated"
    if any(part in {"eval", "evals", "evaluation", "evaluations", "benchmark", "benchmarks"} for part in parts):
        return "evaluation_fixture"
    if any(part in {"docs", "doc", "documentation", "wiki"} for part in parts) or name.endswith((".md", ".mdx", ".rst", ".adoc")):
        return "documentation"
    if any(part in {"examples", "example", "samples", "sample"} for part in parts):
        return "example"
    if is_test({"source_file": path}):
        return "test"
    return "production"


def engine_python() -> str:
    """Use Graphify's prepared runtime when available; never install globally."""
    executable = "Scripts/python.exe" if sys.platform == "win32" else "bin/python"
    package_runtime = SCRIPTS.parent / ".runtime" / executable
    if package_runtime.is_file():
        return str(package_runtime)
    configured_home = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")).expanduser()
    candidates = [
        configured_home / "skills" / "token-graph" / ".runtime" / executable,
        Path.home() / ".claude" / "skills" / "token-graph" / ".runtime" / executable,
    ]
    for installed_runtime in candidates:
        if installed_runtime.is_file():
            return str(installed_runtime)
    return sys.executable


def _source_path(project: Path, source_file: str) -> Path | None:
    raw = Path(source_file)
    if raw.is_absolute():
        candidate = raw.resolve()
    else:
        candidate = (project / raw).resolve()
    try:
        relative = candidate.relative_to(project.resolve())
    except ValueError:
        return None
    forbidden = {".git", ".hg", ".svn", "node_modules", "vendor", "graphify-out", ".runtime", ".venv", "venv"}
    if any(part.casefold() in forbidden or part.casefold().startswith(".env") for part in relative.parts):
        return None
    name = candidate.name.casefold()
    if name.endswith((".pem", ".key", ".p12", ".pfx")) or any(secret in name for secret in ("credential", "secret", "id_rsa", "id_ed25519")):
        return None
    return candidate


def _line_bounds(location: Any) -> tuple[int | None, int | None]:
    if isinstance(location, int):
        return (location, location)
    match = re.search(r"(?:^|[^0-9])L?(\d+)(?:\s*[-:]\s*L?(\d+))?\s*$", str(location or ""))
    if not match:
        return None, None
    start = int(match.group(1))
    end = int(match.group(2) or start)
    return (start, end) if start > 0 and end >= start else (None, None)


def _read_excerpt(project: Path, source_file: str, start: int, end: int | None = None, context: int = 2) -> dict[str, Any] | None:
    path = _source_path(project, source_file)
    if path is None:
        return None
    try:
        if not path.is_file() or path.stat().st_size > MAX_SOURCE_BYTES:
            return None
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError):
        return None
    if start < 1 or start > len(lines):
        return None
    first = max(1, start - max(0, context))
    last = min(len(lines), max(start + context, end or start + context), start + MAX_SNIPPET_LINES - 1)
    return {
        "file": path.relative_to(project.resolve()).as_posix(),
        "start_line": first,
        "end_line": last,
        "text": "\n".join(f"{number}: {lines[number - 1]}" for number in range(first, last + 1)),
        "file_mtime": path.stat().st_mtime,
    }


def _name_matches(label: str, excerpt: str) -> bool:
    name = re.sub(r"\(\)$", "", label.strip()).split("::")[-1].lstrip(".")
    if not name:
        return False
    return re.search(rf"(?<![A-Za-z0-9_$]){re.escape(name)}(?![A-Za-z0-9_$])", excerpt, re.I) is not None


def _node_visible(node: dict[str, Any], include_tests: bool, include_evaluation: bool, include_generated: bool) -> bool:
    kind = classify_source(str(node.get("source_file") or ""))
    return not (
        (kind == "test" and not include_tests)
        or (kind == "evaluation_fixture" and not include_evaluation)
        or (kind == "generated" and not include_generated)
    )


def _load_graph(project: Path) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    graph_file = project / "graphify-out" / "graph.json"
    if not graph_file.is_file():
        return None, {"operation": "", "query": "", "status": "missing_graph", "results": [], "truncated": False,
                      "message": "No graph index found. Build or update the graph first."}
    try:
        graph = json.loads(graph_file.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        return None, {"operation": "", "query": "", "status": "invalid_graph", "results": [], "truncated": False,
                      "message": f"Graph index could not be read: {error}"}
    if not isinstance(graph, dict) or not isinstance(graph.get("nodes"), list):
        return None, {"operation": "", "query": "", "status": "invalid_graph", "results": [], "truncated": False,
                      "message": "Graph index has no valid nodes array."}
    return graph, None


def _result_for_node(project: Path, graph_file: Path, node: dict[str, Any]) -> dict[str, Any]:
    source_file = str(node.get("source_file") or "")
    label = str(node.get("label") or node.get("id") or "")
    start, end = _line_bounds(node.get("source_location"))
    item: dict[str, Any] = {
        "file": source_file.replace("\\", "/") or None,
        "symbol": label or None,
        "id": str(node.get("id") or ""),
        "start_line": start,
        "end_line": end,
        "matched_line": start,
        "source_type": classify_source(source_file),
        "verification": "graph_only",
        "evidence": None,
    }
    if not source_file or start is None:
        return item
    excerpt = _read_excerpt(project, source_file, start, end, context=2)
    if excerpt is None:
        item["verification"] = "source_unavailable"
        return item
    item["file"] = excerpt["file"]
    item["matched_line"] = start
    item["start_line"] = excerpt["start_line"]
    item["end_line"] = excerpt["end_line"]
    item["evidence"] = excerpt["text"]
    item["verification"] = "stale_index" if excerpt["file_mtime"] > graph_file.stat().st_mtime else (
        "verified" if node.get("_file_match") or _name_matches(label, excerpt["text"]) else "graph_only"
    )
    return item


def _result_for_edge(project: Path, graph_file: Path, edge: dict[str, Any], node_by_id: dict[str, dict[str, Any]]) -> dict[str, Any]:
    source_id = str(edge.get("source") or "")
    target_id = str(edge.get("target") or "")
    source = node_by_id.get(source_id, {})
    target = node_by_id.get(target_id, {})
    source_file = str(edge.get("source_file") or "")
    start, end = _line_bounds(edge.get("source_location"))
    item: dict[str, Any] = {
        "relation": str(edge.get("relation") or "unknown"),
        "source": {"id": source_id, "symbol": str(source.get("label") or source_id), "file": source.get("source_file")},
        "target": {"id": target_id, "symbol": str(target.get("label") or target_id), "file": target.get("source_file")},
        "file": source_file.replace("\\", "/") or None,
        "start_line": start,
        "end_line": end,
        "matched_line": start,
        "source_type": classify_source(source_file),
        "verification": "graph_only",
        "evidence": None,
    }
    if not source_file or start is None:
        return item
    excerpt = _read_excerpt(project, source_file, start, end, context=1)
    if excerpt is None:
        item["verification"] = "source_unavailable"
        return item
    item["file"] = excerpt["file"]
    item["matched_line"] = start
    item["start_line"] = excerpt["start_line"]
    item["end_line"] = excerpt["end_line"]
    item["evidence"] = excerpt["text"]
    target_label = str(target.get("label") or target_id)
    current = "stale_index" if excerpt["file_mtime"] > graph_file.stat().st_mtime else (
        "verified" if _name_matches(target_label, excerpt["text"]) else "graph_only"
    )
    item["verification"] = current
    return item


def _query_graph(project: Path, question: str, budget: int) -> dict[str, Any]:
    python = engine_python()
    try:
        completed = subprocess.run(
            [python, str(ASK_ENGINE), str(project), "query", question, str(budget)],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180, check=False,
        )
    except subprocess.TimeoutExpired:
        return {"error": "Graph query timed out."}
    if completed.returncode:
        message = (completed.stderr or completed.stdout).strip().splitlines()
        return {"error": message[-1] if message else "Graph query failed."}
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError:
        return {"error": "Graph query returned invalid structured output."}
    return payload if isinstance(payload, dict) else {"error": "Graph query returned an invalid result."}


def _rank_nodes(nodes: list[dict[str, Any]], query: str) -> list[tuple[int, dict[str, Any]]]:
    normalized = query.casefold().strip()
    terms = [term for term in re.findall(r"[A-Za-z0-9_.$-]+", normalized) if len(term) > 1]
    ranked: list[tuple[int, dict[str, Any]]] = []
    for node in nodes:
        label = str(node.get("label") or "").casefold()
        node_id = str(node.get("id") or "").casefold()
        source_file = str(node.get("source_file") or "").casefold()
        if not label and not node_id and not source_file:
            continue
        if normalized and normalized == label:
            score = 100
        elif normalized and normalized == node_id:
            score = 98
        elif normalized and normalized in label:
            score = 80
        elif normalized and normalized in node_id:
            score = 70
        else:
            score = sum(4 if term in label else 2 if term in node_id else 1 if term in source_file else 0 for term in terms)
        if score:
            ranked.append((score, node))
    ranked.sort(key=lambda pair: (-pair[0], str(pair[1].get("source_file") or ""), str(pair[1].get("label") or ""), str(pair[1].get("id") or "")))
    return ranked


def _is_ignored(project: Path, relative: str) -> bool:
    """Apply simple gitignore-style Graphify patterns during documentation discovery."""
    rules: list[tuple[bool, str]] = []
    try:
        raw_rules = (project / ".graphifyignore").read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError):
        raw_rules = []
    for raw in raw_rules:
        rule = raw.strip()
        if not rule or rule.startswith("#"):
            continue
        negated = rule.startswith("!")
        rules.append((negated, (rule[1:] if negated else rule).lstrip("/")))
    ignored = False
    paths = [relative, *[f"{parent}/" for parent in Path(relative).parents if str(parent) != "."]]
    for negated, pattern in rules:
        plain = pattern.rstrip("/")
        matched = any(
            fnmatch.fnmatchcase(candidate, plain)
            or fnmatch.fnmatchcase(candidate, pattern)
            or ("/" not in pattern and fnmatch.fnmatchcase(Path(candidate.rstrip("/")).name, pattern))
            for candidate in paths
        )
        if matched:
            ignored = not negated
    return ignored


def _search_document_text(project: Path, query: str) -> list[dict[str, Any]]:
    """Find matching documentation lines locally without sending contents to a model."""
    terms = [term.casefold() for term in re.findall(r"[A-Za-z0-9_.$-]+", query) if len(term) > 1]
    matches: list[dict[str, Any]] = []
    for directory, dirnames, filenames in os.walk(project):
        dirnames[:] = [name for name in dirnames if name.casefold() not in SKIP_DIRS and not name.startswith(".")]
        for filename in filenames:
            candidate = Path(directory) / filename
            if candidate.suffix.casefold() not in DOC_SUFFIXES:
                continue
            relative = candidate.relative_to(project).as_posix()
            if _source_path(project, relative) is None or _is_ignored(project, relative):
                continue
            try:
                if candidate.stat().st_size > MAX_SOURCE_BYTES:
                    continue
                content = candidate.read_text(encoding="utf-8").splitlines()
            except (OSError, UnicodeError):
                continue
            for number, text in enumerate(content, 1):
                if query.casefold() in text.casefold() or (terms and all(term in text.casefold() for term in terms)):
                    matches.append({
                        "id": f"doc:{relative}:{number}", "label": candidate.stem,
                        "source_file": relative, "source_location": f"L{number}", "_file_match": True,
                    })
                    break
    return matches


def search_evidence(
    project: Path | str,
    query: str,
    *,
    operation: str = "query",
    target: str | None = None,
    line: int | None = None,
    limit: int = 10,
    include_tests: bool = False,
    include_evaluation: bool = False,
    include_generated: bool = False,
) -> dict[str, Any]:
    """Return concise, cited evidence from the existing graph and source files; never invoke a model."""
    root = Path(project).expanduser().resolve()
    op = operation.strip().casefold().replace("-", "_")
    aliases = {"files": "find_files", "symbols": "find_symbol", "references": "find_references",
               "callers": "find_callers", "callees": "find_callees", "dependencies": "find_dependencies",
               "source": "find_source", "relationships": "find_relationships", "docs": "find_docs"}
    op = aliases.get(op, op)
    allowed = {"query", "find_files", "find_symbol", "find_references", "find_callers", "find_callees",
               "find_dependencies", "find_source", "find_relationships", "find_docs", "path", "affected"}
    if op not in allowed:
        return {"operation": op, "query": query, "status": "invalid_operation", "results": [], "truncated": False,
                "message": "Choose a supported deterministic exploration operation."}
    try:
        result_limit = min(MAX_LIMIT, max(1, int(limit)))
    except (TypeError, ValueError):
        result_limit = 10
    if not root.is_dir():
        return {"operation": op, "query": query, "status": "invalid_project", "results": [], "truncated": False}
    graph, error = _load_graph(root)
    if error:
        error["operation"], error["query"] = op, query
        return error
    assert graph is not None
    graph_file = root / "graphify-out" / "graph.json"
    # A node without a source path cannot support a useful citation. Older or
    # partially merged graphs may contain pathless duplicates of real symbols;
    # ignore them so they cannot crowd out grounded results or create false
    # ambiguity.
    nodes = [node for node in graph["nodes"] if isinstance(node, dict) and node.get("source_file")
             and _node_visible(node, include_tests, include_evaluation, include_generated)]
    node_by_id = {str(node.get("id")): node for node in nodes if node.get("id") is not None}
    links = graph.get("links", graph.get("edges", []))
    links = [edge for edge in links if isinstance(edge, dict)
             and str(edge.get("source")) in node_by_id and str(edge.get("target")) in node_by_id]
    if not query.strip():
        return {"operation": op, "query": query, "status": "insufficient_evidence", "results": [], "truncated": False,
                "message": "Provide a file, symbol, or search term."}

    if op == "query":
        payload = _query_graph(root, query, min(6000, max(1200, result_limit * 220)))
        if payload.get("error"):
            return {"operation": op, "query": query, "status": "query_failed", "results": [], "truncated": False,
                    "message": payload["error"]}
        ids = list(payload.get("seeds", [])) + [node_id for node_id in payload.get("nodes", {}) if node_id not in payload.get("seeds", [])]
        candidates = [node_by_id[node_id] for node_id in ids if node_id in node_by_id]
        truncated = "TRUNCATED" in str(payload.get("text", "")) or len(candidates) > result_limit
    elif op in {"path", "affected"}:
        args = [str(SCRIPTS / "ask_engine.py"), str(root), op]
        if op == "path":
            if not target:
                return {"operation": op, "query": query, "status": "insufficient_evidence", "results": [], "truncated": False,
                        "message": "Provide a target symbol with --target."}
            args += [query, target]
        else:
            args += [query, "2"]
        python = engine_python()
        try:
            completed = subprocess.run([python, *args], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180, check=False)
            payload = json.loads(completed.stdout) if completed.returncode == 0 else {"error": completed.stderr.strip() or completed.stdout.strip()}
        except (subprocess.TimeoutExpired, json.JSONDecodeError):
            payload = {"error": "Graph traversal failed or returned invalid output."}
        if payload.get("error"):
            return {"operation": op, "query": query, "status": "query_failed", "results": [], "truncated": False,
                    "message": payload["error"]}
        ids = payload.get("route", []) if op == "path" else list(payload.get("nodes", {}))
        candidates = [node_by_id[node_id] for node_id in ids if node_id in node_by_id]
        truncated = len(candidates) > result_limit
    elif op in {"find_files", "find_docs"}:
        source_files = sorted({str(node.get("source_file") or "") for node in nodes if node.get("source_file")})
        candidates = []
        for source_file in source_files:
            kind = classify_source(source_file)
            if op == "find_docs" and kind != "documentation":
                continue
            if query.casefold() in source_file.casefold():
                candidates.append({"id": source_file, "label": Path(source_file).name, "source_file": source_file,
                                   "source_location": "L1", "_file_match": True})
        if op == "find_docs":
            candidates.extend(_search_document_text(root, query))
            # Keep a stable first occurrence when a document is also represented in graph.json.
            unique = {}
            for node in candidates:
                unique.setdefault(str(node.get("id") or node.get("source_file")), node)
            candidates = list(unique.values())
        truncated = len(candidates) > result_limit
    elif op == "find_source":
        explicit_path = _source_path(root, query)
        if explicit_path is not None and ("/" in query or Path(query).suffix):
            relative = explicit_path.relative_to(root).as_posix()
            candidates = [{"id": relative, "label": explicit_path.name, "source_file": relative,
                           "source_location": f"L{max(1, line or 1)}", "_file_match": True}]
        else:
            candidates = [node for _score, node in _rank_nodes(nodes, query) if node.get("source_file")]
            if line is not None:
                candidates = [dict(node, source_location=f"L{max(1, int(line))}") for node in candidates]
        truncated = len(candidates) > result_limit
    else:
        ranked = _rank_nodes(nodes, query)
        ambiguous = False
        if op == "find_symbol":
            candidates = [node for _score, node in ranked]
            ambiguous = len(ranked) > 1 and ranked[0][0] == ranked[1][0]
            truncated = len(candidates) > result_limit
        else:
            matched_ids = {str(node.get("id")) for _score, node in ranked[:max(1, result_limit)]}
            if not matched_ids:
                candidates = []
            else:
                if op == "find_callers":
                    selected = [edge for edge in links if edge.get("relation") in CALL_RELATIONS and str(edge.get("target")) in matched_ids]
                elif op == "find_callees":
                    selected = [edge for edge in links if edge.get("relation") in CALL_RELATIONS and str(edge.get("source")) in matched_ids]
                elif op == "find_references":
                    selected = [edge for edge in links if edge.get("relation") in REFERENCE_RELATIONS and (str(edge.get("source")) in matched_ids or str(edge.get("target")) in matched_ids)]
                elif op == "find_dependencies":
                    selected = [edge for edge in links if edge.get("relation") in IMPORT_RELATIONS and (str(edge.get("source")) in matched_ids or str(edge.get("target")) in matched_ids)]
                else:
                    selected = [edge for edge in links if str(edge.get("source")) in matched_ids or str(edge.get("target")) in matched_ids]
                candidates = selected
                truncated = len(candidates) > result_limit

    results: list[dict[str, Any]] = []
    if op in {"find_references", "find_callers", "find_callees", "find_dependencies", "find_relationships"}:
        for edge in candidates[:result_limit]:
            results.append(_result_for_edge(root, graph_file, edge, node_by_id))
    else:
        seen: set[str] = set()
        for node in candidates:
            key = str(node.get("id") or node.get("source_file") or "")
            if key in seen:
                continue
            seen.add(key)
            results.append(_result_for_node(root, graph_file, node))
            if len(results) >= result_limit:
                break
    status = "complete" if results else "insufficient_evidence"
    if op == "find_symbol" and ambiguous:
        status = "ambiguous"
    return {
        "operation": op,
        "query": query,
        "status": status,
        "results": results,
        "truncated": truncated,
        "result_limit": result_limit,
    }
