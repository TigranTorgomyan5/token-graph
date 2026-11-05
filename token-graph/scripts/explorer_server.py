#!/usr/bin/env python3
"""Local Project Explorer server: pick a project folder, build/update its graph, view it.

Listens on 127.0.0.1 only. Every /api call must carry the per-run token in the
X-Token-Graph header, so another web page in the same browser cannot trigger a
build or read files. Running it again reuses the server that is already up.
"""
from __future__ import annotations
import hashlib
import json
import os
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
import threading
import time
from urllib.parse import parse_qs, urlparse
import urllib.request
import webbrowser

SKILL = Path(__file__).resolve().parents[1]
SCRIPTS = SKILL / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
# Child scripts print UTF-8 so their output decodes correctly here on any console code page.
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
from local_inference import local_models
import explore_agent
import system_links
from token_graph import runtime_problems
PAGE = SKILL / "assets" / "project-explorer.html"
STATE = SKILL / ".explorer-server.json"      # port + token of the running server


def code_version() -> str:
    """Changes whenever a script of this skill changes, so the launcher can replace an outdated server."""
    digest = hashlib.sha256()
    for path in sorted(SCRIPTS.glob("*.py")):
        stat = path.stat()
        digest.update(f"{path.name}:{stat.st_size}:{stat.st_mtime_ns}".encode())
    return digest.hexdigest()[:12]


CODE_VERSION = code_version()
PROJECTS = SKILL / ".explorer-projects.json"  # projects built or opened here, newest first
RUNTIME = SKILL / ".runtime" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
MAX_PROJECTS = 50


def claude_agents(project: Path | None = None, user_home: Path | None = None) -> list[dict]:
    """List agent metadata only; never read or execute agent instructions or hooks."""
    roots = [(user_home or Path.home(), 'User')]
    if project:
        roots.append((project, 'Project'))
    agents = []
    for root, scope in roots:
        directory = root / '.claude' / 'agents'
        try:
            directory.resolve().relative_to(root.resolve())
            if not directory.is_dir():
                continue
            for path in sorted(directory.glob('*.md'))[:200]:
                if path.is_symlink() or path.stat().st_size > 128_000:
                    continue
                with path.open(encoding='utf-8') as source:
                    if source.readline().strip() != '---':
                        continue
                    fields = {}
                    for _ in range(100):
                        line = source.readline()
                        if not line or line.strip() == '---':
                            break
                        match = re.match(r'^(name|description|model|effort|tools):\s*(.*?)\s*$', line)
                        if match:
                            fields[match.group(1)] = match.group(2).strip('"\'')[:1200]
                agents.append({'name': fields.get('name') or path.stem, 'description': fields.get('description', ''),
                               'model': fields.get('model', ''), 'effort': fields.get('effort', ''),
                               'tools': fields.get('tools', ''), 'scope': scope})
        except (OSError, UnicodeError, ValueError):
            continue
    return agents

PICK_FOLDER = r"""
import sys, tkinter
from tkinter import filedialog
root = tkinter.Tk(); root.withdraw(); root.attributes('-topmost', True)
path = filedialog.askdirectory(title='Choose a project folder for Token Graph', initialdir=sys.argv[1] or None, mustexist=True)
root.destroy()
sys.stdout.write(path or '')
"""


APPLE_FOLDER_PICKER = r'''on run argv
set startFolder to item 1 of argv
activate
try
    if startFolder is "" then
        set chosenFolder to choose folder with prompt "Choose a project folder for Token Graph"
    else
        set chosenFolder to choose folder with prompt "Choose a project folder for Token Graph" default location (POSIX file startFolder)
    end if
on error number -128
    return ""
end try
return POSIX path of chosenFolder
end run'''

WINDOWS_FOLDER_PICKER = r'''
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
Add-Type -AssemblyName System.Windows.Forms
$owner = New-Object System.Windows.Forms.Form
$owner.TopMost = $true; $owner.ShowInTaskbar = $false; $owner.StartPosition = 'CenterScreen'
$owner.Size = New-Object System.Drawing.Size(1, 1); $owner.Opacity = 0
$owner.Show(); $owner.Activate()
$dialog = New-Object System.Windows.Forms.FolderBrowserDialog
$dialog.Description = 'Choose a project folder for Token Graph'
$dialog.SelectedPath = $env:TOKEN_GRAPH_FOLDER_START
$dialog.ShowNewFolderButton = $false
$answer = $dialog.ShowDialog($owner)
$owner.Close()
if ($answer -eq [System.Windows.Forms.DialogResult]::OK) { [Console]::Out.Write($dialog.SelectedPath) }
'''


def _run_folder_picker(command: list[str], *, env: dict | None = None) -> subprocess.CompletedProcess | None:
    try:
        return subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace",
                              creationflags=NO_WINDOW, timeout=600, env=env, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None


def _picked_path(result: subprocess.CompletedProcess | None, *, cancel_code: int | None = None) -> tuple[str, bool] | None:
    """Return (path, cancelled), or None when the dialog command is unavailable/broken.

    zenity and kdialog exit with 1 when the user presses Cancel (`cancel_code`); that is an
    answer, not a missing dialog, so no second (Tk) dialog may open after it."""
    if result is None:
        return None
    if result.returncode == 0:
        return result.stdout.strip(), not result.stdout.strip()
    if cancel_code is not None and result.returncode == cancel_code and not result.stdout.strip() and not result.stderr.strip():
        return "", True
    return None


def desktop_os() -> str:
    """'mac', 'windows' or 'linux' (any other Unix desktop): which native dialog to try."""
    if sys.platform == "darwin":
        return "mac"
    return "windows" if os.name == "nt" else "linux"


