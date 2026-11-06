#!/usr/bin/env python3
"""Claude Code hook: look at the Token Graph before searching or reading source.

  hook_guard.py guard      PreToolUse (Bash|Grep|Read|Glob): record a Token Graph query, or nudge/deny
  hook_guard.py reset      SessionStart (compact): give the session a fresh nudge budget
  hook_guard.py install    add both hooks to ~/.claude/settings.json (replaces `graphify hook-guard`)
  hook_guard.py uninstall  remove them

Soft by default: after `NUDGE_CAP` reminders per session it stays silent. Strict mode
(TOKEN_GRAPH_HOOK_STRICT=1, or `install --strict`) denies Grep/Read/search until a Token Graph query
ran in the last `FRESH_SECONDS`. Repos without a graph, with a tiny graph, or reads outside the repo
are never touched. Standard library only; reads no source and sends nothing anywhere.
"""
from __future__ import annotations
import json
import os
import re
import shlex
import sys
import time
from pathlib import Path

NUDGE_CAP = int(os.environ.get("TOKEN_GRAPH_HOOK_NUDGE_CAP", "5"))
FRESH_SECONDS = int(os.environ.get("TOKEN_GRAPH_HOOK_FRESH_SECONDS", "1800"))
MIN_GRAPH_BYTES = 150_000           # below this the whole repo is cheaper to read than to query
STATE_DIR = Path.home() / ".token-graph" / "hook-state"
SETTINGS = Path.home() / ".claude" / "settings.json"
MARK = "hook_guard.py"
GRAPH_USE = re.compile(r"token_graph\.py\b.*\b(query|explain|affected|path|hubs|launch)\b")
SEARCH = re.compile(r"(^|[\s;&|(])(grep|egrep|rg|ag|ack|find|fd)\s")
SKIP_READ = re.compile(r"(^|/)(CLAUDE|AGENTS|README|SKILL)\.md$|\.(md|json|ya?ml|toml|lock)$", re.I)


def strict() -> bool:
    return os.environ.get("TOKEN_GRAPH_HOOK_STRICT", "") == "1" or (STATE_DIR / "strict").exists()


def find_graph(start: str) -> Path | None:
    here = Path(start or ".").resolve()
    for folder in (here, *here.parents):
        graph = folder / "graphify-out" / "graph.json"
        if graph.is_file():
            return graph
        if (folder / ".git").exists():
            break
    return None


def load(session: str) -> dict:
    try:
        return json.loads((STATE_DIR / f"{session}.json").read_text())
    except (OSError, ValueError):
        return {}


def save(session: str, state: dict) -> None:
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        (STATE_DIR / f"{session}.json").write_text(json.dumps(state))
    except OSError:
        pass


def target_path(tool: str, args: dict) -> str:
    return str(args.get("file_path") or args.get("path") or args.get("pattern") or "")


def classify(tool: str, args: dict) -> str:
    """'query' | 'search' | 'read' | '' (everything else is none of our business)."""
    if tool == "Bash":
        command = str(args.get("command") or "")
        if GRAPH_USE.search(command):
            return "query"
        return "search" if SEARCH.search(command) else ""
    if tool == "Grep":
        return "search"
    if tool == "Glob":
        return "read"
    if tool == "Read":
        return "" if SKIP_READ.search(str(args.get("file_path") or "")) else "read"
    return ""


def guard(event: dict) -> dict | None:
    kind = classify(str(event.get("tool_name") or ""), event.get("tool_input") or {})
    if not kind:
        return None
    session = re.sub(r"[^\w.-]", "_", str(event.get("session_id") or "default"))
    state = load(session)
    if kind == "query":
        state["last_query"] = time.time()
        save(session, state)
        return None
    graph = find_graph(str(event.get("cwd") or os.getcwd()))
    if graph is None or graph.stat().st_size < MIN_GRAPH_BYTES:
        return None
    target = target_path(str(event.get("tool_name")), event.get("tool_input") or {})
    if target.startswith("/") and not str(Path(target).resolve()).startswith(str(graph.parent.parent)):
        return None                                   # outside this repo
    if time.time() - float(state.get("last_query", 0)) < FRESH_SECONDS:
        return None
    command = f"python3 ~/.claude/skills/token-graph/scripts/token_graph.py launch {graph.parent.parent} --action query --question \"<task>\" --no-tests"
    if strict():
        state["denied"] = int(state.get("denied", 0)) + 1
        save(session, state)
        return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                "permissionDecisionReason": f"Query the Token Graph first (strict mode): {command}"}}
    if int(state.get("nudges", 0)) >= NUDGE_CAP:
        return None
    state["nudges"] = int(state.get("nudges", 0)) + 1
    save(session, state)
    return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "additionalContext":
            f"This repo has a Token Graph. Locate code with it before searching or reading many files: {command}"}}


def reset(event: dict) -> None:
    session = re.sub(r"[^\w.-]", "_", str(event.get("session_id") or "default"))
    state = load(session)
    state.update(nudges=0, denied=0)
    save(session, state)


def _ours(entry: dict) -> bool:
    return any(MARK in str(h.get("command", "")) or "graphify hook-guard" in str(h.get("command", ""))
               for h in entry.get("hooks", []))


def install(make_strict: bool) -> None:
    script = Path(__file__).resolve()
    settings = json.loads(SETTINGS.read_text()) if SETTINGS.is_file() else {}
    if SETTINGS.is_file():
        SETTINGS.with_suffix(".json.bak-token-graph").write_text(SETTINGS.read_text())
    hooks = settings.setdefault("hooks", {})
    for event in ("PreToolUse", "SessionStart"):
        hooks[event] = [e for e in hooks.get(event, []) if not _ours(e)]
    base = f"python3 {shlex.quote(str(script))}"
    hooks["PreToolUse"].append({"matcher": "Bash|Grep|Read|Glob", "hooks": [{"type": "command", "command": f"{base} guard", "timeout": 10}]})
    hooks["SessionStart"].append({"matcher": "compact", "hooks": [{"type": "command", "command": f"{base} reset", "timeout": 10}]})
    SETTINGS.write_text(json.dumps(settings, indent=2) + "\n")
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    flag = STATE_DIR / "strict"
    flag.touch() if make_strict else flag.unlink(missing_ok=True)
    print(f"installed in {SETTINGS} ({'strict' if make_strict else 'soft'}); replaced any `graphify hook-guard` entries")


def uninstall() -> None:
    settings = json.loads(SETTINGS.read_text())
    for event in ("PreToolUse", "SessionStart"):
        settings.get("hooks", {})[event] = [e for e in settings.get("hooks", {}).get(event, []) if not _ours(e)]
        if not settings["hooks"][event]:
            del settings["hooks"][event]
    SETTINGS.write_text(json.dumps(settings, indent=2) + "\n")
    print("removed")


def main() -> int:
    command = sys.argv[1] if len(sys.argv) > 1 else "guard"
    if command == "install":
        install("--strict" in sys.argv)
        return 0
    if command == "uninstall":
        uninstall()
        return 0
    try:
        event = json.loads(sys.stdin.read() or "{}")
        result = guard(event) if command == "guard" else (reset(event) or None)
    except Exception:           # a hook must never break the tool call
        return 0
    if result:
        print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
