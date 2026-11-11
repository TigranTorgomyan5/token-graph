"""Code-health reports computed from the graph alone: dead code, reuse finder, hotspots.

  review    what the current git diff touches: callers, missing tests, hotspots (review.py)
  dead      symbols nobody calls/references/imports (and, when it can tell, dead chains)
  similar   existing symbols that already do what you are about to write
  hotspots  big, heavily connected, frequently changed symbols and files

Every report reads graph.json (a scoped copy without generated code, externals, docs and
minified bundles) and prints a bounded plain-text answer, like the other launcher actions.
Language-specific knowledge lives in the extractors; only name/path conventions live here.
"""
from __future__ import annotations
import json
import math
import re
import subprocess
from collections import Counter, defaultdict
from pathlib import Path

from node_filters import is_test, is_generated, is_external, is_doc

# Relations that only say "this contains that" or "this documents that"; they are not use.
STRUCTURAL = {"contains", "method", "rationale_for", "defines"}
VENDOR_DIR = re.compile(r"(^|/)(vendor|third_party|node_modules|site-packages)(/|$)", re.I)
ENTRY_NAME = re.compile(r"^(main|run|setUp|tearDown|setUpClass|tearDownClass|setUpModule|tearDownModule|do_[A-Z]+|__\w+__)$")
TEXT_EXT = {".html", ".htm", ".bat", ".cmd", ".sh", ".ps1", ".json", ".yml", ".yaml", ".toml", ".cfg"}
SKIP_DIRS = {".git", "node_modules", "graphify-out", ".runtime", "vendor", "__pycache__", ".venv", "venv",
             "dist", "build", "bin", "obj", ".idea", ".vs"}
WIDE_USE = 3                       # no graph caller but the name occurs this often elsewhere: treat as alive
MAX_EXTRA_BYTES = 100_000          # non-graph text files bigger than this are build outputs, not usage
STOPWORDS = {"the", "a", "an", "of", "to", "for", "and", "or", "in", "on", "is", "are", "with", "that", "this",
             "from", "how", "do", "i", "we", "it", "be", "by", "as", "at", "my", "need", "want", "write",
             "function", "code", "method", "class", "make", "add", "create", "new", "something", "which",
             "if", "than", "then", "when", "not", "has", "have", "does", "can", "should", "so"}
LOC = re.compile(r"L(\d+)(?:-(\d+))?")


# ---- graph helpers ---------------------------------------------------------------------------

def load(graph_file: Path) -> dict:
    return json.loads(Path(graph_file).read_text(encoding="utf-8"))


def _links(data: dict) -> list[dict]:
    return data.get("links", data.get("edges", [])) or []


def file_of(node: dict) -> str:
    return str(node.get("source_file") or "").replace("\\", "/")


def span(node: dict) -> tuple[int, int]:
    """(first line, last line) of a symbol; (0, 0) when the graph has no location."""
    match = LOC.fullmatch(str(node.get("source_location") or ""))
    if not match:
        return 0, 0
    start = int(match.group(1))
    end = node.get("end_line")
    if not isinstance(end, int):
        end = int(match.group(2)) if match.group(2) else start
    return start, max(end, start)


def lines_of(node: dict) -> int:
    start, end = span(node)
    return end - start + 1 if start else 0


def where(node: dict) -> str:
    start, end = span(node)
    return f"{file_of(node)}:L{start}" + (f"-{end}" if end > start else "")


def bare_name(label: str) -> str:
    return re.sub(r"\(.*\)\s*$", "", label).lstrip(".").strip()


def is_file_node(node: dict) -> bool:
    file = file_of(node)
    return bool(file) and str(node.get("label") or "") == file.rsplit("/", 1)[-1]


def kind_of(node: dict) -> str:
    label = str(node.get("label") or "")
    if label.startswith("."):
        return "method"
    if node.get("_callable_class"):
        return "class"
    if label.endswith(")") or node.get("_callable"):
        return "function"
    return "symbol"