def pick_folder(start: str) -> dict:
    """Open a native chooser on macOS/Windows/Linux, then Tk if installed; otherwise allow path entry."""
    result, cancel_code = None, None
    system = desktop_os()
    if system == "mac" and shutil.which("osascript"):
        result = _run_folder_picker([shutil.which("osascript"), "-e", APPLE_FOLDER_PICKER, start])
    elif system == "windows":
        powershell = shutil.which("powershell.exe") or shutil.which("powershell") or shutil.which("pwsh.exe") or shutil.which("pwsh")
        if powershell:
            env = os.environ.copy()
            env["TOKEN_GRAPH_FOLDER_START"] = start
            result = _run_folder_picker([powershell, "-NoProfile", "-STA", "-Command", WINDOWS_FOLDER_PICKER], env=env)
    else:
        zenity, kdialog = shutil.which("zenity"), shutil.which("kdialog")
        cancel_code = 1
        if zenity:
            start_dir = str(Path(start).resolve())
            result = _run_folder_picker([zenity, "--file-selection", "--directory", "--title=Choose a project folder for Token Graph",
                                         f"--filename={start_dir.rstrip(os.sep)}{os.sep}"])
        elif kdialog:
            result = _run_folder_picker([kdialog, "--getexistingdirectory", start, "--title", "Choose a project folder for Token Graph"])

    picked = _picked_path(result, cancel_code=cancel_code)
    if picked is not None:
        path, _cancelled = picked
        return {"path": str(Path(path).resolve()) if path else "", "manual": False}

    # Tk is a useful portable fallback, but several Python distributions omit _tkinter.
    executable = sys.executable.replace("pythonw.exe", "python.exe")
    tk_result = _run_folder_picker([executable, "-I", "-c", PICK_FOLDER, start])
    picked = _picked_path(tk_result)
    if picked is not None:
        path, _cancelled = picked
        return {"path": str(Path(path).resolve()) if path else "", "manual": False}
    return {"path": "", "manual": True}


MAX_FOLDERS = 1000


def folder_roots() -> list[dict]:
    """Starting points for the in-page folder browser: home, then the drives (Windows) or /."""
    home = Path.home()
    roots = [{"name": "Home", "path": str(home)}]
    if os.name == "nt":
        import string
        roots += [{"name": f"{letter}:", "path": f"{letter}:\\"} for letter in string.ascii_uppercase if os.path.isdir(f"{letter}:\\")]
    else:
        roots.append({"name": "/", "path": "/"})
        for mounts in ("/Volumes", "/mnt", "/media"):
            if os.path.isdir(mounts):
                roots.append({"name": mounts, "path": mounts})
    return roots


def list_folders(raw: str | None) -> dict:
    """Sub-folders of a folder, for choosing a folder inside the page on any OS, with no native
    dialog. Only folder names are listed (no files); hidden folders are left out."""
    path = Path(raw).expanduser() if raw else Path.home()
    try:
        path = path.resolve()
    except OSError:
        pass
    if not path.is_dir():
        raise ValueError(f"Not a folder: {path}")
    folders, truncated = [], False
    try:
        with os.scandir(path) as entries:
            for entry in entries:
                if entry.name.startswith((".", "$")):
                    continue
                try:
                    if not entry.is_dir():
                        continue
                except OSError:
                    continue
                if len(folders) >= MAX_FOLDERS:
                    truncated = True
                    break
                folders.append({"name": entry.name, "path": str(path / entry.name)})
    except PermissionError:
        raise PermissionError(f"No permission to open {path}")
    folders.sort(key=lambda item: item["name"].lower())
    parent = path.parent if path.parent != path else None
    return {"path": str(path), "parent": str(parent) if parent else "", "folders": folders,
            "truncated": truncated, "roots": folder_roots(), "separator": os.sep}


def find_rider() -> str | None:
    """rider64.exe from a standalone install or JetBrains Toolbox; None when Rider is not installed."""
    import glob
    import shutil
    found = shutil.which("rider64") or shutil.which("rider64.exe")
    if found:
        return found
    local = os.environ.get("LOCALAPPDATA", "")
    patterns = [
        os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"), "JetBrains", "*Rider*", "bin", "rider64.exe"),
        os.path.join(local, "Programs", "*Rider*", "bin", "rider64.exe"),
        os.path.join(local, "JetBrains", "Toolbox", "apps", "Rider", "*", "*", "bin", "rider64.exe"),
        os.path.join(local, "JetBrains", "Toolbox", "apps", "Rider", "*", "bin", "rider64.exe"),
        os.path.join(local, "JetBrains", "Toolbox", "apps", "rider", "bin", "rider64.exe"),
    ]
    hits = sorted((p for pattern in patterns for p in glob.glob(pattern)), key=os.path.getmtime, reverse=True)
    return hits[0] if hits else None


def has_vscode() -> bool:
    if os.name != "nt":
        return False
    import winreg
    try:
        winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, "vscode").Close()  # the vscode:// handler the installer registers
        return True
    except OSError:
        return False


def open_in_editor(editor: str, file: Path, line: int) -> None:
    if editor == "vscode":
        from urllib.parse import quote
        # A URL handled by VS Code itself: no shell, no command-line parsing of the path.
        os.startfile("vscode://file/" + quote(file.as_posix(), safe="/:") + f":{line}:1")
        return
    rider = find_rider()
    if not rider:
        raise ValueError("Rider is not installed on this computer.")
    flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    subprocess.Popen([rider, "--line", str(line), str(file)], creationflags=flags, close_fds=True)


