#!/usr/bin/env python3
"""Compute the Project Explorer dashboard summary for one project graph.

Everything here is derived from files Token Graph already wrote for the project
(graph.json, manifest.json, cache/, last-build.json) and from the bundled engine's own
analysis functions. Nothing is estimated or invented: a figure that cannot be derived
is reported as unavailable, with the reason. Runs under the offline policy. Usage:
    dashboard_engine.py <project>
Prints one JSON object.
"""
from __future__ import annotations
from collections import Counter, defaultdict
import json
import os
from pathlib import Path
import sys

SCRIPTS = Path(__file__).resolve().parent
sys.path[:0] = [str(SCRIPTS), str(SCRIPTS.parent / "vendor")]
from offline_policy import install  # noqa: E402
install(False)
from graphify import analyze  # noqa: E402
from graphify import serve  # noqa: E402
from graphify.extract import _DISPATCH  # noqa: E402  (the engine's extension -> extractor table)
from node_filters import is_generated, is_test  # noqa: E402

# Display names for engine extractor ids. Cosmetic only: an id missing here is shown
# as-is, so a newly supported language still appears. (Phase 1 moves this into
# per-language plugin manifests.)
LANGUAGE_NAMES = {
    "python": "Python", "js": "JavaScript / TypeScript", "csharp": "C#", "java": "Java", "go": "Go",
    "rust": "Rust", "c": "C", "cpp": "C++", "ruby": "Ruby", "kotlin": "Kotlin", "scala": "Scala",
    "php": "PHP", "swift": "Swift", "lua": "Lua", "zig": "Zig", "powershell": "PowerShell",
    "powershell_manifest": "PowerShell manifest", "elixir": "Elixir", "erlang": "Erlang", "objc": "Objective-C",
    "julia": "Julia", "fortran": "Fortran", "vue": "Vue", "svelte": "Svelte", "astro": "Astro",
    "dart": "Dart", "ocaml": "OCaml", "commonlisp": "Common Lisp", "verilog": "Verilog", "sql": "SQL",
    "pascal": "Pascal", "bash": "Shell", "terraform": "Terraform / HCL", "vbnet": "VB.NET",
    "groovy": "Groovy", "r": "R", "solidity": "Solidity", "razor": "Razor", "xaml": "XAML",
    "robot": "Robot Framework", "apex": "Apex", "cobol": "COBOL", "dm": "DM",
    "markdown": "Markdown", "json": "JSON", "sln": "Solution", "slnx": "Solution", "csproj": "Project file",
}
# Extractors that index configuration or documentation rather than program code.
NON_CODE = {"markdown", "json", "sln", "slnx", "csproj", "lazarus_package", "delphi_form", "lazarus_form", "powershell_manifest"}
# Relations that describe structure (what is inside what), not a dependency between parts.
STRUCTURAL = {"contains", "method", "defines", "rationale_for", "cites"}
IMPORT_RELATIONS = {"imports_from", "re_exports"}  # what analyze.find_import_cycles follows


def language_of(file: str) -> str:
    suffix = Path(file).suffix
    extractor = _DISPATCH.get(suffix) or _DISPATCH.get(suffix.lower())
    if extractor is None:
        return "other"
    name = getattr(extractor, "__name__", "")
    return name[len("extract_"):] if name.startswith("extract_") else name or "other"


def module_depth(dirs: list[tuple[str, ...]]) -> int:
    """Shallowest folder depth that splits the files into at least three modules."""
    depth = 1
    while depth < 4:
        if len({d[:depth] for d in dirs}) >= 3:
            break
        if len({d[:depth + 1] for d in dirs}) <= len({d[:depth] for d in dirs}):
            break
        depth += 1
    return depth


def count_files(folder: Path) -> int:
    """Cached extraction results under folder (the engine nests them in version subfolders)."""
    total = 0
    for _dirpath, _dirnames, filenames in os.walk(folder):
        total += sum(1 for name in filenames if name.endswith(".json"))
    return total