def symbols(data: dict, prefix: str = "") -> dict[str, dict]:
    """Code symbols of the project: not files, docstrings, externals, docs or vendored folders."""
    result: dict[str, dict] = {}
    for node in data.get("nodes", []):
        file = file_of(node)
        if not file or node.get("file_type") != "code" or is_file_node(node) or is_external(node) or is_doc(node):
            continue
        if not span(node)[0]:
            continue
        if prefix and not file.startswith(prefix):
            continue
        result[node["id"]] = node
    return result


def drop_vendor_dirs(nodes: dict[str, dict], prefix: str) -> dict[str, dict]:
    """Code the project does not own is never 'dead' for it, unless the caller asked for that folder."""
    if prefix and VENDOR_DIR.search(prefix):
        return nodes
    return {k: n for k, n in nodes.items() if not VENDOR_DIR.search(file_of(n))}


def docstrings(data: dict) -> dict[str, str]:
    """symbol id -> its docstring (rationale node text)."""
    texts = {n["id"]: str(n.get("label") or "") for n in data.get("nodes", []) if n.get("file_type") == "rationale"}
    result: dict[str, str] = {}
    for link in _links(data):
        if link.get("relation") == "rationale_for" and link.get("source") in texts:
            result[link["target"]] = texts[link["source"]]
    return result


# ---- text usage (names used where the parser cannot see: dynamic calls, html handlers) -------

def identifier_counts(root: Path, graph_files: set[str]) -> Counter:
    """How often each identifier occurs in the project's own text. Graph files are always read;
    other small text files (html, scripts, configs) are read too, since onclick="foo()" or a
    batch file is a real use the parser never sees. Large non-graph files are build outputs."""
    counts: Counter = Counter()
    seen: set[str] = set()
    ident = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")

    def read(path: Path) -> None:
        try:
            counts.update(ident.findall(path.read_text(encoding="utf-8", errors="ignore")))
        except OSError:
            pass

    for rel in graph_files:
        seen.add(rel)
        read(root / rel)
    stack = [root]
    while stack:
        folder = stack.pop()
        try:
            entries = list(folder.iterdir())
        except OSError:
            continue
        for entry in entries:
            if entry.is_dir():
                if entry.name not in SKIP_DIRS:
                    stack.append(entry)
            elif entry.suffix.lower() in TEXT_EXT:
                rel = entry.relative_to(root).as_posix()
                try:
                    small = entry.stat().st_size <= MAX_EXTRA_BYTES
                except OSError:
                    continue
                if rel not in seen and small:
                    read(entry)
    return counts


# ---- 1. dead code ------------------------------------------------------------------------------

# A route/router/controller file may use a framework the graph cannot see (decorators, attributes,
# registries). Such roots are reported as uncertain, never as safe to delete, and never seed chains.
ROUTE_FILE = re.compile(r'(^|/)(routes?|routers?|controllers?|handlers?|endpoints?|resources?|views?|apis?|urls|web)(/|\.|$)', re.I)


def framework_entry_ids(root: Path, candidates: dict[str, dict]) -> set[str]:
    """Symbols that a framework route in the same file dispatches to (Flask, Express, ASP.NET, Go mux …).

    Language patterns stay in agent_frameworks; this only maps its route line back to a symbol name."""
    import agent_frameworks
    names_by_file: dict[str, set[str]] = {}
    entries: set[str] = set()
    for key, node in candidates.items():
        file = file_of(node)
        if not file:
            continue
        if file not in names_by_file:
            names: set[str] = set()
            path = root / file
            try:
                if path.is_file() and path.stat().st_size <= 1_000_000:
                    lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
                    for hint in agent_frameworks.file_routes(file, lines):
                        match = re.search(r":(\d+)$", hint)
                        if match:
                            name = agent_frameworks.handler_name(file, lines, int(match.group(1)))
                            if name:
                                names.add(name)
            except OSError:
                pass
            names_by_file[file] = names
        if bare_name(str(node.get("label") or "")) in names_by_file[file]:
            entries.add(key)
    return entries