def ask(body: dict, project: Path) -> dict:
    """Run scripts/ask_engine.py (bundled engine, offline policy) for one question/path/impact request."""
    mode = body.get("mode")
    text = lambda key: str(body.get(key) or "").strip()[:300]
    if mode == "query":
        if not text("a"):
            raise ValueError("Type a question.")
        args = [text("a"), str(min(max(int(body.get("budget") or 800), 300), 6000))]
    elif mode == "path":
        if not text("a") or not text("b"):
            raise ValueError("Fill in both From and To.")
        args = [text("a"), text("b")]
    elif mode == "affected":
        if not text("a"):
            raise ValueError("Name the class, method or file to check.")
        args = [text("a"), str(min(max(int(body.get("depth") or 2), 1), 4))]
    else:
        raise ValueError("Unknown mode")
    if not graph_path(project).is_file():
        raise ValueError("This project has no graph yet; build it first.")
    # The page's Hide tests / Hide generated toggles also scope the answer.
    args += [flag for flag, on in (("--no-tests", body.get("hideTests")), ("--no-generated", body.get("hideGenerated"))) if on]
    try:
        result = subprocess.run([engine_python(), str(SCRIPTS / "ask_engine.py"), str(project), mode, *args], cwd=project,
                                capture_output=True, text=True, encoding="utf-8", errors="replace",
                                creationflags=NO_WINDOW, timeout=180, check=False)
    except subprocess.TimeoutExpired:
        raise ValueError("The graph engine took longer than 3 minutes; try a narrower question.") from None
    if result.returncode:
        raise ValueError((result.stderr.strip().splitlines() or ["The graph engine failed."])[-1])
    return json.loads(result.stdout)


def ollama_models() -> list[str]:
    """List installed local models, excluding Ollama's hosted model entries."""
    try:
        models = local_models()
    except (OSError, ValueError) as error:
        raise ValueError('Ollama is not responding. Start the Ollama app and try again.') from error
    return sorted(models)


def llm_answer(body: dict, project: Path) -> dict:
    """Answer with an installed local Ollama model (explore_agent.explore).

    The model searches the graph and reads bounded source excerpts through read-only tools,
    and cites file:line. It cannot edit files or run commands; Ollama is reached on 127.0.0.1 only.
    """
    question = str(body.get("question") or "").strip()
    model = str(body.get("model") or "")
    if not question or len(question) > 4000:
        raise ValueError("Enter a question of up to 4,000 characters.")
    if not graph_path(project).is_file():
        raise ValueError("This project has no graph yet; build it first.")
    if model not in ollama_models():
        raise ValueError("Select an installed local Ollama model first.")
    history = body.get("history", [])
    if not isinstance(history, list) or len(history) > 12 or any(
        not isinstance(item, dict) or item.get("role") not in ("user", "assistant")
        or not isinstance(item.get("content"), str) or len(item["content"]) > 8000 for item in history
    ):
        raise ValueError("Invalid conversation history.")
    started = time.time()
    try:
        text = explore_agent.explore(project, question, model, [{"role": i["role"], "content": i["content"]} for i in history])
    except (OSError, ValueError, KeyError, subprocess.TimeoutExpired) as error:
        raise ValueError(f"The local model could not answer: {error}") from error
    return {"text": text, "model": model, "seconds": round(time.time() - started, 1)}


def agent_answer(body: dict, project: Path) -> dict:
    """Return deterministic evidence for the viewer; this path never calls Ollama."""
    question = str(body.get("question") or "").strip()
    if not question or len(question) > 4000:
        raise ValueError("Enter a question of up to 4,000 characters.")
    if not graph_path(project).is_file():
        raise ValueError("This project has no graph yet; build it first.")
    operation = str(body.get("operation") or "query")
    history = body.get("history", [])
    if not isinstance(history, list) or len(history) > 12 or any(
        not isinstance(item, dict) or item.get("role") not in ("user", "assistant")
        or not isinstance(item.get("content"), str) or len(item["content"]) > 8000 for item in history
    ):
        raise ValueError("Invalid conversation history.")
    try:
        limit = min(100, max(1, int(body.get("limit") or 10)))
        evidence = explore_agent.search_evidence(
            project,
            question,
            operation=operation,
            target=str(body.get("target") or "") or None,
            line=int(body["line"]) if body.get("line") is not None else None,
            limit=limit,
            include_tests=not bool(body.get("hideTests")),
            include_evaluation=bool(body.get("includeEvaluation")),
            include_generated=not bool(body.get("hideGenerated")),
        )
    except (OSError, ValueError, KeyError, subprocess.TimeoutExpired) as error:
        raise ValueError(f"Evidence search failed: {error}") from error
    return {"text": json.dumps(evidence, ensure_ascii=False, indent=2), "evidence": evidence}


SYSTEM_SCANS: dict[str, tuple] = {}  # system id -> (saved timestamp, scan result); rescan on demand


def system_scan(system: dict, refresh: bool) -> dict:
    cached = SYSTEM_SCANS.get(system["id"])
    if refresh or not cached or cached[0] != system.get("updated"):
        cached = (system.get("updated"), system_links.scan_system(system["services"], system))
        SYSTEM_SCANS[system["id"]] = cached
    return cached[1]


def engine_python() -> str:
    return str(RUNTIME) if RUNTIME.is_file() else sys.executable


def load_projects() -> list[str]:
    try:
        items = json.loads(PROJECTS.read_text(encoding="utf-8-sig"))  # -sig: tolerate a BOM from hand edits
        return [p for p in items if isinstance(p, str)]
    except (OSError, ValueError):
        return []


def remember(project: Path) -> None:
    key = str(project)
    items = [p for p in load_projects() if os.path.normcase(p) != os.path.normcase(key)]
    PROJECTS.write_text(json.dumps([key, *items][:MAX_PROJECTS], indent=1), encoding="utf-8")


def line_number(location: str) -> int:
    """First line from a node location such as 'L42', 'L42-L60' or 'file.cs:42'."""
    digits = "".join(ch for ch in location.split(":")[-1].split("-")[0] if ch.isdigit())
    return max(1, int(digits)) if digits else 1


def graph_path(project: Path) -> Path:
    return project / "graphify-out" / "graph.json"


def previous_graph_path(project: Path) -> Path:
    return project / "graphify-out" / "graph.prev.json"


SKIP_DIRS = {".git", ".hg", ".svn", ".vs", ".idea", ".vscode", "node_modules", "bin", "obj", "dist", "build", "target",
             "vendor", "graphify-out", "__pycache__", ".venv", "venv", ".runtime", ".next", "coverage", "TestResults"}
_graph_files_cache: dict[str, tuple[float, set[str]]] = {}


