#!/usr/bin/env python3
"""Build and query local project graphs using Token Graph’s bundled offline engine."""
from __future__ import annotations
import argparse
import json
import html
import re
import contextlib
import io
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
import urllib.error
import urllib.request

SECRET_ENV = {
    "GEMINI_API_KEY", "GOOGLE_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY",
    "DEEPSEEK_API_KEY", "KIMI_API_KEY", "OPENROUTER_API_KEY", "MISTRAL_API_KEY",
    "COHERE_API_KEY", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy",
    "https_proxy", "all_proxy",
}
LOCAL_OLLAMA = "http://127.0.0.1:11434"
QUIET_LOG: Path | None = None


def engine_python() -> str:
    runtime = Path(__file__).resolve().parents[1] / ".runtime" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    return str(runtime) if runtime.is_file() else sys.executable


def engine_command() -> list[str]:
    return [engine_python(), str(Path(__file__).with_name("offline_engine.py"))]


def clean_env(*, ollama: bool = False, vision: bool = False) -> dict[str, str]:
    env = os.environ.copy()
    for key in list(env):
        upper = key.upper()
        if key in SECRET_ENV or upper.endswith(("_API_KEY", "_API_BASE", "_BASE_URL")) or "_PROXY" in upper:
            env.pop(key, None)
    env.pop("GRAPHIFY_OUT", None)
    # Graphify otherwise persists query text in ~/.cache. Token Graph keeps
    # query artifacts with this project and disables that extra local log.
    env["GRAPHIFY_QUERY_LOG_DISABLE"] = "1"
    if ollama:
        # Override user-provided hosts and proxies: semantic extraction must stay on this device.
        env["OLLAMA_HOST"] = LOCAL_OLLAMA
        env["OLLAMA_BASE_URL"] = LOCAL_OLLAMA + "/v1"
        env["OLLAMA_API_KEY"] = "token-graph-local"
        env["NO_PROXY"] = "127.0.0.1,localhost"
        env["no_proxy"] = "127.0.0.1,localhost"
        if vision:
            env["GRAPHIFY_OLLAMA_VISION"] = "1"
        else:
            env.pop("GRAPHIFY_OLLAMA_VISION", None)
    scripts = Path(__file__).resolve().parent
    env["PYTHONPATH"] = os.pathsep.join([str(scripts), str(scripts.parent / "vendor")])
    env["TOKEN_GRAPH_OFFLINE"] = "1"
    env["TOKEN_GRAPH_ALLOW_OLLAMA"] = "1" if ollama else "0"
    env["PYTHONHASHSEED"] = "0"
    env["HF_HUB_OFFLINE"] = "1"
    env["TRANSFORMERS_OFFLINE"] = "1"
    return env


def project_path(raw: str) -> Path:
    if "://" in raw:
        raise ValueError("Only local project directories are supported; URLs are not accepted.")
    path = Path(raw).expanduser().resolve()
    if not path.is_dir():
        raise ValueError(f"Project directory does not exist: {path}")
    return path


def prepare_project(root: Path) -> None:
    patterns = [".env", ".env.*", "*.env", "**/.env", "**/.env.*", "**/*.env", "**/*credentials*", "**/*.pem", "**/*.key", "**/*.pfx", "**/node_modules/**", "**/vendor/**", "**/.runtime/**", "**/bin/**", "**/obj/**", "**/__pycache__/**", ".git/**", "**/.explorer-*.json", "graphify-out/**"]
    path = root / ".graphifyignore"
    previous = path.read_text(encoding="utf-8") if path.exists() else ""
    missing = [pattern for pattern in patterns if pattern not in previous.splitlines()]
    if missing: path.write_text(previous.rstrip() + "\n" + "\n".join(missing) + "\n", encoding="utf-8")
    path = root / ".gitignore"
    previous = path.read_text(encoding="utf-8") if path.exists() else ""
    if "graphify-out/" not in previous.splitlines(): path.write_text(previous.rstrip() + "\ngraphify-out/\n", encoding="utf-8")


def run_engine(root: Path, *args: str, local_model: bool = False, vision: bool = False) -> int:
    if args and args[0] in {"extract", "update"}: prepare_project(root)
    command = [*engine_command(), *args]
    env = clean_env(ollama=local_model, vision=vision)
    if QUIET_LOG is None:
        return subprocess.run(command, cwd=root, env=env, check=False).returncode
    QUIET_LOG.parent.mkdir(parents=True, exist_ok=True)
    with QUIET_LOG.open("a", encoding="utf-8") as log:
        return subprocess.run(command, cwd=root, env=env, stdout=log, stderr=subprocess.STDOUT, check=False).returncode


def require_local_model(model: str) -> None:
    if not model or "://" in model:
        raise ValueError("--model must be the name of an Ollama model already installed on this computer, not a URL or remote provider.")
    request = urllib.request.Request(LOCAL_OLLAMA + "/api/tags", headers={"Accept": "application/json"})
    try:
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(request, timeout=3) as response:
            payload = json.load(response)
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as error:
        raise RuntimeError("Local Ollama is unavailable at 127.0.0.1:11434. Start/fix Ollama locally; Token Graph will not use a hosted API or download a model.") from error
    installed = {item.get("name") for item in payload.get("models", [])}
    if model not in installed and not any(name and name.split(":", 1)[0] == model for name in installed):
        raise RuntimeError(f"Model {model!r} is not installed in local Ollama. Pull it yourself while online, then rerun; Token Graph never downloads models.")