def find_dead(data: dict, root: Path, prefix: str = "", ignore: list[str] | None = None) -> dict:
    """Classify unused symbols. Returns {"checked": n, "items": [...]} sorted by lines saved."""
    nodes = drop_vendor_dirs(symbols(data, prefix), prefix)
    everything = {n["id"]: n for n in data.get("nodes", [])}
    ignores = [re.compile(p) for p in ignore or []]
    incoming: dict[str, set[str]] = defaultdict(set)
    for link in _links(data):
        if link.get("relation") in STRUCTURAL or link.get("source") == link.get("target"):
            continue
        incoming[link["target"]].add(link["source"])

    by_name: Counter = Counter(bare_name(str(n.get("label") or "")) for n in symbols(data).values())
    # A class lives as long as one of its members is used from outside the class.
    members: dict[str, set[str]] = defaultdict(set)
    for link in _links(data):
        if link.get("relation") in ("method", "contains") and link.get("source") in nodes:
            members[link["source"]].add(link["target"])

    def candidate(node: dict) -> bool:
        name = bare_name(str(node.get("label") or ""))
        if kind_of(node) == "symbol" or is_test(node) or is_generated(node):
            return False
        if ENTRY_NAME.match(name) or name.startswith("test") or any(p.search(name) or p.search(file_of(node)) for p in ignores):
            return False
        return True

    candidates = {k for k, n in nodes.items() if candidate(n)}

    def used_from(target: str) -> set[str]:
        sources = set(incoming.get(target, ()))
        for member in members.get(target, ()):
            sources |= {s for s in incoming.get(member, ()) if s != target and s not in members.get(target, ())}
        return sources

    graph_files = {file_of(n) for n in data.get("nodes", []) if file_of(n)}
    counts = identifier_counts(root, graph_files)
    edge_count: Counter = Counter()
    for link in _links(data):
        if link.get("relation") not in STRUCTURAL and link.get("source") != link.get("target"):
            edge_count[link["target"]] += 1

    def textual(key: str) -> int:
        """Occurrences of the name beyond its own definitions: calls the parser could not see."""
        name = bare_name(str(nodes[key].get("label") or ""))
        return counts.get(name, 0) - by_name.get(name, 1)

    # Roots: no caller in the graph. A root whose name also appears elsewhere in the text is only
    # "maybe" dead (top-level script code, html handlers, dynamic calls) and must not condemn
    # the code it calls, so dead chains grow from the certain roots only.
    roots = {k for k in candidates if not used_from(k)}
    entries = framework_entry_ids(root, {k: nodes[k] for k in roots})
    roots -= entries
    # A root in a router/controller-style file can be an entry point registered elsewhere; keep it
    # visible but medium, and do not let it seed a dead chain.
    suspected = {k for k in roots if ROUTE_FILE.search(file_of(nodes[k]))}
    certain = {k for k in roots if textual(k) <= 0} - suspected
    dead = set(certain)
    chain: set[str] = set()
    changed = True
    while changed:
        changed = False
        for key in candidates - roots - dead:
            sources = used_from(key)
            # Its name may occur once per call site inside the dead callers; more than that is a use we cannot see.
            if sources and all(s in dead for s in sources) and textual(key) <= edge_count[key]:
                dead.add(key)
                chain.add(key)
                changed = True
    dead |= roots

    items = []
    widely_named = 0
    for key in dead:
        node = nodes[key]
        name = bare_name(str(node.get("label") or ""))
        kind = kind_of(node)
        if key not in certain and key not in chain and textual(key) >= WIDE_USE:
            widely_named += 1      # named all over the project: a graph blind spot, not dead code
            continue
        if key in chain:
            confidence, why = "high", "only called by other unused code"
        elif key in suspected:
            confidence, why = "medium", "router/controller-style file: could be a framework entry point"
        elif key not in certain:
            confidence, why = "medium", f"no graph references, but the name appears {textual(key)}x elsewhere (dynamic call?)"
        else:
            confidence, why = "high", "no callers, name appears nowhere else"
        if kind == "method" and confidence == "high" and key not in chain:
            confidence, why = "medium", "unused method (an override or interface member?)"
        items.append({"id": key, "node": node, "name": name, "kind": kind, "lines": lines_of(node),
                      "confidence": confidence, "why": why, "chain": key in chain, "tests_only": False})
    # Used only by tests: still dead for production, reported separately.
    for key in candidates - dead:
        sources = used_from(key)
        if sources and all(is_test(everything.get(s, {})) for s in sources):
            node = nodes[key]
            items.append({"id": key, "node": node, "name": bare_name(str(node.get("label") or "")), "kind": kind_of(node),
                          "lines": lines_of(node), "confidence": "tests", "why": "used only by tests",
                          "chain": False, "tests_only": True})
    items.sort(key=lambda i: (-i["lines"], file_of(i["node"]), span(i["node"])[0]))
    return {"checked": len(candidates), "items": items, "widely_named": widely_named}


