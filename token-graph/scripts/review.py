"""`launch --action review`: what does the current git diff touch, and what should be checked first?

Maps the changed lines of `git diff` onto graph symbols, then for each changed symbol reports
who calls it (and from which files), whether any test calls it, whether it is a hotspot,
whether the edit left it unused, and whether a new function looks like one that already exists.
Everything but the diff itself comes from the graph and code_health.py.
"""
from __future__ import annotations
import math
import re
import subprocess
from collections import Counter, defaultdict
from pathlib import Path

import code_health as ch
from node_filters import is_test, is_generated

HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")
HOTSPOT_RANK = 15          # a changed symbol among the N top hotspots is flagged
NEW_SHARE = 0.8            # a symbol is "new" when this share of its lines changed
WHOLE_FILE = 10 ** 9


def _git(root: Path, *args: str) -> tuple[int, str]:
    try:
        proc = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=60, check=False)
    except (OSError, subprocess.SubprocessError) as error:
        return 1, str(error)
    return proc.returncode, proc.stdout if proc.returncode == 0 else proc.stderr.strip()


def parse_diff(text: str) -> tuple[dict[str, list[tuple[int, int]]], list[str]]:
    """Unified diff (-U0) -> ({path: [(first, last) new-side changed lines]}, [deleted paths])."""
    files: dict[str, list[tuple[int, int]]] = defaultdict(list)
    deleted: list[str] = []
    old = current = None
    for line in text.splitlines():
        if line.startswith("--- "):
            old = line[4:].strip()
            old = old[2:] if old.startswith("a/") else None
        elif line.startswith("+++ "):
            new = line[4:].strip()
            if new == "/dev/null":
                current = None
                if old:
                    deleted.append(old)
            else:
                current = new[2:] if new.startswith("b/") else new
        elif current and (match := HUNK.match(line)):
            start = int(match.group(1))
            count = int(match.group(2)) if match.group(2) is not None else 1
            # count 0 is a pure deletion: the neighbouring line is where code was removed.
            files[current].append((max(start, 1), max(start, 1) + max(count, 1) - 1))
    return dict(files), deleted


def git_changes(root: Path, ref: str = "HEAD") -> dict:
    """Working tree against `ref` (default HEAD), plus untracked files as wholly new.
    Paths are relative to `root`. {"files", "deleted", "error"}"""
    if not ref or ref.startswith("-") or any(char in ref for char in "\n\r"):
        return {"files": {}, "deleted": [], "error": "Invalid git ref."}
    code, out = _git(root, "diff", "-U0", "--no-color", "--no-renames", "--relative", ref)
    if code != 0:
        return {"files": {}, "deleted": [], "error": out or "git diff failed (is this a git repository?)"}
    files, deleted = parse_diff(out)
    code, untracked = _git(root, "ls-files", "--others", "--exclude-standard")
    if code == 0:
        for path in untracked.splitlines():
            if path.strip():
                files.setdefault(path.strip().replace("\\", "/"), []).append((1, WHOLE_FILE))
    return {"files": files, "deleted": deleted, "error": ""}


def changed_symbols(data: dict, files: dict[str, list[tuple[int, int]]]) -> tuple[list[dict], dict[str, int]]:
    """Innermost graph symbols overlapping the changed lines, and per-file counts of edits that sit
    outside any symbol (module-level code)."""
    by_file: dict[str, list[dict]] = defaultdict(list)
    for node in ch.symbols(data).values():
        if ch.file_of(node) in files:
            by_file[ch.file_of(node)].append(node)
    found: dict[str, dict] = {}
    module_level: Counter = Counter()
    for file, ranges in files.items():
        nodes = by_file.get(file, [])
        for first, last in ranges:
            hit = [n for n in nodes if ch.span(n)[0] <= last and ch.span(n)[1] >= first]
            # A class whose method changed is not itself changed: keep the innermost symbols.
            leaves = [n for n in hit if not any(m is not n and ch.span(n)[0] <= ch.span(m)[0] and ch.span(m)[1] <= ch.span(n)[1]
                                                and ch.span(m) != ch.span(n) for m in hit)]
            if not leaves:
                module_level[file] += 1
            for node in leaves:
                entry = found.setdefault(node["id"], {"node": node, "changed_lines": 0})
                lo, hi = max(first, ch.span(node)[0]), min(last, ch.span(node)[1])
                entry["changed_lines"] += max(hi - lo + 1, 0)
    return list(found.values()), dict(module_level)