def write_local_svg(root: Path) -> int:
    """Dependency-free SVG fallback for installations without the engine's matplotlib extra."""
    data = json.loads(graph_file(root).read_text(encoding="utf-8"))
    nodes = data.get("nodes", [])
    links = data.get("links", data.get("edges", []))
    width, height = 1600, 1100
    cx, cy = width / 2, height / 2
    radius = max(80.0, min(width, height) * 0.43)
    ids = [str(node.get("id", node.get("name", node.get("label", f"node-{i}")))) for i, node in enumerate(nodes)]
    positions = {key: (cx + radius * math.cos(2 * math.pi * i / max(1, len(ids))), cy + radius * math.sin(2 * math.pi * i / max(1, len(ids)))) for i, key in enumerate(ids)}
    palette = ("#8b7cff", "#39c8d8", "#54d6a6", "#f4c95d", "#ec7eaa", "#ff916b")
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" role="img"><title>Token Graph local graph</title><rect width="100%" height="100%" fill="#0b1020"/>']
    for edge in links:
        source = str(edge.get("source", edge.get("from", "")))
        target = str(edge.get("target", edge.get("to", "")))
        if source in positions and target in positions:
            x1, y1 = positions[source]; x2, y2 = positions[target]
            title = html.escape(str(edge.get("relation", edge.get("type", "relationship"))))
            parts.append(f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" stroke="#53617d" stroke-opacity=".35" stroke-width="1"><title>{title}</title></line>')
    for i, node in enumerate(nodes):
        node_id = ids[i]
        x, y = positions[node_id]
        label = html.escape(str(node.get("label", node.get("name", node_id))))
        color = palette[i % len(palette)]
        parts.append(f'<g><title>{label}</title><circle cx="{x:.1f}" cy="{y:.1f}" r="7" fill="{color}"/><text x="{x + 10:.1f}" y="{y + 4:.1f}" fill="#e8edf8" font-family="system-ui,sans-serif" font-size="10">{label}</text></g>')
    parts.append('</svg>')
    output = root / "graphify-out" / "graph.svg"
    output.write_text("\n".join(parts), encoding="utf-8")
    print(f"Created dependency-free local SVG: {output}")
    return 0


def graph_file(root: Path) -> Path:
    graph = root / "graphify-out" / "graph.json"
    if not graph.is_file():
        raise ValueError(f"Graph not found: {graph}. Build it first with a Token Graph build command.")
    return graph


def write_embedded_viewer(root: Path) -> Path:
    """Create an interactive file:// viewer with graph data embedded and no CDN requests."""
    benchmark = subprocess.run(
        [sys.executable, str(Path(__file__).with_name("token_savings.py")), str(root), "--json"],
        cwd=root, env=clean_env(), capture_output=True, text=True, check=False,
    )
    if benchmark.returncode:
        print("Token Graph benchmark unavailable; see graphify-out/benchmark-error.log.")
        (root / "graphify-out" / "benchmark-error.log").write_text(benchmark.stderr, encoding="utf-8")
    else:
        (root / "graphify-out" / "benchmark-error.log").unlink(missing_ok=True)
    data = json.loads(graph_file(root).read_text(encoding="utf-8"))
    source = Path(__file__).resolve().parents[1] / "assets" / "graph-viewer.html"
    page = source.read_text(encoding="utf-8")
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    assignment = "window.GRAPHIFY_DATA=" + payload + ";"
    if "window.GRAPHIFY_DATA=null;" in page:
        page = page.replace("window.GRAPHIFY_DATA=null;", assignment, 1)
    elif "window.GRAPHIFY_DATA = null;" in page:
        page = page.replace("window.GRAPHIFY_DATA = null;", assignment, 1)
    elif "</head>" in page.lower():
        page = re.sub(r"</head>", "<script>" + assignment + "</script>\n</head>", page, count=1, flags=re.I)
    else:
        raise RuntimeError("The bundled offline graph viewer has no graph-data placeholder or head section.")
    destination = root / "graphify-out" / "graph.html"
    destination.write_text(page, encoding="utf-8")
    return destination


def write_local_tree(root: Path) -> Path:
    """Write a dependency-free file/community hierarchy view from the local graph."""
    data = json.loads(graph_file(root).read_text(encoding="utf-8"))
    nodes = data.get("nodes", [])
    links = data.get("links", data.get("edges", []))
    labels = {str(n.get("id", n.get("name", ""))): str(n.get("label", n.get("name", n.get("id", "")))) for n in nodes}
    by_file: dict[str, list[dict]] = {}
    for item in nodes:
        by_file.setdefault(str(item.get("source_file") or "(unknown file)"), []).append(item)
    counts: dict[str, int] = {}
    for edge in links:
        for key in ("source", "from"):
            if edge.get(key) is not None:
                counts[str(edge[key])] = counts.get(str(edge[key]), 0) + 1
                break
    sections = []
    for filename, items in sorted(by_file.items()):
        entries = []
        for item in sorted(items, key=lambda node: str(node.get("label", node.get("name", ""))).lower()):
            node_id = str(item.get("id", item.get("name", "")))
            label = html.escape(labels.get(node_id, node_id))
            kind = html.escape(str(item.get("type", item.get("file_type", "node"))))
            community = html.escape(str(item.get("community_name", item.get("community", "Other"))))
            entries.append(f"<li><strong>{label}</strong><span>{kind} · {community} · {counts.get(node_id, 0)} outgoing links</span></li>")
        sections.append(f"<details><summary>{html.escape(filename)} <small>({len(items)} nodes)</small></summary><ul>{''.join(entries)}</ul></details>")
    page = """<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>Token Graph local hierarchy</title>
<style>body{margin:2rem auto;padding:0 1rem;max-width:1000px;background:#0b1020;color:#e8edf8;font:15px system-ui}h1{color:#a99cff}details{margin:.5rem 0;padding:.8rem 1rem;border:1px solid #27334b;border-radius:10px;background:#111a2d}summary{cursor:pointer;font-weight:600}small,li span{color:#94a1b8}ul{list-style:none;padding-left:1rem}li{display:flex;justify-content:space-between;gap:1rem;padding:.35rem 0;border-top:1px solid #222d43}li strong{overflow-wrap:anywhere}</style>
<h1>Local graph hierarchy</h1><p>Grouped by source file. All data is embedded locally; this page loads no remote scripts.</p>""" + "".join(sections)
    destination = root / "graphify-out" / "GRAPH_TREE.html"
    destination.write_text(page, encoding="utf-8")
    return destination


def sanitize_local_html(path: Path) -> None:
    """Remove external resources and executable scripts from generated HTML exports."""
    page = path.read_text(encoding="utf-8")
    page = re.sub(r"<script\b[^>]*>.*?</script\s*>", "", page, flags=re.I | re.S)
    page = re.sub(r"<link\b[^>]*href=[\"']https?://[^\"']+[\"'][^>]*>", "", page, flags=re.I | re.S)
    path.write_text(page, encoding="utf-8")


def finish_graph(root: Path) -> int:
    # cluster-only creates the engine's report and its interactive graph.html locally.
    if run_engine(root, "cluster-only", str(root), "--graph", str(graph_file(root)), "--no-label", "--no-viz"):
        return 1
    write_embedded_viewer(root)
    return 0


def finish_local_graph(root: Path, model: str, vision: bool) -> int:
    if run_engine(root, "cluster-only", str(root), "--graph", str(graph_file(root)), "--backend=ollama", f"--model={model}", local_model=True, vision=vision):
        return 1
    write_embedded_viewer(root)
    return 0


def transcribe_local_media(root: Path, model_dir: str | None, *, skip_missing: bool = False) -> int:
    helper = Path(__file__).with_name("transcribe_local.py")
    media_out = root / "graphify-out" / "transcripts"
    model_arg = str(Path(model_dir).expanduser().resolve()) if model_dir else ""
    command = [engine_python(), str(helper), str(root), model_arg, str(media_out)]
    if skip_missing:
        command.append("--skip-missing")
    if QUIET_LOG is None:
        return subprocess.run(command, cwd=root, env=clean_env(), check=False).returncode
    with QUIET_LOG.open("a", encoding="utf-8") as log:
        return subprocess.run(command, cwd=root, env=clean_env(), stdout=log, stderr=subprocess.STDOUT, check=False).returncode


def remove_stale_transcript_nodes(root: Path) -> None:
    """Drop nodes/edges from deleted derived transcripts before re-merging current media."""
    graph_path = graph_file(root)
    data = json.loads(graph_path.read_text(encoding="utf-8"))
    transcript_root = (root / "graphify-out" / "transcripts").resolve()
    nodes_key = "nodes"
    nodes = data.get(nodes_key, [])
    stale_ids: set[str] = set()
    kept_nodes = []
    for node in nodes:
        source = node.get("source_file")
        if source:
            source_path = Path(str(source))
            if not source_path.is_absolute():
                source_path = root / source_path
            try:
                source_path.resolve().relative_to(transcript_root)
                stale_ids.add(str(node.get("id", node.get("name", ""))))
                continue
            except ValueError:
                pass
        kept_nodes.append(node)
    if not stale_ids:
        return
    data[nodes_key] = kept_nodes
    edges_key = "links" if "links" in data else "edges"
    data[edges_key] = [edge for edge in data.get(edges_key, []) if str(edge.get("source")) not in stale_ids and str(edge.get("target")) not in stale_ids]
    if isinstance(data.get("hyperedges"), list):
        data["hyperedges"] = [edge for edge in data["hyperedges"] if not stale_ids.intersection(map(str, edge.get("nodes", edge.get("members", []))))]
    graph_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def merge_transcript_graph(root: Path, model: str, vision: bool) -> int:
    transcript_dir = root / "graphify-out" / "transcripts"
    remove_stale_transcript_nodes(root)
    if not transcript_dir.is_dir() or not any(transcript_dir.rglob("*.txt")):
        return 0
    index_out = root / "graphify-out" / "transcript-index"
    if run_engine(root, "extract", str(transcript_dir), "--backend", "ollama", "--model", model, "--no-cluster", "--out", str(index_out), local_model=True, vision=vision):
        return 1
    media_graph = index_out / "graphify-out" / "graph.json"
    if not media_graph.is_file():
        raise RuntimeError(f"The engine did not create the local transcript graph: {media_graph}")
    merged = root / "graphify-out" / ".token-graph-merged.json"
    merged.unlink(missing_ok=True)
    if run_engine(root, "merge-graphs", str(graph_file(root)), str(media_graph), "--out", str(merged), local_model=True):
        return 1
    merged.replace(graph_file(root))
    return 0


def build_local(root: Path, model: str, vision: bool, whisper_model_dir: str | None, *, skip_missing_media: bool = False) -> int:
    require_local_model(model)
    # Video/audio transcription is local and uses only a cached model directory.
    if transcribe_local_media(root, whisper_model_dir, skip_missing=skip_missing_media):
        return 1
    if run_engine(root, "extract", str(root), "--backend", "ollama", "--model", model, "--no-cluster", local_model=True, vision=vision):
        return 1
    if merge_transcript_graph(root, model, vision):
        return 1
    return finish_local_graph(root, model, vision)


def launch_local(root: Path, question: str | None, refresh: bool, *, open_page: bool = False, budget: int = 800, contexts: list[str] | None = None, tests: bool = True, generated: bool = False) -> int:
    """Build/query locally; return only the focused query result, or save the graph locally."""
    global QUIET_LOG
    output = root / "graphify-out"
    output.mkdir(parents=True, exist_ok=True)
    QUIET_LOG = output / "token-graph.log"
    mode = "existing graph"
    graph = output / "graph.json"
    query_return_code = 0
    try:
        if refresh or not graph.is_file():
            if run_engine(root, "extract", str(root), "--code-only", "--no-cluster") or finish_graph(root):
                raise RuntimeError("Local code graph build failed; see graphify-out/token-graph.log")
            mode = "bundled offline AST engine (no model required)"
        if question:
            if budget < 1: raise ValueError("--budget must be positive")
            from node_filters import scoped_graph
            answer_graph = scoped_graph(graph, tests=tests, generated=generated)
            query_command = [*engine_command(), "query", question, "--graph", str(answer_graph), "--budget", str(budget)]
            for context in contexts or []: query_command.extend(["--context", context])
            with QUIET_LOG.open("a", encoding="utf-8") as log:
                result = subprocess.run(query_command, cwd=root, env=clean_env(), capture_output=True, text=True, check=False)
                query_return_code = result.returncode
                if result.stdout:
                    log.write(result.stdout)
                if result.stderr:
                    log.write(result.stderr)
            query_text = (result.stdout + "\n" + result.stderr).strip()
            if result.returncode and not query_text:
                query_text = "The local graph query failed. See graphify-out/token-graph.log for local diagnostics."
            page = ('<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>Token Graph local result</title><style>body{margin:2rem auto;padding:0 1rem;max-width:1000px;background:#0b1020;color:#e8edf8;font:16px system-ui}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#121a2d;border:1px solid #27324a;border-radius:12px;padding:1.25rem}h1{color:#9f91ff}</style><h1>Local graph query result</h1><pre>' + html.escape(query_text) + '</pre>')
            destination = output / "query-result.html"
            destination.write_text(page, encoding="utf-8")
        else:
            destination = write_embedded_viewer(root)
        media_status = output / "TOKEN_GRAPH_MEDIA_STATUS.md"
        media_note = media_status.read_text(encoding="utf-8") if media_status.is_file() else ""
        delivery_note = (
            "Selected graph-query context was returned to the invoking assistant."
            if question else "No graph-query context was returned to the assistant."
        )
        (output / "TOKEN_GRAPH_STATUS.md").write_text(
            "# Local Token Graph\n\n" + f"Build mode: {mode}\n\n" + media_note + "\n" +
            "Graph construction, indexing, and traversal ran locally. " + delivery_note +
            " The full graph and full source files were not returned.\n",
            encoding="utf-8",
        )
        if question:
            print(query_text or "The local graph query returned no matching context.")
        else:
            print(f"Token Graph saved locally: {destination}. Open it manually when needed.")
        return query_return_code
    finally:
        QUIET_LOG = None


def manage_hooks(root: Path, install: bool) -> str:
    import shlex
    result = subprocess.run(["git", "rev-parse", "--git-path", "hooks"], cwd=root, capture_output=True, text=True, check=True)
    hooks = Path(result.stdout.strip())
    if not hooks.is_absolute(): hooks = root / hooks
    hooks.mkdir(parents=True, exist_ok=True)
    start, end = "# token-graph-hook-start", "# token-graph-hook-end"
    command = " ".join(shlex.quote(x) for x in [engine_python(), str(Path(__file__).resolve()), "update", str(root)])
    for name in ("post-commit", "post-checkout"):
        path = hooks / name
        old = path.read_text(encoding="utf-8") if path.exists() else "#!/bin/sh\n"
        cleaned = re.sub(re.escape(start) + r".*?" + re.escape(end) + r"\n?", "", old, flags=re.S)
        if install:
            cleaned = cleaned.rstrip() + "\n" + start + "\n" + command + " >> " + shlex.quote(str(root / "graphify-out" / "hook.log")) + " 2>&1\n" + end + "\n"
        if cleaned != old: path.write_text(cleaned, encoding="utf-8")
        if install: path.chmod(path.stat().st_mode | 0o100)
    return "Token Graph hooks installed." if install else "Token Graph hooks removed."


def local_feature(root: Path, action: str, *, question: str | None = None,
                  source: str | None = None, target: str | None = None,
                  node: str | None = None, relations: list[str] | None = None,
                  depth: int = 2, top: int = 10, export_format: str | None = None,
                  queries: int = 1, budget: int = 800, contexts: list[str] | None = None,
                  merge_graphs: list[str] | None = None, tests: bool = True, generated: bool = False) -> int:
    """Run one allowlisted local action; return bounded query context or save a local artifact."""
    global QUIET_LOG
    output = root / "graphify-out"
    output.mkdir(parents=True, exist_ok=True)
    QUIET_LOG = output / "token-graph.log"
    graph = graph_file(root)
    # Question-answering actions read a copy without generated code (and without tests on request):
    # those nodes crowd the bounded answer without explaining the code.
    from node_filters import scoped_graph
    answer_graph = scoped_graph(graph, tests=tests, generated=generated) if action in ("query", "path", "explain", "affected", "hubs") else graph
    commands: dict[str, list[str]] = {
        "query": ["query", question or "", "--graph", str(answer_graph)],
        "path": ["path", source or "", target or "", "--graph", str(answer_graph)],
        "explain": ["explain", node or "", "--graph", str(answer_graph)],
        "affected": ["affected", node or "", "--graph", str(answer_graph), "--depth", str(depth)],
        "hubs": ["god-nodes", "--graph", str(answer_graph), "--top", str(top)],
        "update": ["update", str(root), "--no-cluster"],
        "recluster": ["cluster-only", str(root), "--graph", str(graph), "--no-label"],
        "tree": [],
        "hook-install": ["hook", "install"],
        "hook-uninstall": ["hook", "uninstall"],
        "reflect": ["reflect", "--graph", str(graph)],
    }
    if action == "query":
        if not question: raise ValueError("--question is required for query")
        if budget < 1: raise ValueError("--budget must be positive")
        commands[action].extend(["--budget", str(budget)])
        for context in contexts or []: commands[action].extend(["--context", context])
    elif action == "path":
        if not source or not target: raise ValueError("--source and --target are required for path")
    elif action in ("explain", "affected") and not node:
        raise ValueError("--node is required for this action")
    elif action == "affected":
        if depth < 1: raise ValueError("--depth must be positive")
        for relation in relations or []: commands[action].extend(["--relation", relation])
    elif action == "hubs" and top < 1:
        raise ValueError("--top must be positive")
    elif action == "benchmark":
        if queries < 1: raise ValueError("--queries must be positive")
        commands[action] = [sys.executable, str(Path(__file__).with_name("token_savings.py")), str(root), "--queries", str(queries)]
        if question: commands[action].extend(["--question", question])
    elif action == "stats":
        data = json.loads(graph.read_text(encoding="utf-8"))
        nodes = data.get("nodes", [])
        edges = data.get("links", data.get("edges", []))
        communities = {str(item.get("community")) for item in nodes if item.get("community") is not None}
        files = {item.get("source_file") for item in nodes if item.get("source_file")}
        result = f"Nodes: {len(nodes):,}\nConnections: {len(edges):,}\nCommunities: {len(communities):,}\nRepresented files: {len(files):,}"
        commands[action] = []
    elif action == "export":
        allowed_formats = {"html", "callflow-html", "svg", "graphml", "wiki", "obsidian", "neo4j", "falkordb"}
        if export_format not in allowed_formats:
            raise ValueError("--format must be html, callflow-html, svg, graphml, wiki, obsidian, neo4j, or falkordb")
        if export_format == "callflow-html":
            commands[action] = ["export", export_format, str(graph), "--output", str(output / "callflow.html")]
        elif export_format == "obsidian":
            commands[action] = ["export", export_format, "--graph", str(graph), "--dir", str(output / "obsidian")]
        elif export_format == "html":
            commands[action] = []
        else:
            commands[action] = ["export", export_format, "--graph", str(graph)]
    elif action == "merge":
        if not merge_graphs:
            raise ValueError("At least one --merge-graph file is required")
        local_graphs = []
        for raw in merge_graphs:
            if "://" in raw: raise ValueError("Only local graph files are supported")
            local_path = Path(raw).expanduser().resolve()
            if not local_path.is_file(): raise ValueError(f"Graph file does not exist: {local_path}")
            local_graphs.append(str(local_path))
        merged_path = output / "merged-graph.json"
        commands[action] = ["merge-graphs", *local_graphs, "--out", str(merged_path)]
    elif action == "diagnose":
        commands[action] = ["diagnose", "multigraph", "--graph", str(graph), "--json"]
    elif action in ("watch-start", "watch-stop", "watch-status"):
        commands[action] = []
    elif action not in commands:
        raise ValueError(f"Unsupported local action: {action}")

    try:
        if action in ("hook-install", "hook-uninstall"):
            completed = subprocess.CompletedProcess([], 0, manage_hooks(root, action == "hook-install"), "")
        elif action == "stats":
            completed = subprocess.CompletedProcess([], 0, result, "")
        elif action in ("tree",):
            completed = subprocess.CompletedProcess([], 0, str(write_local_tree(root)), "")
        elif action == "export" and export_format == "html":
            completed = subprocess.CompletedProcess([], 0, str(write_embedded_viewer(root)), "")
        elif action == "update":
            update_result = subprocess.run([*engine_command(), *commands[action]], cwd=root, env=clean_env(), capture_output=True, text=True, check=False)
            if update_result.returncode == 0:
                finish_code = finish_graph(root)
                completed = subprocess.CompletedProcess([], finish_code, update_result.stdout, update_result.stderr)
            else:
                completed = update_result
        elif action == "recluster":
            recluster_result = subprocess.run([*engine_command(), *commands[action]], cwd=root, env=clean_env(), capture_output=True, text=True, check=False)
            if recluster_result.returncode == 0:
                write_embedded_viewer(root)
            completed = recluster_result
        elif action in ("watch-start", "watch-stop", "watch-status"):
            pid_file = output / ".token-graph-watch.pid"
            pid = None
            try: pid = int(pid_file.read_text(encoding="utf-8").strip())
            except (OSError, ValueError): pass
            if action == "watch-start":
                if pid:
                    try: os.kill(pid, 0)
                    except ProcessLookupError: pid = None
                if pid:
                    completed = subprocess.CompletedProcess([], 0, f"Local graph watcher is already running (pid {pid}).", "")
                else:
                    with QUIET_LOG.open("a", encoding="utf-8") as log:
                        process = subprocess.Popen([engine_python(), str(Path(__file__).with_name("watch_local.py")), str(root)], cwd=root, env=clean_env(), stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
                    pid_file.write_text(str(process.pid), encoding="utf-8")
                    completed = subprocess.CompletedProcess([], 0, f"Started local graph watcher (pid {process.pid}).", "")
            elif action == "watch-stop":
                if not pid:
                    completed = subprocess.CompletedProcess([], 0, "No local graph watcher is recorded.", "")
                else:
                    try: os.kill(pid, 15)
                    except ProcessLookupError: pass
                    pid_file.unlink(missing_ok=True)
                    completed = subprocess.CompletedProcess([], 0, "Stopped the local graph watcher.", "")
            else:
                running = False
                if pid:
                    try: os.kill(pid, 0); running = True
                    except ProcessLookupError: pid_file.unlink(missing_ok=True)
                completed = subprocess.CompletedProcess([], 0, f"Local graph watcher: {'running' if running else 'stopped'}.", "")
        else:
            command = commands[action]
            if action in ("query", "path", "explain", "affected", "hubs", "tree", "export", "hook-install", "hook-uninstall", "reflect", "merge", "diagnose"):
                command = [*engine_command(), *command]
            env = clean_env()
            completed = subprocess.run(command, cwd=root, env=env, capture_output=True, text=True, check=False)
            if action == "export" and export_format == "svg" and completed.returncode:
                with contextlib.redirect_stdout(io.StringIO()):
                    fallback_code = write_local_svg(root)
                if fallback_code == 0:
                    completed = subprocess.CompletedProcess([], 0, "Created a local SVG using the built-in renderer.", "")
            if action == "export" and export_format == "callflow-html" and completed.returncode == 0:
                sanitize_local_html(output / "callflow.html")
        result = (completed.stdout + ("\n" + completed.stderr if completed.stderr else "")).strip()
        with QUIET_LOG.open("a", encoding="utf-8") as log:
            if result: log.write(f"\n[{action}]\n{result}\n")
        if completed.returncode:
            result = result or f"The local {action} action failed. See graphify-out/token-graph.log for local diagnostics."

        if action == "tree":
            destination = output / "GRAPH_TREE.html"
        elif action in ("recluster", "update"):
            destination = output / "graph.html"
        elif action == "export":
            output_names = {"html": "graph.html", "callflow-html": "callflow.html", "svg": "graph.svg", "graphml": "graph.graphml", "neo4j": "cypher.txt", "falkordb": "cypher.txt"}
            destination = output / output_names.get(export_format or "", "")
            if export_format == "html": destination = output / "graph.html"
            if export_format == "obsidian": destination = output / "obsidian"
            if export_format == "wiki": destination = output / "wiki" / "index.md"
        elif action == "merge":
            destination = output / "merged-graph.json"
        elif action == "diagnose":
            destination = output / "diagnostics.json"
            destination.write_text(result or "{}", encoding="utf-8")
        elif action in ("hook-install", "hook-uninstall", "reflect"):
            destination = output / (Path("reflections/LESSONS.md") if action == "reflect" else Path("local-result.html"))
        else:
            destination = output / "local-result.html"
            safe = html.escape(result or "No result was produced.")
            page = ('<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>Token Graph local result</title><style>body{margin:2rem auto;padding:0 1rem;max-width:1000px;background:#0b1020;color:#e8edf8;font:16px system-ui}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#121a2d;border:1px solid #27324a;border-radius:12px;padding:1.25rem}h1{color:#9f91ff}</style><h1>Token Graph — ' + html.escape(action.title()) + '</h1><pre>' + safe + '</pre>')
            destination.write_text(page, encoding="utf-8")
        if not destination.exists():
            destination = output / "local-result.html"
            destination.write_text('<!doctype html><meta charset="utf-8"><title>Token Graph</title><pre>' + html.escape(result or "The local action completed; see the project graphify-out folder.") + '</pre>', encoding="utf-8")
        if action in {"query", "path", "explain", "affected", "hubs"}:
            # This bounded graph result is the only project-derived payload
            # returned to the hosted assistant for answering the user's question.
            if completed.returncode == 0:
                print(result or "The local graph action returned no matching context.")
            else:
                print(result or f"The local {action} failed; diagnostics remain in graphify-out/token-graph.log.")
            return completed.returncode
        print(f"Token Graph {action} result saved locally: {destination}. Open it manually when needed.")
        return completed.returncode
    finally:
        QUIET_LOG = None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subs = parser.add_subparsers(dest="command", required=True)
    for name in ("build", "update", "build-local", "update-local", "stats", "viewer", "tree", "watch"):
        p = subs.add_parser(name)
        p.add_argument("project")
        if name.endswith("-local"):
            p.add_argument("--model", required=True, help="preinstalled local Ollama model name")
            p.add_argument("--vision", action="store_true", help="enable the engine's opt-in Ollama vision path for a local vision model")
            p.add_argument("--whisper-model-dir", help="already-downloaded local faster-whisper model directory, needed when the project contains audio/video")
    exp = subs.add_parser("export")
    exp.add_argument("project")
    exp.add_argument("format", choices=("html", "callflow-html", "svg", "graphml", "wiki", "obsidian"))
    one = subs.add_parser("launch", help="one invocation: build or refresh locally, then save graph artifacts or return query context")
    one.add_argument("project")
    one.add_argument("--question", help="run a local graph query and return its bounded result")
    one.add_argument("--budget", type=int, default=800, help="local graph query context budget (raise it when the answer says TRUNCATED and lacks what you need)")
    one.add_argument("--no-tests", action="store_true", help="answer from the graph without test files")
    one.add_argument("--include-generated", action="store_true", help="include generated code (gen/, *.pb.go, *.g.cs …) in answers")
    one.add_argument("--context", action="append", default=[], help="edge context filter for a query; repeat as needed")
    one.add_argument("--refresh", action="store_true", help="refresh the graph before generating artifacts")
    one.add_argument("--action", choices=("graph", "query", "path", "explain", "affected", "hubs", "stats", "benchmark", "update", "tree", "recluster", "export", "hook-install", "hook-uninstall", "reflect", "merge", "diagnose", "watch-start", "watch-stop", "watch-status"), default="graph", help="local graph operation to run; results stay on this device")
    one.add_argument("--source", help="source node for --action path")
    one.add_argument("--target", help="target node for --action path")
    one.add_argument("--node", help="node for explain or affected")
    one.add_argument("--relation", action="append", default=[], help="relation filter for affected; repeat as needed")
    one.add_argument("--depth", type=int, default=2, help="affected traversal depth")
    one.add_argument("--top", type=int, default=10, help="number of graph hubs")
    one.add_argument("--format", dest="export_format", help="local export format: html, callflow-html, svg, graphml, wiki, obsidian, neo4j, falkordb")
    one.add_argument("--queries", type=int, default=1, help="projected query count for benchmark estimates")
    one.add_argument("--merge-graph", action="append", dest="merge_graphs", default=[], help="local graph.json to merge; repeat as needed")
    q = subs.add_parser("query"); q.add_argument("project"); q.add_argument("question"); q.add_argument("--dfs", action="store_true"); q.add_argument("--budget", type=int, default=800); q.add_argument("--context", action="append", default=[])
    p = subs.add_parser("path"); p.add_argument("project"); p.add_argument("source"); p.add_argument("target")
    p = subs.add_parser("explain"); p.add_argument("project"); p.add_argument("node")
    p = subs.add_parser("affected"); p.add_argument("project"); p.add_argument("node"); p.add_argument("--relation", action="append", default=[]); p.add_argument("--depth", type=int, default=2)
    p = subs.add_parser("hubs"); p.add_argument("project"); p.add_argument("--top", type=int, default=10)
    p = subs.add_parser("benchmark"); p.add_argument("project"); p.add_argument("--question", action="append", default=[]); p.add_argument("--queries", type=int, default=1); p.add_argument("--json", action="store_true")
    for name in ("query", "path", "explain", "affected", "hubs"):
        command_parser = subs.choices[name]
        command_parser.add_argument("--no-tests", action="store_true", help="exclude test nodes from answers")
        command_parser.add_argument("--include-generated", action="store_true", help="include generated nodes in answers")
    args = parser.parse_args()
    try:
        root = project_path(args.project)
        if args.command == "launch":
            if args.action == "graph":
                return launch_local(root, args.question, args.refresh, budget=args.budget, contexts=args.context, tests=not args.no_tests, generated=args.include_generated)
            if not (root / "graphify-out" / "graph.json").is_file():
                if launch_local(root, None, True, open_page=False): return 1
            return local_feature(root, args.action, question=args.question, source=args.source, target=args.target, node=args.node, relations=args.relation, depth=args.depth, top=args.top, export_format=args.export_format, queries=args.queries, budget=args.budget, contexts=args.context, merge_graphs=args.merge_graphs, tests=not args.no_tests, generated=args.include_generated)
        if args.command == "build":
            if run_engine(root, "extract", str(root), "--code-only", "--no-cluster"): return 1
            return finish_graph(root)
        if args.command == "update":
            if run_engine(root, "update", str(root), "--no-cluster"): return 1
            return finish_graph(root)
        if args.command == "build-local": return build_local(root, args.model, args.vision, args.whisper_model_dir)
        if args.command == "update-local":
            # Extract uses the engine's incremental manifest; media transcripts are refreshed by source hash.
            return build_local(root, args.model, args.vision, args.whisper_model_dir)
        if args.command == "tree": return run_engine(root, "tree", "--graph", str(graph_file(root)))
        if args.command == "watch": return subprocess.run([engine_python(), str(Path(__file__).with_name("watch_local.py")), str(root)], cwd=root, env=clean_env(), check=False).returncode
        if args.command == "export":
            graph = str(graph_file(root))
            if args.format == "html":
                print(write_embedded_viewer(root))
                return 0
            if args.format == "callflow-html":
                return run_engine(root, "export", "callflow-html", graph, "--output", str(root / "graphify-out" / "callflow.html"))
            if args.format == "obsidian":
                return run_engine(root, "export", "obsidian", "--graph", graph, "--dir", str(root / "graphify-out" / "obsidian"))
            if args.format == "svg":
                proc = subprocess.run([*engine_command(), "export", "svg", "--graph", graph], cwd=root, env=clean_env(), capture_output=True, text=True, check=False)
                if proc.returncode == 0:
                    if proc.stdout: print(proc.stdout, end="")
                    return 0
                return write_local_svg(root)
            return run_engine(root, "export", args.format, "--graph", graph)
        if args.command in {"query", "path", "explain", "affected", "hubs"}:
            from node_filters import scoped_graph
            answer_graph = scoped_graph(graph_file(root), tests=not args.no_tests, generated=args.include_generated)
        if args.command == "query":
            if args.budget < 1: raise ValueError("--budget must be positive")
            cmd = ["query", args.question, "--graph", str(answer_graph), "--budget", str(args.budget)]
            if args.dfs: cmd.append("--dfs")
            for context in args.context: cmd.extend(["--context", context])
            return run_engine(root, *cmd)
        if args.command == "path": return run_engine(root, "path", args.source, args.target, "--graph", str(answer_graph))
        if args.command == "explain": return run_engine(root, "explain", args.node, "--graph", str(answer_graph))
        if args.command == "affected":
            if args.depth < 1: raise ValueError("--depth must be positive")
            cmd = ["affected", args.node, "--graph", str(answer_graph), "--depth", str(args.depth)]
            for relation in args.relation: cmd.extend(["--relation", relation])
            return run_engine(root, *cmd)
        if args.command == "hubs":
            if args.top < 1: raise ValueError("--top must be positive")
            return run_engine(root, "god-nodes", "--graph", str(answer_graph), "--top", str(args.top))
        if args.command == "stats":
            graph = json.loads(graph_file(root).read_text(encoding="utf-8"))
            nodes, links = graph.get("nodes", []), graph.get("links", graph.get("edges", []))
            communities = {str(n.get("community")) for n in nodes if n.get("community") is not None}
            files = {n.get("source_file") for n in nodes if n.get("source_file")}
            print(f"Graph: {len(nodes):,} nodes, {len(links):,} links, {len(communities):,} communities, {len(files):,} represented files")
            return 0
        if args.command == "benchmark":
            if args.queries < 1: raise ValueError("--queries must be positive")
            token_script = Path(__file__).with_name("token_savings.py")
            cmd = [sys.executable, str(token_script), str(root), "--queries", str(args.queries)]
            for question in args.question: cmd.extend(["--question", question])
            if args.json: cmd.append("--json")
            return subprocess.run(cmd, cwd=root, env=clean_env(), check=False).returncode
        if args.command == "viewer":
            graph_file(root)
            destination = root / "graphify-out" / "graph-viewer.html"
            source = Path(__file__).resolve().parents[1] / "assets" / "graph-viewer.html"
            shutil.copyfile(source, destination)
            print(f"Created self-contained local viewer: {destination}")
            print("Open it and choose graphify-out/graph.json using its Load another graph control.")
            return 0
    except (RuntimeError, ValueError, OSError, json.JSONDecodeError) as error:
        print(f"token-graph: {error}", file=sys.stderr)
        return 2
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