def format_dead(result: dict, top: int = 30) -> str:
    items = result["items"]
    if not items:
        return f"Dead code: none found among {result['checked']} functions and classes."
    order = [("high", "HIGH — safe to delete (verify once, then remove)"),
             ("medium", "MEDIUM — probably unused; check dynamic use first"),
             ("tests", "TESTS ONLY — nothing in production code uses these")]
    total = sum(i["lines"] for i in items if i["confidence"] != "tests")
    out = [f"Dead code: {sum(1 for i in items if i['confidence'] != 'tests')} unused of {result['checked']} functions/classes "
           f"(~{total} lines); {sum(1 for i in items if i['confidence'] == 'tests')} used only by tests."]
    shown = 0
    for confidence, title in order:
        group = [i for i in items if i["confidence"] == confidence]
        if not group:
            continue
        out.append(f"\n{title} — {len(group)}")
        per_file: dict[str, list[dict]] = defaultdict(list)
        for item in group:
            per_file[file_of(item["node"])].append(item)
        for file, rows in sorted(per_file.items(), key=lambda kv: -sum(r["lines"] for r in kv[1])):
            if shown >= top:
                break
            out.append(f"  {file}")
            for row in sorted(rows, key=lambda r: span(r["node"])[0]):
                if shown >= top:
                    break
                start, end = span(row["node"])
                out.append(f"    L{start}-{end}  {row['name']}  [{row['kind']}, {row['lines']} lines] {row['why']}")
                shown += 1
    hidden = len(items) - shown
    if hidden > 0:
        out.append(f"\n(+{hidden} more; raise --top, or narrow with --path <folder>)")
    out.append("\nThe graph sees static calls only: reflection, decorators/routes, and external callers need a check "
               "before deleting. Use `affected --node <name>` to confirm.")
    return "\n".join(out)


# ---- 2. reuse finder ---------------------------------------------------------------------------

def tokens(text: str) -> list[str]:
    """snake_case / camelCase / paths -> lowercase stems, so 'validateWidgetToken' matches 'validate widget tokens'."""
    spaced = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", text)
    spaced = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1 \2", spaced)
    words = re.findall(r"[a-zA-Z]{2,}", spaced.lower())
    return [stem(w) for w in words if w not in STOPWORDS]


def stem(word: str) -> str:
    for suffix in ("ing", "ed", "es", "s"):
        if word.endswith(suffix) and len(word) - len(suffix) >= 3:
            return word[: -len(suffix)]
    return word