def graph_source_files(project: Path) -> tuple[float, set[str]]:
    """(graph mtime, relative source files in the graph), cached until graph.json changes."""
    graph = graph_path(project)
    mtime = graph.stat().st_mtime
    cached = _graph_files_cache.get(str(project))
    if cached and cached[0] == mtime:
        return cached
    data = json.loads(graph.read_text(encoding="utf-8"))
    files = {str(n.get("source_file")).replace("\\", "/") for n in data.get("nodes", []) if n.get("source_file")}
    _graph_files_cache[str(project)] = (mtime, files)
    return mtime, files


def staleness(project: Path) -> dict:
    """Source files changed, added or deleted since graph.json was written (same file types as the graph)."""
    built, files = graph_source_files(project)
    suffixes = {Path(f).suffix.lower() for f in files if Path(f).suffix}
    changed: list[str] = []
    seen = 0
    for dirpath, dirnames, filenames in os.walk(project):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
        for name in filenames:
            if Path(name).suffix.lower() not in suffixes:
                continue
            seen += 1
            full = os.path.join(dirpath, name)
            try:
                if os.path.getmtime(full) > built + 1:
                    changed.append(Path(full).relative_to(project).as_posix())
            except OSError:
                pass
        if seen > 60_000:
            break
    deleted = sorted(f for f in files if Path(f).suffix.lower() in suffixes and not (project / f).exists())
    return {"built": built, "changed": len(changed), "deleted": len(deleted), "examples": sorted(changed)[:8] + deleted[:4]}


def graph_diff(project: Path) -> dict:
    """Nodes and links added/removed between graph.prev.json and graph.json."""
    prev_file = previous_graph_path(project)
    if not prev_file.is_file():
        return {"available": False}
    load = lambda p: json.loads(p.read_text(encoding="utf-8"))
    old, new = load(prev_file), load(graph_path(project))
    def index(data):
        nodes = {n["id"]: n for n in data.get("nodes", []) if "id" in n}
        links = {(l.get("source"), l.get("target"), l.get("relation")) for l in data.get("links", data.get("edges", []))}
        return nodes, links
    old_nodes, old_links = index(old)
    new_nodes, new_links = index(new)
    describe = lambda n: {"id": n["id"], "label": n.get("label", n["id"]), "file": n.get("source_file") or ""}
    added = [describe(new_nodes[i]) for i in sorted(new_nodes.keys() - old_nodes.keys())]
    removed = [describe(old_nodes[i]) for i in sorted(old_nodes.keys() - new_nodes.keys())]
    links_added, links_removed = new_links - old_links, old_links - new_links
    # Existing nodes whose connections changed: their calls/imports/references differ from the last build.
    touched = sorted({e for link in links_added | links_removed for e in link[:2] if e in new_nodes and e in old_nodes})
    label = lambda i: (new_nodes.get(i) or old_nodes.get(i) or {}).get("label", i)
    sample = lambda links: [f"{label(s)} --{r}--> {label(t)}" for s, t, r in sorted(links, key=str)[:40]]
    return {"available": True, "previous_built": prev_file.stat().st_mtime, "built": graph_path(project).stat().st_mtime,
            "added": added[:400], "removed": removed[:400], "added_count": len(added), "removed_count": len(removed),
            "links_added": len(links_added), "links_removed": len(links_removed),
            "links_added_sample": sample(links_added), "links_removed_sample": sample(links_removed), "touched": touched[:600]}


# ---- dashboard: activity log, project summary, local AI status, documentation ----
# Activity lives with the project (graphify-out/, which Token Graph git-ignores), the same
# place as its other query artifacts. Only events from this Explorer are recorded.
ACTIVITY_FILE = "token-graph-activity.jsonl"
LAST_BUILD_FILE = "token-graph-last-build.json"
MAX_ACTIVITY = 200
_activity_lock = threading.Lock()
_dashboard_cache: dict[str, tuple[float, dict]] = {}
_runtime_cache: dict[str, tuple[float, list[str]]] = {}
LOCAL_OLLAMA = "http://127.0.0.1:11434"


def record_activity(project: Path, kind: str, title: str, *, detail: str = "", ok: bool = True) -> None:
    """Append one event to the project's activity log; never fails the request that caused it."""
    entry = {"time": time.time(), "kind": kind, "title": title[:240], "detail": detail[:400], "ok": ok}
    path = project / "graphify-out" / ACTIVITY_FILE
    with _activity_lock:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            lines = path.read_text(encoding="utf-8").splitlines() if path.is_file() else []
            lines = [*lines[-(MAX_ACTIVITY - 1):], json.dumps(entry, ensure_ascii=False)]
            path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        except OSError:
            pass


def read_activity(project: Path, limit: int = 30) -> list[dict]:
    path = project / "graphify-out" / ACTIVITY_FILE
    items = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines() if path.is_file() else []
    except OSError:
        return []
    for line in reversed(lines):
        try:
            item = json.loads(line)
        except ValueError:
            continue
        if isinstance(item, dict) and isinstance(item.get("time"), (int, float)):
            items.append({k: item.get(k) for k in ("time", "kind", "title", "detail", "ok")})
        if len(items) >= limit:
            break
    return items


def clear_activity(project: Path) -> None:
    with _activity_lock:
        (project / "graphify-out" / ACTIVITY_FILE).unlink(missing_ok=True)


def cached_runtime_problems(*, ollama: bool = False) -> list[str]:
    """runtime_problems() starts a process; the dashboard polls, so reuse a recent answer."""
    key = "ai" if ollama else "core"
    cached = _runtime_cache.get(key)
    if cached and time.time() - cached[0] < 30:
        return cached[1]
    problems = runtime_problems(ollama=ollama)
    _runtime_cache[key] = (time.time(), problems)
    return problems