def build_review(data: dict, root: Path, ref: str = "HEAD", prefix: str = "") -> dict:
    changes = git_changes(root, ref)
    if changes["error"]:
        return {"error": changes["error"]}
    files = {f: r for f, r in changes["files"].items() if not prefix or f.startswith(prefix)}
    everything = {n["id"]: n for n in data.get("nodes", [])}
    incoming: dict[str, list[str]] = defaultdict(list)
    for link in ch._links(data):
        if link.get("relation") not in ch.STRUCTURAL and link.get("source") != link.get("target"):
            incoming[link["target"]].append(link["source"])

    graph_files = {ch.file_of(n) for n in data.get("nodes", []) if ch.file_of(n)}
    symbols, module_level = changed_symbols(data, files)
    dead_ids = {i["id"] for i in ch.find_dead(data, root, prefix)["items"] if i["confidence"] != "tests"}
    hot = ch.find_hotspots(data, root, top=HOTSPOT_RANK, prefix=prefix)["symbols"]
    hot_ids = {row["node"]["id"]: rank + 1 for rank, row in enumerate(hot)}

    rows, skipped_tests = [], 0
    for entry in symbols:
        node = entry["node"]
        if is_test(node):
            skipped_tests += 1
            continue
        file = ch.file_of(node)
        callers = incoming.get(node["id"], [])
        outside = [everything[s] for s in dict.fromkeys(callers) if s in everything and ch.file_of(everything[s]) != file]
        inside = [everything[s] for s in dict.fromkeys(callers) if s in everything and ch.file_of(everything[s]) == file]
        tests = [n for n in outside + inside if is_test(n)]
        outside = [n for n in outside if not is_test(n)]       # tests are listed on their own
        inside = [n for n in inside if not is_test(n)]
        size = max(ch.lines_of(node), 1)
        is_new = entry["changed_lines"] / size >= NEW_SHARE
        twin = None
        if is_new and ch.kind_of(node) != "symbol":
            name = ch.bare_name(str(node.get("label") or ""))
            terms = list(dict.fromkeys(ch.tokens(name)))
            for hit in ch.find_similar(data, name, top=3, exclude_id=node["id"]):
                # A real twin has the same words in its name, or shares nearly all of a multi-word name;
                # a short generic one-word name (load, run, tokens) says nothing.
                other = list(dict.fromkeys(ch.tokens(ch.bare_name(str(hit["node"].get("label") or "")))))
                same = terms == other and (len(terms) > 1 or len(name) >= 6)
                close = len(terms) > 1 and len(hit["matched"]) >= math.ceil(0.75 * len(terms))
                if same or close:
                    twin = hit["node"]
                    break
        rows.append({"node": node, "new": is_new, "callers": outside, "local_callers": inside,
                     "tests": tests, "hotspot": hot_ids.get(node["id"]), "unused": node["id"] in dead_ids,
                     "twin": twin, "changed_lines": entry["changed_lines"]})

    def risk(row: dict) -> tuple:
        untested = not row["tests"]
        files_out = len({ch.file_of(n) for n in row["callers"]})
        return (-(files_out * 3 + len(row["callers"]) + (4 if row["hotspot"] else 0) + (2 if untested and row["callers"] else 0)),
                ch.file_of(row["node"]), ch.span(row["node"])[0])

    rows.sort(key=risk)
    return {"error": "", "ref": ref, "files": len(files), "rows": rows, "module_level": module_level,
            "deleted": changes["deleted"], "skipped_tests": skipped_tests,
            "not_in_graph": sorted(f for f in files if f not in graph_files and f not in module_level)}


def _names(nodes: list[dict], limit: int = 4) -> str:
    shown = [ch.bare_name(str(n.get("label") or "")) or str(n.get("label")) for n in nodes[:limit]]
    return ", ".join(shown) + (f" +{len(nodes) - limit}" if len(nodes) > limit else "")


def format_review(result: dict, top: int = 15) -> str:
    if result.get("error"):
        return f"Review: {result['error']}"
    rows = result["rows"]
    if not rows and not result["module_level"] and not result["deleted"]:
        return f"Review (working tree vs {result['ref']}): no changed code found."
    out = [f"Review (working tree vs {result['ref']}): {result['files']} changed file(s), {len(rows)} changed symbol(s)"
           + (f", {sum(result['module_level'].values())} module-level edit(s)" if result["module_level"] else "")
           + (f", {result['skipped_tests']} test symbol(s) skipped" if result["skipped_tests"] else "") + "."]
    for row in rows[:top]:
        node = row["node"]
        flags = []
        if row["new"]:
            flags.append("NEW")
        files_out = len({ch.file_of(n) for n in row["callers"]})
        flags.append(f"{len(row['callers'])} caller(s) in {files_out} other file(s)" if row["callers"] else "no callers outside its file")
        flags.append("tests: " + (_names(row["tests"], 2) if row["tests"] else "NONE"))
        if row["hotspot"]:
            flags.append(f"HOTSPOT #{row['hotspot']}")
        if row["unused"]:
            flags.append("UNUSED after this change")
        out.append(f"\n  {ch.where(node)}  {ch.bare_name(str(node.get('label') or ''))}  [{ch.kind_of(node)}; {row['changed_lines']} changed line(s)]")
        out.append("    " + " | ".join(flags))
        if row["callers"]:
            out.append(f"    callers: {_names(row['callers'])}")
        if row["twin"]:
            out.append(f"    looks like existing: {ch.where(row['twin'])}  {ch.bare_name(str(row['twin'].get('label') or ''))}")
    if len(rows) > top:
        out.append(f"\n(+{len(rows) - top} more changed symbols; raise --top)")
    for file, count in sorted(result["module_level"].items()):
        out.append(f"\n  {file}: {count} edit(s) outside any function/class (imports, constants, top-level code)")
    if result["deleted"]:
        out.append(f"\n  deleted: {', '.join(result['deleted'][:5])}" + (" ..." if len(result["deleted"]) > 5 else "")
                   + " — check that nothing still imports them (`affected --node <file>`).")
    if result["not_in_graph"]:
        out.append(f"\n  not in the graph (docs/config/unparsed): {', '.join(result['not_in_graph'][:6])}")
    untested = [r for r in rows if not r["tests"] and r["callers"]]
    out.append(f"\nCheck first: {len(untested)} changed symbol(s) with callers but no test; "
               f"{sum(1 for r in rows if r['hotspot'])} hotspot(s). Details: `--action affected --node <name>`.")
    return "\n".join(out)
