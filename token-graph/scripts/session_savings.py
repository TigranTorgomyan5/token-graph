#!/usr/bin/env python3
"""How many tokens Token Graph saved in a Claude Code session, in tokens and percent.

Reads the session's transcript (~/.claude/projects/<project>/<session>.jsonl) on this
computer; no model or network is used. Tokens are estimated as 4 characters per token.

For every graph answer (query/explain/affected/path/hubs) it compares
  without graph:  reading in full each file the answer pointed to and Claude then opened
                  (or, when Claude opened none, the answer's most-cited file)
  with graph:     the graph answer itself + what Claude actually read from those files
saved = without - with, percent = saved / without.

    python session_savings.py                  latest session
    python session_savings.py --session <id>   one session (id or .jsonl path)
    python session_savings.py --list 10        the 10 most recent sessions
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import Counter
from pathlib import Path

PROJECTS = Path.home() / ".claude" / "projects"
CHARS_PER_TOKEN = 4
ANSWER_ACTIONS = ("query", "explain", "affected", "path", "hubs")
GRAPHIFY_CALL = re.compile(r'''(?:^|[;&|])\s*(?:(?:env|command)\s+)?(?:[A-Za-z_]\w*=(?:"[^"]*"|'[^']*'|[^\s;&|]+)\s+)*(?:timeout\s+\d+[smh]?\s+)?(?:"[^"\n]*[/\\]graphify(?:\.exe)?"|'[^'\n]*[/\\]graphify(?:\.exe)?'|(?:[^\s"';&|]*[/\\])?graphify(?:\.exe)?)\s+(query|explain|affected|path|hubs)\b''', re.I)
SHELL_TOOLS = ("Bash", "PowerShell")
READ_VERB = re.compile(r"\b(cat|sed|head|tail|less|more|Get-Content|gc|type|awk)\b")
FILE_PATTERNS = (
    re.compile(r"src=([^\s\]]+)"),                              # query: NODE x [src=path loc=L1]
    re.compile(r"Source:\s+(\S+)\s+L\d"),                       # explain header
    re.compile(r"([\w.\\/-]+\.[A-Za-z0-9]+):L\d+"),             # connections, affected: path:L12
)


def tokens(text: str | int) -> int:
    size = text if isinstance(text, int) else len(text)
    return (size + CHARS_PER_TOKEN - 1) // CHARS_PER_TOKEN


def result_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(item.get("text", "") for item in content if isinstance(item, dict))
    return ""


def load_calls(transcript: Path, info: dict | None = None) -> list[dict]:
    """Tool calls in order: {name, input, output}. With `info`, also fills in where and when the
    session ran: title (Claude Code's session name), folder, started, last_prompt."""
    calls: dict[str, dict] = {}
    order: list[str] = []
    with transcript.open(encoding="utf-8", errors="replace") as stream:
        for line in stream:
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            if info is not None and isinstance(entry, dict):
                kind = entry.get("type")
                if kind == "ai-title" and entry.get("aiTitle"):
                    info["title"] = str(entry["aiTitle"])          # the latest name wins
                elif kind == "last-prompt" and entry.get("lastPrompt"):
                    info["last_prompt"] = str(entry["lastPrompt"])
                if entry.get("cwd") and not info.get("folder"):
                    info["folder"] = str(entry["cwd"])
                if entry.get("timestamp") and not info.get("started"):
                    info["started"] = str(entry["timestamp"])
            content = (entry.get("message") or {}).get("content")
            if not isinstance(content, list):
                continue
            for item in content:
                if not isinstance(item, dict):
                    continue
                if item.get("type") == "tool_use" and item.get("id") not in calls:
                    calls[item["id"]] = {"name": item.get("name", ""), "input": item.get("input") or {}, "output": "", "cwd": entry.get("cwd"), "error": False}
                    order.append(item["id"])
                elif item.get("type") == "tool_result" and item.get("tool_use_id") in calls:
                    calls[item["tool_use_id"]]["output"] = result_text(item.get("content"))
                    calls[item["tool_use_id"]]["error"] = bool(item.get("is_error"))
    return [calls[i] for i in order]


def _windows_path(raw: str) -> str:
    if re.match(r"^/[a-zA-Z](/|$)", raw):  # Git Bash /c/Users -> C:/Users
        return raw[1].upper() + ":" + (raw[2:] or "/")
    return raw


CHANGE_DIR = re.compile(r"(?:^|[;&|]|\s)(?:cd|Set-Location|pushd)\s+(?:-Path\s+)?(\"[^\"]+\"|'[^']+'|[^\s;&|]+)")


def absolute_path(path: Path) -> bool:
    return path.is_absolute() or bool(re.match(r'^[A-Za-z]:[/\\]', str(path)))


def command_path(raw: str, command: str, cwd: str | None) -> Path | None:
    """Resolve only literal shell path variables; never evaluate shell expressions."""
    raw = raw.strip("\"'")
    assignments = {}
    for match in re.finditer(r'''(?:^|[;\s])(?:export\s+)?\$?([A-Za-z_]\w*)=("[^"\n]*"|'[^'\n]*'|[^\s;&|]+)''', command):
        assignments[match.group(1)] = match.group(2).strip("\"'")
    unresolved = False
    def expand(match):
        nonlocal unresolved
        name = match.group(1) or match.group(2)
        value = {'PWD': cwd or '', 'HOME': str(Path.home())}.get(name, assignments.get(name, ''))
        if not value: unresolved = True
        return value
    raw = re.sub(r'\$\{([A-Za-z_]\w*)\}|\$([A-Za-z_]\w*)', expand, raw)
    if unresolved or not raw or '$' in raw or '`' in raw:
        return None
    return Path(_windows_path(raw)).expanduser()


def project_root(command: str, cwd: str | None = None) -> Path | None:
    """The project a `token_graph.py launch <project>` command ran on ($var paths included).

    A relative project (`launch .`) is relative to a `cd`/`Set-Location` earlier in the same
    command, else to the folder the session was started in (`cwd`)."""
    graphify = GRAPHIFY_CALL.search(command)
    if graphify:
        base = Path(cwd) if cwd else None
        for moved in CHANGE_DIR.finditer(command[:graphify.start(1)]):
            target = command_path(moved.group(1), command[:graphify.start(1)], cwd)
            if target is None: return None
            base = target if absolute_path(target) else (base / target if base else None)
        # An explicit graph may point at another project's index.
        explicit = re.search(r'''--graph(?:=|\s+)("[^"]+"|'[^']+'|[^\s;&|]+)''', command[graphify.end():])
        if explicit:
            graph = command_path(explicit.group(1), command[:graphify.end()], cwd)
            if graph is None: return None
            if not absolute_path(graph):
                if base is None: return None
                graph = base / graph
            return graph.parent.parent if graph.name == 'graph.json' and graph.parent.name == 'graphify-out' else None
        return base
    match = re.search(r"\blaunch\s+(\"[^\"]+\"|'[^']+'|\S+)", command)
    if not match:
        return None
    raw = match.group(1).strip("\"'")
    variable = re.fullmatch(r"\$\{?(\w+)\}?", raw)
    if variable and variable.group(1) == "PWD":
        raw = "."
    elif variable:  # PowerShell $r="C:\..." or shell r="/c/..."
        assigned = re.search(r"(?:^|[\s;])\$?" + variable.group(1) + r"\s*=\s*[\"']([^\"']+)[\"']", command)
        raw = assigned.group(1) if assigned else ""
    raw = _windows_path(raw)
    if not raw:
        return None
    path = Path(raw)
    if absolute_path(path):
        return path
    base = Path(cwd) if cwd else None
    for moved in CHANGE_DIR.finditer(command[:match.start()]):
        target = Path(_windows_path(moved.group(1).strip("\"'")))
        base = target if absolute_path(target) else (base or Path(".")) / target
    return (base / path) if base else path


def graph_action(command: str) -> str | None:
    match = GRAPHIFY_CALL.search(command)
    if match:
        return match.group(1).lower()
    if "token_graph.py" not in command or not re.search(r"\blaunch\s", command):
        return None
    action = re.search(r"--action\s+(\w+)", command)
    if action:
        return action.group(1) if action.group(1) in ANSWER_ACTIONS else None
    return "query" if "--question" in command else None


def cited_files(output: str, root: Path) -> Counter:
    """Project files an answer points to, by how often it cites them."""
    found: Counter = Counter()
    for pattern in FILE_PATTERNS:
        for raw in pattern.findall(output):
            path = Path(raw.replace("\\", "/"))
            full = path if path.is_absolute() else root / path
            try:
                resolved = full.resolve()
                resolved.relative_to(root.resolve())
                if resolved.is_file():
                    found[resolved] += 1
            except (OSError, ValueError):
                continue
    return found


def mentions(command: str, file: Path, root: Path | None) -> bool:
    text = command.replace("\\", "/")
    names = {str(file).replace("\\", "/")}
    if root:
        try:
            names.add(file.relative_to(root.resolve()).as_posix())
        except ValueError:
            pass
    if any(name in text for name in names):
        return True
    # A path relative to another folder (cd token-graph && sed -n 1,9p scripts/x.py): the file
    # name is enough, because only files the preceding graph answer cited are checked.
    return re.search(r"(?:^|[\s\"'/\\])" + re.escape(file.name) + r"(?:$|[\s\"';|])", command) is not None


def file_tokens(file: Path, cache: dict) -> int:
    if file not in cache:
        try:
            cache[file] = tokens(file.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            cache[file] = 0
    return cache[file]


def same_path(a: Path, b: Path) -> bool:
    return os.path.normcase(str(a.resolve())) == os.path.normcase(str(b.resolve()))


def analyze(transcript: Path, project: Path | None = None) -> dict:
    """Savings of one session; with `project`, only graph answers about that project count."""
    info: dict = {}
    calls = load_calls(transcript, info)
    sizes: dict[Path, int] = {}
    answers = []
    for index, call in enumerate(calls):
        command = str(call["input"].get("command", "")) if call["name"] in SHELL_TOOLS else ""
        action = graph_action(command)
        root = project_root(command, call.get("cwd") or info.get("folder")) if action else None
        if not action or not root or call.get('error') or not call['output'] or (project is not None and not same_path(root, project)):
            continue
        answers.append({"index": index, "action": action, "root": root,
                        "graph_tokens": tokens(call["output"]), "cited": cited_files(call["output"], root)})

    # Reads that follow an answer and open a file it cited.
    opened: dict[Path, int] = {}       # file -> tokens Claude actually read from it
    used_by: dict[int, set] = {}       # answer -> cited files Claude opened
    for answer_no, answer in enumerate(answers):
        end = answers[answer_no + 1]["index"] if answer_no + 1 < len(answers) else len(calls)
        used_by[answer_no] = set()
        for call in calls[answer["index"] + 1:end]:
            if call["name"] == "Read":
                targets = [Path(str(call["input"].get("file_path", "")))]
                targets = [t.resolve() for t in targets if t.is_file() and t.resolve() in answer["cited"]]
            elif call["name"] in SHELL_TOOLS:
                command = str(call["input"].get("command", ""))
                if graph_action(command) or not READ_VERB.search(command):
                    continue
                targets = [f for f in answer["cited"] if mentions(command, f, answer["root"])]
            else:
                continue
            for target in targets:
                opened[target] = opened.get(target, 0) + tokens(call["output"]) // len(targets)
                used_by[answer_no].add(target)

    without = with_graph = 0
    counted: set = set()
    rows = []
    for answer_no, answer in enumerate(answers):
        files = used_by[answer_no] or ({answer["cited"].most_common(1)[0][0]} if answer["cited"] else set())
        new = files - counted          # each file is counted once per session: it would be read once
        counted |= new
        full = sum(file_tokens(f, sizes) for f in new)
        read = sum(opened.pop(f, 0) for f in new)
        without += full
        with_graph += answer["graph_tokens"] + read
        rows.append({"action": answer["action"], "project": answer["root"].name, "graph_tokens": answer["graph_tokens"],
                     "files": len(new), "full_tokens": full, "read_tokens": read})
    saved = without - with_graph
    return {
        "session": transcript.stem, "scope": "subagent" if transcript.parent.name == 'subagents' else 'session', "transcript": str(transcript), "updated": transcript.stat().st_mtime,
        "title": info.get("title", ""), "folder": info.get("folder", ""), "started": info.get("started", ""),
        "last_prompt": info.get("last_prompt", ""),
        "graph_answers": len(answers),
        "graph_tokens": sum(r["graph_tokens"] for r in rows), "read_tokens": sum(r["read_tokens"] for r in rows),
        "files": len(counted), "without_graph_tokens": without, "with_graph_tokens": with_graph,
        "saved_tokens": saved, "saved_percent": round(100 * saved / without, 1) if without else None,
        "answers": rows,
    }


def find_transcript(session: str | None) -> Path:
    if session:
        path = Path(session)
        if path.suffix == ".jsonl" and path.is_file():
            return path
        matches = list(PROJECTS.glob(f"*/{session}*.jsonl"))
        if not matches:
            raise SystemExit(f"No transcript for session {session!r} under {PROJECTS}")
        return max(matches, key=lambda p: p.stat().st_mtime)
    transcripts = recent(1)
    if not transcripts:
        raise SystemExit(f"No Claude Code transcripts under {PROJECTS}")
    return transcripts[0]


def recent(count: int) -> list[Path]:
    paths = []
    for pattern in ("*/*.jsonl", "*/*/subagents/*.jsonl"):
        for path in PROJECTS.glob(pattern):
            try: paths.append((path.stat().st_mtime, path))
            except OSError: continue
    return [p for _, p in sorted(paths, key=lambda item: item[0], reverse=True)[:count]]


def project_transcripts(project: Path, count: int) -> list[Path]:
    """Also check this project's own history, even when newer sessions belong elsewhere."""
    folder = PROJECTS / re.sub(r'[^A-Za-z0-9]', '-', str(project.resolve()))
    paths = []
    for pattern in ('*.jsonl', '*/subagents/*.jsonl'):
        for path in folder.glob(pattern):
            try: paths.append((path.stat().st_mtime, path))
            except OSError: continue
    return [p for _, p in sorted(paths, key=lambda item: item[0], reverse=True)[:count]]


_CACHE: dict[tuple, dict] = {}


def recent_savings(project: Path | None = None, *, scan: int = 300, limit: int = 10) -> list[dict]:
    """Check recent global transcripts and recent transcripts from the project's own folder.
    Results are cached per transcript version, so a page refresh re-reads only changed sessions."""
    results = []
    candidates = recent(scan)
    if project:
        candidates = list(dict.fromkeys(candidates + project_transcripts(project, scan)))
        def modified(path):
            try: return path.stat().st_mtime
            except OSError: return 0
        candidates.sort(key=modified, reverse=True)
    for path in candidates:
        try:
            key = (str(path), path.stat().st_mtime, str(project or ""))
            if key not in _CACHE:
                with path.open(encoding="utf-8", errors="replace") as stream:
                    used = any("token_graph.py" in line or "graphify" in line for line in stream)
                _CACHE[key] = analyze(path, project) if used else {"graph_answers": 0}
        except OSError:
            continue
        if _CACHE[key]["graph_answers"]:
            results.append(_CACHE[key])
            if len(results) >= limit:
                break
    return results


def bar(percent: float, width: int = 30) -> str:
    filled = max(0, min(width, round(width * percent / 100)))
    return "#" * filled + "." * (width - filled)


def percent_text(result: dict) -> str:
    percent = result["saved_percent"]
    return "n/a (the answers pointed to no project file)" if percent is None else f"{percent}%"


def report(result: dict) -> str:
    if not result["graph_answers"]:
        return f"Token Graph - session {result['session'][:8]}\n  No graph answers in this session."
    lines = [
        f"Token Graph - session {result['session'][:8]}" + (f"  \"{result['title']}\"" if result.get("title") else ""),
        f"  Folder: {result.get('folder') or '?'}   started {result.get('started', '')[:16].replace('T', ' ') or '?'}",
        f"  Graph answers:            {result['graph_answers']:>9,}",
        f"  Files they pointed to:    {result['files']:>9,}",
        "",
        f"  Without graph (estimate): {result['without_graph_tokens']:>9,} tokens  (read those files in full)",
        f"  With graph:               {result['with_graph_tokens']:>9,} tokens  "
        f"(answers {result['graph_tokens']:,} + reads {result['read_tokens']:,})",
        f"  Saved:                    {result['saved_tokens']:>9,} tokens  = {percent_text(result)}",
        f"  [{bar(result['saved_percent'] or 0)}] {percent_text(result)}",
        "",
        "  Estimate: 4 characters = 1 token; file sizes as they are on disk now.",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--session", help="session id (or its start) or a transcript .jsonl path; default: latest")
    parser.add_argument("--list", type=int, metavar="N", help="summary of the N most recent sessions")
    parser.add_argument("--project", help="count only graph answers about this project folder")
    parser.add_argument("--details", action="store_true", help="one line per graph answer")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    args = parser.parse_args(argv)
    if args.list:
        project = Path(args.project) if args.project else None
        results = [analyze(path, project) for path in recent(args.list)]
        if args.json:
            print(json.dumps(results, indent=2))
            return 0
        print(f"{'session':10} {'answers':>7} {'without':>10} {'with':>9} {'saved':>10} {'%':>6}  title")
        for r in results:
            print(f"{r['session'][:8]:10} {r['graph_answers']:>7} {r['without_graph_tokens']:>10,} "
                  f"{r['with_graph_tokens']:>9,} {r['saved_tokens']:>10,} "
                  f"{'-' if r['saved_percent'] is None else str(r['saved_percent']) + '%':>6}  {r.get('title', '')[:40]}")
        return 0
    result = analyze(find_transcript(args.session), Path(args.project) if args.project else None)
    if args.json:
        print(json.dumps(result, indent=2))
        return 0
    print(report(result))
    if args.details:
        print("\n  action    project            answer   files  without     read")
        for row in result["answers"]:
            print(f"  {row['action']:9} {row['project'][:18]:18} {row['graph_tokens']:>6,} {row['files']:>6} "
                  f"{row['full_tokens']:>8,} {row['read_tokens']:>8,}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