def project_summary(project: Path) -> dict:
    """Graph, language, architecture, quality and index figures (scripts/dashboard_engine.py),
    recomputed only when graph.json changes."""
    graph = graph_path(project)
    if not graph.is_file():
        raise ValueError("This project has no graph yet; build it first.")
    mtime = graph.stat().st_mtime
    key = os.path.normcase(str(project))
    cached = _dashboard_cache.get(key)
    if cached and cached[0] == mtime:
        return cached[1]
    try:
        result = subprocess.run([engine_python(), str(SCRIPTS / "dashboard_engine.py"), str(project)], cwd=project,
                                capture_output=True, text=True, encoding="utf-8", errors="replace",
                                creationflags=NO_WINDOW, timeout=300, check=False)
    except subprocess.TimeoutExpired:
        raise ValueError("Analysing this graph took longer than 5 minutes.") from None
    if result.returncode:
        raise ValueError((result.stderr.strip().splitlines() or ["The dashboard analysis failed."])[-1])
    summary = json.loads(result.stdout)
    first = cached is None or cached[0] != mtime
    _dashboard_cache[key] = (mtime, summary)
    if first:
        quality = summary.get("quality", {})
        cycles = quality.get("cycles", {})
        parts = [f"{len(quality.get('hotspots', []))} coupling hotspots"]
        parts.append(f"{cycles.get('count', 0)} import cycles" if cycles.get("supported") else "import cycles not applicable")
        parts.append(f"{quality.get('unconnected_symbols', 0)} unconnected symbols")
        last = read_activity(project, 1)
        if not (last and last[0].get("kind") == "analysis" and last[0].get("detail", "").endswith(f"@{mtime:.0f}")):
            record_activity(project, "analysis", "Code analysis: " + ", ".join(parts), detail=f"graph@{mtime:.0f}")
    return summary


def last_build(project: Path) -> dict | None:
    path = project / "graphify-out" / LAST_BUILD_FILE
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def dashboard(project: Path) -> dict:
    job = JOBS.status()
    if job and os.path.normcase(job.get("project", "")) != os.path.normcase(str(project)):
        job = None
    return {"summary": project_summary(project), "stale": staleness(project), "job": job,
            "last_build": last_build(project), "activity": read_activity(project),
            "problems": cached_runtime_problems()}


def _ollama_get(path: str, payload: dict | None = None, timeout: float = 3):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(LOCAL_OLLAMA + path, data=data, headers={"Content-Type": "application/json"} if data else {})
    with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req, timeout=timeout) as response:
        return json.load(response)


def ai_status() -> dict:
    """What the local model provider reports right now: reachability, models, what is loaded."""
    status = {"provider": "Ollama", "endpoint": LOCAL_OLLAMA, "reachable": False, "version": None,
              "chat_models": [], "embedding_models": [], "loaded": [], "error": "",
              "problems": cached_runtime_problems(ollama=True),
              "vector_index": {"available": False,
                               "reason": "Token Graph has no vector index yet; questions use graph traversal and keyword search."}}
    try:
        status["version"] = _ollama_get("/api/version").get("version")
        models = ollama_models()
        status["reachable"] = True
    except (OSError, ValueError) as error:
        status["error"] = str(error) or "Ollama is not responding."
        return status
    for name in models[:30]:
        try:
            capabilities = _ollama_get("/api/show", {"model": name}).get("capabilities") or []
        except (OSError, ValueError):
            capabilities = []
        is_embedding = "embedding" in capabilities or ("completion" not in capabilities and "embed" in name.lower())
        status["embedding_models" if is_embedding else "chat_models"].append(name)
    try:
        for item in _ollama_get("/api/ps").get("models", []):
            size, vram = int(item.get("size") or 0), int(item.get("size_vram") or 0)
            processor = "GPU" if size and vram >= size else "CPU" if not vram else f"{round(100 * vram / size)}% GPU"
            status["loaded"].append({"name": item.get("name"), "processor": processor, "expires": item.get("expires_at")})
    except (OSError, ValueError):
        pass
    return status


def generate_docs(project: Path) -> dict:
    """The engine's wiki export: one Markdown article per community and key symbol."""
    if not graph_path(project).is_file():
        raise ValueError("This project has no graph yet; build it first.")
    analysis = project / "graphify-out" / ".graphify_analysis.json"
    try:
        has_communities = bool(json.loads(analysis.read_text(encoding="utf-8")).get("communities"))
    except (OSError, ValueError, AttributeError):
        has_communities = False
    if not has_communities:  # graphs built with --no-cluster have no community data; the wiki export refuses without it
        subprocess.run([engine_python(), str(SCRIPTS / "token_graph.py"), "launch", str(project), "--action", "recluster"], cwd=project,
                       capture_output=True, text=True, encoding="utf-8", errors="replace",
                       creationflags=NO_WINDOW, timeout=600, check=False)
    result = subprocess.run([engine_python(), str(SCRIPTS / "token_graph.py"), "export", str(project), "wiki"], cwd=project,
                            capture_output=True, text=True, encoding="utf-8", errors="replace",
                            creationflags=NO_WINDOW, timeout=600, check=False)
    index = project / "graphify-out" / "wiki" / "index.md"
    if result.returncode or not index.is_file():
        raise ValueError((result.stdout + result.stderr).strip().splitlines()[-1] if (result.stdout + result.stderr).strip() else "Documentation export failed.")
    articles = sum(1 for _ in index.parent.glob("*.md")) - 1
    return {"file": index.relative_to(project).as_posix(), "articles": max(articles, 0)}


def build_warnings(text: str) -> list[str]:
    """Engine warnings worth surfacing (missing grammars, files without symbols, failed chunks)."""
    seen: list[str] = []
    for line in text.splitlines():
        clean = line.strip()
        if "warning" in clean.lower() and clean not in seen:
            seen.append(clean[:400])
    return seen[:20]