def find_similar(data: dict, query: str, top: int = 8, prefix: str = "", exclude_id: str | None = None) -> list[dict]:
    """Existing symbols that match the words of what you plan to write (name > docstring > path)."""
    nodes = {k: n for k, n in drop_vendor_dirs(symbols(data, prefix), prefix).items()
             if not is_test(n) and not is_generated(n) and kind_of(n) != "symbol" and k != exclude_id}
    query_terms = list(dict.fromkeys(tokens(query)))
    if not query_terms or not nodes:
        return []
    docs = docstrings(data)
    fan_in: Counter = Counter()
    for link in _links(data):
        if link.get("relation") not in STRUCTURAL and link.get("source") != link.get("target"):
            fan_in[link["target"]] += 1
    profile = {}
    for key, node in nodes.items():
        profile[key] = (set(tokens(bare_name(str(node.get("label") or "")))),
                        set(tokens(docs.get(key, "")[:400])),
                        set(tokens(file_of(node).replace("/", " "))))
    df: Counter = Counter()
    for name_t, doc_t, path_t in profile.values():
        df.update(name_t | doc_t | path_t)
    total = len(profile)
    scored = []
    for key, (name_t, doc_t, path_t) in profile.items():
        score, matched = 0.0, []
        for term in query_terms:
            weight = 2.0 if term in name_t else 1.0 if term in doc_t else 0.3 if term in path_t else 0.0
            if weight:
                score += weight * (math.log((1 + total) / (1 + df[term])) + 1)
                matched.append(term)
        # A symbol must carry the idea in its name or docstring, not just live in a similarly named folder.
        if not any(t in name_t or t in doc_t for t in matched):
            continue
        coverage = len(matched) / len(query_terms)
        score = score * (0.5 + coverage) / math.sqrt(1 + len(name_t) / 6) + 0.15 * math.log(1 + fan_in[key])
        scored.append((score, key, matched))
    scored.sort(key=lambda row: -row[0])
    if not scored:
        return []
    cutoff = scored[0][0] * 0.4
    return [{"node": nodes[key], "score": score, "matched": matched, "used_by": fan_in[key], "doc": docs.get(key, "")}
            for score, key, matched in scored[:top] if score >= cutoff]


def format_similar(query: str, rows: list[dict]) -> str:
    if not rows:
        return (f'Reuse finder: nothing in the project looks like "{query}". '
                "Safe to write it new (try other words if the idea has another name).")
    out = [f'Reuse finder: {len(rows)} existing symbol(s) close to "{query}" — read these before writing new code:']
    for row in rows:
        node = row["node"]
        doc = re.sub(r"\s+", " ", row["doc"]).strip()
        doc = (doc[:100] + "…") if len(doc) > 100 else doc
        used = f"used by {row['used_by']}" if row["used_by"] else "unused so far"
        out.append(f"  {where(node)}  {bare_name(str(node.get('label') or ''))}  [{kind_of(node)}, {used}; matches: {', '.join(row['matched'])}]")
        if doc:
            out.append(f"      {doc}")
    out.append("\nMatching is by name/docstring words, not behaviour: open the best hit and check its signature.")
    return "\n".join(out)


# ---- 3. hotspots -------------------------------------------------------------------------------

def git_churn(root: Path, days: int) -> tuple[Counter, str]:
    """Commits touching each file (paths relative to root) in the last `days` days."""
    try:
        proc = subprocess.run(["git", "-C", str(root), "log", f"--since={days}.days", "--relative", "--name-only",
                               "--pretty=format:", "--no-renames"],
                              capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60, check=False)
    except (OSError, subprocess.SubprocessError):
        return Counter(), "git not available: churn ignored"
    if proc.returncode != 0:
        return Counter(), "not a git repository: churn ignored"
    return Counter(line.strip().replace("\\", "/") for line in proc.stdout.splitlines() if line.strip()), ""