def summarize(project: Path) -> dict:
    output = project / "graphify-out"
    graph_file = output / "graph.json"
    raw = json.loads(graph_file.read_text(encoding="utf-8"))
    nodes = [n for n in raw.get("nodes", []) if isinstance(n, dict) and "id" in n]
    edges = [e for e in raw.get("links", raw.get("edges", [])) if isinstance(e, dict)]
    by_id = {str(n["id"]): n for n in nodes}
    file_of = {nid: str(n.get("source_file") or "").replace("\\", "/") for nid, n in by_id.items()}

    def ends(edge: dict) -> tuple[str, str]:
        return str(edge.get("_src", edge.get("source"))), str(edge.get("_tgt", edge.get("target")))

    # --- languages and files --------------------------------------------------------
    # Folder nodes carry their directory as source_file; only real files count.
    files = sorted({f for f in file_of.values() if f and not (project / f).is_dir()})
    file_language = {f: language_of(f) for f in files}
    language_files: Counter = Counter(file_language.values())
    language_nodes: Counter = Counter(file_language[f] for f in file_of.values() if f in file_language)
    extensions: dict[str, set] = defaultdict(set)
    for f, lang in file_language.items():
        extensions[lang].add(Path(f).suffix.lower() or Path(f).name)
    languages = [{
        "id": lang, "name": LANGUAGE_NAMES.get(lang, lang), "files": count, "nodes": language_nodes[lang],
        "extensions": sorted(extensions[lang]), "code": lang not in NON_CODE and lang != "other",
    } for lang, count in language_files.most_common()]
    code_files = [f for f in files if file_language[f] not in NON_CODE and file_language[f] != "other"]

    # --- relationships and dependencies ---------------------------------------------
    relations = Counter(str(e.get("relation") or "related") for e in edges)
    confidence = Counter(str(e.get("confidence") or "EXTRACTED") for e in edges)
    degree: Counter = Counter()
    incoming: Counter = Counter()
    for edge in edges:
        source, target = ends(edge)
        degree[source] += 1
        degree[target] += 1
        incoming[target] += 1
    external = [n for nid, n in by_id.items() if not file_of[nid] and n.get("file_type") != "document"]
    external_uses: Counter = Counter()
    for n in external:
        external_uses[str(n.get("label", n["id"]))] += incoming[str(n["id"])]
    dependency_edges = sum(count for relation, count in relations.items() if relation not in STRUCTURAL)

    # --- architecture: folder modules and the dependencies between them -------------
    dirs = [tuple(Path(f).parent.as_posix().split("/")) if Path(f).parent.as_posix() != "." else ("(root)",) for f in code_files]
    depth = module_depth(dirs) if dirs else 1
    module_of = {}
    for f in code_files:
        parent = Path(f).parent.as_posix()
        module_of[f] = "/".join(parent.split("/")[:depth]) if parent != "." else "(root)"
    module_files: Counter = Counter(module_of.values())
    module_nodes: Counter = Counter(module_of[file_of[nid]] for nid in by_id if file_of[nid] in module_of)
    module_links: Counter = Counter()
    for edge in edges:
        if str(edge.get("relation")) in STRUCTURAL:
            continue
        source, target = ends(edge)
        a, b = module_of.get(file_of.get(source, "")), module_of.get(file_of.get(target, ""))
        if a and b and a != b:
            module_links[(a, b)] += 1

    # --- code intelligence: the engine's own analyses --------------------------------
    G = serve._load_graph(str(graph_file))
    hotspots = [{"label": h["label"], "degree": h["degree"], "file": file_of.get(str(h["id"]), "")}
                for h in analyze.god_nodes(G, top_n=6)]
    import_edges = sum(relations[r] for r in IMPORT_RELATIONS)
    cycles = analyze.find_import_cycles(G, top_n=10) if import_edges else []
    connected = {nid for nid, d in degree.items() if d}
    unconnected = sum(1 for nid, n in by_id.items()
                      if file_of[nid] and nid not in connected and n.get("file_type", "code") == "code")
    file_set = [{"source_file": f} for f in code_files]

    # --- knowledge index: what the engine has indexed on disk ------------------------
    manifest_file = output / "manifest.json"
    manifest = {}
    if manifest_file.is_file():
        try:
            manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
        except ValueError:
            manifest = {}
    seen = [entry.get("seen") for entry in manifest.values() if isinstance(entry, dict) and isinstance(entry.get("seen"), (int, float))]

    return {
        "graph": {
            "built": graph_file.stat().st_mtime,
            "nodes": len(nodes), "edges": len(edges), "files": len(files), "code_files": len(code_files),
            "communities": len({n.get("community") for n in nodes if n.get("community") is not None}),
            "relations": dict(relations.most_common()),
            "inferred": confidence.get("INFERRED", 0), "extracted": confidence.get("EXTRACTED", 0),
            "ai_nodes": sum(1 for n in nodes if n.get("_origin") == "semantic"),
            "commit": raw.get("built_at_commit"),
        },
        "languages": languages,
        "dependencies": {
            "edges": dependency_edges,
            "external_symbols": len(external_uses),
            "top_external": [{"label": label, "uses": uses} for label, uses in external_uses.most_common(5)],
        },
        "architecture": {
            "depth": depth,
            "modules": [{"name": name, "files": count, "nodes": module_nodes[name]} for name, count in module_files.most_common(8)],
            "module_count": len(module_files),
            "links": [{"from": a, "to": b, "count": count} for (a, b), count in module_links.most_common(6)],
        },
        "quality": {
            "hotspots": hotspots,
            "cycles": {"supported": bool(import_edges), "import_edges": import_edges, "count": len(cycles),
                       "items": [{"files": c["cycle"], "length": c["length"]} for c in cycles[:5]]},
            "unconnected_symbols": unconnected,
            "test_files": sum(1 for f in file_set if is_test(f)),
            "generated_files": sum(1 for f in file_set if is_generated(f)),
            "code_files": len(code_files),
        },
        "index": {
            "manifest_files": len(manifest),
            "last_indexed": max(seen) if seen else None,
            "ast_cache_entries": count_files(output / "cache" / "ast"),
            "semantic_cache_entries": count_files(output / "cache" / "semantic"),
        },
    }


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__, file=sys.stderr)
        return 2
    project = Path(sys.argv[1]).resolve()
    if not (project / "graphify-out" / "graph.json").is_file():
        print("This project has no graph yet; build it first.", file=sys.stderr)
        return 1
    sys.stdout.write(json.dumps(summarize(project), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