class Jobs:
    """One build at a time; the page polls its status."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.current: dict | None = None

    def start(self, project: Path, model: str | None = None) -> dict:
        # Refuse up front, with the command that fixes it, instead of failing mid-build.
        problems = runtime_problems(ollama=bool(model))
        if problems:
            raise ValueError("\n".join(problems))
        with self.lock:
            if self.current and self.current["state"] == "running":
                raise RuntimeError(f"A graph is already being built for {self.current['project']}.")
            job = {"id": secrets.token_hex(6), "project": str(project), "state": "running", "step": "starting",
                   "started": time.time(), "finished": None, "message": "", "mode": "", "model": model}
            self.current = job
        threading.Thread(target=self._run, args=(job, project), daemon=True).start()
        return dict(job)

    def status(self) -> dict | None:
        with self.lock:
            return dict(self.current) if self.current else None

    def _step(self, job: dict, project: Path, step: str, args: list[str]) -> subprocess.CompletedProcess:
        job["step"] = step
        return subprocess.run([engine_python(), str(SCRIPTS / "token_graph.py"), *args], cwd=project, capture_output=True,
                              text=True, encoding="utf-8", errors="replace", creationflags=NO_WINDOW, check=False)

    def _run(self, job: dict, project: Path) -> None:
        saved_graph = False
        result = None
        log = project / "graphify-out" / "token-graph.log"
        log_start = log.stat().st_size if log.is_file() else 0  # first builds write engine output here
        try:
            # Keep the previous graph so the page can show what changed (/api/diff).
            if graph_path(project).is_file():
                shutil.copy2(graph_path(project), previous_graph_path(project))
                saved_graph = True
            # An existing graph is updated incrementally; AI generation uses local semantic extraction.
            if job.get("model"):
                job["mode"] = "ai"
                result = self._step(job, project, "generating graph with local Ollama", ["build-local", str(project), "--model", job["model"]])
            elif graph_path(project).is_file():
                job["mode"] = "update"
                result = self._step(job, project, "updating graph (changed files only)", ["update", str(project)])
            else:
                job["mode"] = "build"
                result = self._step(job, project, "building graph", ["launch", str(project)])
            if result.returncode or not graph_path(project).is_file():
                tail = (result.stdout + result.stderr).strip().splitlines()[-6:]
                raise RuntimeError("\n".join(tail) or "Graph build failed; see graphify-out/token-graph.log in the project.")
            # The context-reduction strip in the page reads benchmark.json; a failure here does not fail the build.
            self._step(job, project, "estimating context reduction", ["launch", str(project), "--action", "benchmark"])
            remember(project)
            job["state"] = "done"
            job["step"] = "done"
        except Exception as error:  # reported to the page, never swallowed
            if job.get("model") and saved_graph:
                try:
                    shutil.copy2(previous_graph_path(project), graph_path(project))
                except OSError as restore_error:
                    error = RuntimeError(f"{error}\nCould not restore the previous graph: {restore_error}")
            job["state"] = "error"
            job["message"] = str(error)
        finally:
            job["finished"] = time.time()
            self._report(job, project, result, log, log_start)

    @staticmethod
    def _report(job: dict, project: Path, result, log: Path, log_start: int) -> None:
        """Keep this build's outcome and engine warnings for the dashboard."""
        output = (result.stdout + "\n" + result.stderr) if result is not None else ""
        try:
            if log.is_file() and log.stat().st_size > log_start:
                with log.open("rb") as stream:
                    stream.seek(log_start)
                    output += "\n" + stream.read(2_000_000).decode("utf-8", errors="replace")
        except OSError:
            pass
        ok = job.get("state") == "done"
        nodes = edges = None
        if ok:
            try:
                data = json.loads(graph_path(project).read_text(encoding="utf-8"))
                nodes, edges = len(data.get("nodes", [])), len(data.get("links", data.get("edges", [])))
            except (OSError, ValueError, AttributeError):
                pass
        summary = {"mode": job.get("mode"), "model": job.get("model"), "ok": ok, "started": job.get("started"),
                   "finished": job.get("finished"), "nodes": nodes, "edges": edges,
                   "warnings": build_warnings(output), "message": "" if ok else job.get("message", "")[:2000]}
        try:
            (project / "graphify-out").mkdir(parents=True, exist_ok=True)
            (project / "graphify-out" / LAST_BUILD_FILE).write_text(json.dumps(summary, indent=1, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass
        label = {"ai": "AI graph generation", "update": "Graph update", "build": "Graph build"}.get(job.get("mode"), "Graph build")
        seconds = round((job.get("finished") or time.time()) - (job.get("started") or time.time()))
        if ok:
            detail = f"{nodes:,} nodes, {edges:,} connections · {seconds}s" if nodes is not None else f"{seconds}s"
            if summary["warnings"]:
                detail += f" · {len(summary['warnings'])} warning(s)"
            record_activity(project, job.get("mode") or "build", f"{label} finished", detail=detail)
        else:
            record_activity(project, job.get("mode") or "build", f"{label} failed",
                            detail=(job.get("message") or "").splitlines()[-1][:300] if job.get("message") else "", ok=False)


JOBS = Jobs()


class Handler(BaseHTTPRequestHandler):
    server_version = "TokenGraphExplorer/1"
    token = ""
    port = 0

    def log_message(self, *_args) -> None:  # no console under pythonw
        pass

    # ---- plumbing -------------------------------------------------------
    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: int, payload) -> None:
        self._send(status, json.dumps(payload, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    def _host_ok(self) -> bool:
        # Rejects DNS-rebinding: the page must be addressed as this loopback server.
        return self.headers.get("Host", "") in {f"127.0.0.1:{self.port}", f"localhost:{self.port}"}

    def _authorized(self) -> bool:
        return self._host_ok() and secrets.compare_digest(self.headers.get("X-Token-Graph", ""), self.token)

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if length < 0 or length > 100_000:
            raise ValueError("Request too large")
        body = json.loads(self.rfile.read(length) or b"{}")
        if not isinstance(body, dict):
            raise ValueError("Request body must be a JSON object")
        return body

    def _project(self, raw: str | None) -> Path:
        if not raw or "://" in raw:
            raise ValueError("A local project folder is required.")
        path = Path(raw).expanduser().resolve()
        if not path.is_dir():
            raise ValueError(f"Folder does not exist: {path}")
        return path

    # ---- routes ---------------------------------------------------------
    def do_GET(self) -> None:
        url = urlparse(self.path)
        if not self._host_ok():
            return self._send(403, b"Forbidden", "text/plain")
        if url.path in ("/", "/index.html"):
            return self._send(200, PAGE.read_bytes(), "text/html; charset=utf-8")
        if not url.path.startswith("/api/"):
            return self._send(404, b"Not found", "text/plain")
        if not self._authorized():
            return self._json(403, {"error": "Missing or wrong token. Reopen the Token Graph shortcut."})
        query = {k: v[0] for k, v in parse_qs(url.query).items()}
        try:
            if url.path == "/api/ping":
                return self._json(200, {"ok": True, "code": CODE_VERSION})
            if url.path == "/api/projects":
                items = [{"path": p, "name": Path(p).name, "has_graph": graph_path(Path(p)).is_file()} for p in load_projects()]
                return self._json(200, {"projects": items})
            if url.path == "/api/status":
                return self._json(200, {"job": JOBS.status()})
            if url.path == "/api/stale":
                return self._json(200, staleness(self._known_project(query.get("project"))))
            if url.path == "/api/diff":
                return self._json(200, graph_diff(self._known_project(query.get("project"))))
            if url.path == "/api/editors":
                return self._json(200, {"vscode": has_vscode(), "rider": find_rider() is not None})
            if url.path == "/api/agent/status":
                return self._json(200, {"models": ollama_models()})
            if url.path == "/api/claude-agents":
                project = self._known_project(query['project']) if query.get('project') else None
                return self._json(200, {"agents": claude_agents(project)})
            if url.path == "/api/dashboard":
                return self._json(200, dashboard(self._known_project(query.get("project"))))
            if url.path == "/api/folders":
                return self._json(200, list_folders(query.get("path")))
            if url.path == "/api/savings":
                # Tokens saved by graph answers about this project in recent Claude Code sessions,
                # estimated from the transcripts on this computer (no model involved).
                from session_savings import recent_savings
                keep = ("session", "updated", "title", "folder", "started", "last_prompt", "graph_answers", "files", "graph_tokens", "read_tokens",
                        "without_graph_tokens", "with_graph_tokens", "saved_tokens", "saved_percent")
                sessions = recent_savings(self._known_project(query.get("project")))
                return self._json(200, {"sessions": [{k: s[k] for k in keep} for s in sessions]})
            if url.path == "/api/ai/status":
                return self._json(200, ai_status())
            if url.path == "/api/graph":
                project = self._project(query.get("project"))
                graph = graph_path(project)
                if not graph.is_file():
                    return self._json(404, {"error": f"No graph yet for {project}."})
                data = json.loads(graph.read_text(encoding="utf-8"))
                bench = project / "graphify-out" / "benchmark.json"
                if not data.get("benchmark") and bench.is_file():
                    try:
                        data["benchmark"] = json.loads(bench.read_text(encoding="utf-8"))
                    except ValueError:
                        pass
                data["project_name"] = project.name
                data["project_path"] = str(project)
                data["graph_mtime"] = graph.stat().st_mtime
                remember(project)
                return self._json(200, data)
            if url.path == "/api/source":
                return self._source(query)
            if url.path == "/api/systems":
                return self._json(200, {"systems": system_links.load_systems()})
        except (ValueError, OSError) as error:
            return self._json(400, {"error": str(error)})
        return self._json(404, {"error": "Unknown endpoint"})

    def do_POST(self) -> None:
        if not self._authorized():
            return self._json(403, {"error": "Missing or wrong token. Reopen the Token Graph shortcut."})
        url = urlparse(self.path)
        try:
            body = self._body()
            if url.path == "/api/pick":
                start = body.get("start") or (load_projects() or [str(Path.home())])[0]
                start = str(Path(start).parent) if Path(start).is_dir() else str(Path.home())
                return self._json(200, pick_folder(start))
            if url.path == "/api/build":
                project = self._project(body.get("project"))
                return self._json(200, {"job": JOBS.start(project)})
            if url.path == "/api/agent/build":
                project = self._known_project(body.get("project"))
                model = str(body.get("model") or "")
                if model not in ollama_models():
                    raise ValueError("Select an installed local Ollama model first.")
                return self._json(200, {"job": JOBS.start(project, model)})
            if url.path == "/api/ask":
                project = self._known_project(body.get("project"))
                label = {"query": "Question", "path": "Path", "affected": "Impact"}.get(body.get("mode"), "Question")
                text = " → ".join(t for t in (str(body.get("a") or "").strip(), str(body.get("b") or "").strip()) if t) if body.get("mode") == "path" else str(body.get("a") or "").strip()
                try:
                    answer = ask(body, project)
                except ValueError as error:
                    record_activity(project, "question", f"{label}: {text}", detail=str(error), ok=False)
                    raise
                record_activity(project, "question", f"{label}: {text}", detail=f"{len(answer.get('nodes') or {})} matching nodes")
                return self._json(200, answer)
            if url.path == "/api/agent":
                project = self._known_project(body.get("project"))
                question, model = str(body.get("question") or "").strip(), str(body.get("model") or "")
                try:
                    answer = agent_answer(body, project)
                except ValueError as error:
                    record_activity(project, "ai_question", question, detail=str(error), ok=False)
                    raise
                record_activity(project, "ai_question", question, detail=f"Answered locally by {model}")
                return self._json(200, answer)
            if url.path == "/api/llm":
                project = self._known_project(body.get("project"))
                question, model = str(body.get("question") or "").strip(), str(body.get("model") or "")
                try:
                    answer = llm_answer(body, project)
                except ValueError as error:
                    record_activity(project, "ai_question", question, detail=str(error), ok=False)
                    raise
                record_activity(project, "ai_question", question, detail=f"Answered locally by {model} in {answer['seconds']}s")
                return self._json(200, answer)
            if url.path == "/api/docs":
                project = self._known_project(body.get("project"))
                try:
                    docs = generate_docs(project)
                except ValueError as error:
                    record_activity(project, "docs", "Documentation export failed", detail=str(error), ok=False)
                    raise
                record_activity(project, "docs", "Documentation generated", detail=f"{docs['articles']} articles · {docs['file']}")
                return self._json(200, docs)
            if url.path == "/api/systems/save":
                system = system_links.save_system(body)
                known = {os.path.normcase(p) for p in load_projects()}
                new = [s["path"] for s in system["services"] if os.path.normcase(s["path"]) not in known]
                if new:  # known projects (source preview, editors); keeps the user's most recent project first
                    items = load_projects()[:max(0, MAX_PROJECTS - len(new))] + new[:MAX_PROJECTS]
                    PROJECTS.write_text(json.dumps(items, indent=1), encoding="utf-8")
                SYSTEM_SCANS.pop(system["id"], None)
                return self._json(200, {"system": system})
            if url.path == "/api/systems/delete":
                system_links.delete_system(str(body.get("id") or ""))
                SYSTEM_SCANS.pop(str(body.get("id") or ""), None)
                return self._json(200, {"ok": True})
            if url.path == "/api/systems/discover":
                return self._json(200, {"services": system_links.discover_services(self._project(body.get("path")))})
            if url.path == "/api/systems/scan":
                system = system_links.get_system(str(body.get("id") or ""))
                return self._json(200, {"system": system, **system_scan(system, bool(body.get("refresh")))})
            if url.path == "/api/systems/graph":
                system = system_links.get_system(str(body.get("id") or ""))
                return self._json(200, system_links.system_graph(system["name"], system_scan(system, False)))
            if url.path == "/api/activity/clear":
                clear_activity(self._known_project(body.get("project")))
                return self._json(200, {"ok": True})
            if url.path == "/api/open":
                editor = body.get("editor")
                if editor not in ("vscode", "rider"):
                    raise ValueError("Unknown editor")
                file = self._project_file(body.get("project"), body.get("file", ""))
                open_in_editor(editor, file, line_number(str(body.get("line", ""))))
                return self._json(200, {"ok": True})
            if url.path == "/api/forget":
                key = os.path.normcase(str(Path(body.get("project", "")).resolve()))
                PROJECTS.write_text(json.dumps([p for p in load_projects() if os.path.normcase(p) != key], indent=1), encoding="utf-8")
                return self._json(200, {"ok": True})
            if url.path == "/api/shutdown":
                job = JOBS.status()
                if job and job["state"] == "running":
                    # Stopping now would leave a half-written graph behind.
                    raise RuntimeError(f"A graph is being built for {Path(job['project']).name}; stop the server after it finishes.")
                self._json(200, {"ok": True})
                threading.Thread(target=self.server.shutdown, daemon=True).start()
                return None
        except RuntimeError as error:
            return self._json(409, {"error": str(error)})
        except (ValueError, OSError) as error:
            return self._json(400, {"error": str(error)})
        return self._json(404, {"error": "Unknown endpoint"})

    def _known_project(self, raw_project: str | None) -> Path:
        project = self._project(raw_project)
        if os.path.normcase(str(project)) not in {os.path.normcase(p) for p in load_projects()}:
            raise PermissionError("Unknown project")
        return project

    def _project_file(self, raw_project: str | None, raw_file: str) -> Path:
        """A file inside a project this server knows; anything else is refused."""
        project = self._known_project(raw_project)
        file = (project / raw_file).resolve()
        try:
            file.relative_to(project)
        except ValueError:
            raise PermissionError("File is outside the project") from None
        if not file.is_file():
            raise FileNotFoundError("Source unavailable")
        return file

    def _source(self, query: dict) -> None:
        """A few lines around a node, only from files inside a project this server knows."""
        try:
            file = self._project_file(query.get("project"), query.get("file", ""))
        except PermissionError as error:
            return self._json(403, {"error": str(error)})
        except FileNotFoundError as error:
            return self._json(404, {"error": str(error)})
        if file.stat().st_size > 2_000_000:
            return self._json(404, {"error": "Source unavailable"})
        line = line_number(query.get("line", ""))
        lines = file.read_text(encoding="utf-8", errors="replace").splitlines()
        start = max(1, line - 3)
        return self._json(200, {"start": start, "lines": lines[start - 1:start + 29]})


def existing_server() -> str | None:
    """URL of a server already running this same code.

    A server started from older scripts (no or a different code version) is asked to stop and
    None is returned so a fresh one starts. While it is building a graph it refuses, and is reused.
    """
    try:
        state = json.loads(STATE.read_text(encoding="utf-8"))
        base, headers = f"http://127.0.0.1:{state['port']}", {"X-Token-Graph": state["token"]}
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(urllib.request.Request(base + "/api/ping", headers=headers), timeout=2) as response:
            ping = json.loads(response.read() or b"{}")
    except Exception:
        return None
    url = f"{base}/?token={state['token']}"
    if ping.get("code") == CODE_VERSION:
        return url
    try:
        stop = urllib.request.Request(base + "/api/shutdown", data=b"{}", method="POST",
                                      headers={**headers, "Content-Type": "application/json"})
        with opener.open(stop, timeout=5):
            pass
    except Exception:
        return url
    for _ in range(50):  # the old process removes its state file as it exits
        if not STATE.exists():
            break
        time.sleep(0.1)
    return None


def main() -> int:
    url = existing_server()
    if url:
        if "--no-browser" not in sys.argv:
            webbrowser.open(url)
        else:
            print(url, flush=True)
        return 0
    Handler.token = secrets.token_urlsafe(24)
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    Handler.port = server.server_address[1]
    STATE.write_text(json.dumps({"port": Handler.port, "token": Handler.token, "pid": os.getpid()}), encoding="utf-8")
    url = f"http://127.0.0.1:{Handler.port}/?token={Handler.token}"
    if "--no-browser" not in sys.argv:
        webbrowser.open(url)
    else:
        print(url, flush=True)
    try:
        server.serve_forever()
    finally:
        try:  # only remove the state file if a newer server has not replaced it
            if json.loads(STATE.read_text(encoding="utf-8")).get("token") == Handler.token:
                STATE.unlink()
        except (OSError, ValueError):
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