def find_hotspots(data: dict, root: Path, top: int = 10, days: int = 180, prefix: str = "") -> dict:
    """Rank symbols and files by size x connectivity x recent change. Higher = riskier to touch."""
    nodes = {k: n for k, n in drop_vendor_dirs(symbols(data, prefix), prefix).items()
             if not is_test(n) and not is_generated(n) and kind_of(n) != "symbol"}
    churn, note = git_churn(root, days)
    fan_in: Counter = Counter()
    fan_out: Counter = Counter()
    callers: dict[str, set[str]] = defaultdict(set)
    for link in _links(data):
        if link.get("relation") in STRUCTURAL or link.get("source") == link.get("target"):
            continue
        fan_in[link["target"]] += 1
        fan_out[link["source"]] += 1
        callers[link["target"]].add(link["source"])
    file_of_id = {n["id"]: file_of(n) for n in data.get("nodes", []) if file_of(n)}
    rows = []
    for key, node in nodes.items():
        lines = lines_of(node)
        if lines < 10:
            continue
        file = file_of(node)
        outside = len({s for s in callers.get(key, ()) if file_of_id.get(s) != file})
        degree = fan_in[key] + fan_out[key]
        changes = churn.get(file, 0)
        score = lines * math.sqrt(1 + degree) * (1 + math.log(1 + changes))
        rows.append({"node": node, "lines": lines, "fan_in": fan_in[key], "fan_out": fan_out[key],
                     "outside_files": outside, "churn": changes, "score": score})
    rows.sort(key=lambda r: -r["score"])
    per_file: dict[str, dict] = {}
    max_end: dict[str, int] = {}
    for node in nodes.values():
        file = file_of(node)
        max_end[file] = max(max_end.get(file, 0), span(node)[1])
        entry = per_file.setdefault(file, {"file": file, "symbols": 0, "lines": 0, "degree": 0, "churn": churn.get(file, 0)})
        entry["symbols"] += 1
        entry["degree"] += fan_in[node["id"]] + fan_out[node["id"]]
    for entry in per_file.values():
        # File length from the farthest symbol end, so one-line files do not rank on size.
        entry["lines"] = max_end.get(entry["file"], 0)
        entry["score"] = entry["lines"] * math.sqrt(1 + entry["degree"] / max(entry["symbols"], 1)) * (1 + math.log(1 + entry["churn"]))
    files = sorted(per_file.values(), key=lambda e: -e["score"])
    return {"symbols": rows[:top], "files": files[:top], "days": days, "note": note}


def format_hotspots(result: dict) -> str:
    out = [f"Hotspots (size x connections x commits in the last {result['days']} days; refactor or test these first)."]
    if result["note"]:
        out.append(f"[{result['note']}]")
    out.append("\nSymbols")
    for row in result["symbols"]:
        node = row["node"]
        out.append(f"  {where(node)}  {bare_name(str(node.get('label') or ''))}  "
                   f"[{row['lines']} lines, in {row['fan_in']} / out {row['fan_out']}, {row['outside_files']} caller file(s), "
                   f"file changed {row['churn']}x]")
    out.append("\nFiles")
    for row in result["files"]:
        out.append(f"  {row['file']}  [{row['lines']} lines, {row['symbols']} symbols, {row['degree']} connections, changed {row['churn']}x]")
    out.append("\nBefore editing a hotspot run `affected --node <name>`: it shows what breaks.")
    return "\n".join(out)


# ---- entry point used by token_graph.py -------------------------------------------------------

def run(action: str, graph_file: Path, root: Path, *, question: str | None = None, node: str | None = None,
        top: int | None = None, prefix: str = "", days: int = 180, ignore: list[str] | None = None,
        ref: str = "HEAD") -> str:
    data = load(graph_file)
    prefix = prefix.replace("\\", "/").removeprefix("./") if prefix else ""
    if action == "dead":
        return format_dead(find_dead(data, root, prefix, ignore), top or 30)
    if action == "similar":
        exclude = None
        query = question or ""
        if node and not query:
            target = next((n for n in symbols(data).values() if bare_name(str(n.get("label") or "")).lower() == node.lower().rstrip("()")), None)
            query = bare_name(str(target.get("label") or node)) if target else node
            exclude = target["id"] if target else None
        if not query:
            raise ValueError("--question (what you plan to write) or --node is required for similar")
        return format_similar(query, find_similar(data, query, top or 8, prefix, exclude))
    if action == "hotspots":
        return format_hotspots(find_hotspots(data, root, top or 10, days, prefix))
    if action == "review":
        import review
        return review.format_review(review.build_review(data, root, ref or "HEAD", prefix), top or 15)
    raise ValueError(f"unknown code-health action: {action}")
