#!/usr/bin/env python3
"""Local Project Explorer server: pick a project folder, build/update its graph, view it.

Listens on 127.0.0.1 only. Every /api call must carry the per-run token in the
X-Token-Graph header, so another web page in the same browser cannot trigger a
build or read files. Running it again reuses the server that is already up.
"""
from __future__ import annotations
import json
import os
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
import explore_agent
PAGE = SKILL / "assets" / "project-explorer.html"
STATE = SKILL / ".explorer-server.json"      # port + token of the running server
PROJECTS = SKILL / ".explorer-projects.json"  # projects built or opened here, newest first
RUNTIME = SKILL / ".runtime" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
MAX_PROJECTS = 50

PICK_FOLDER = r"""
import sys, tkinter
from tkinter import filedialog
root = tkinter.Tk(); root.withdraw(); root.attributes('-topmost', True)
path = filedialog.askdirectory(title='Choose a project folder for Token Graph', initialdir=sys.argv[1] or None, mustexist=True)
root.destroy()
sys.stdout.write(path or '')
"""


def pick_folder(start: str) -> dict:
    picked = subprocess.run([sys.executable.replace("pythonw.exe", "python.exe"), "-I", "-c", PICK_FOLDER, start],
                            capture_output=True, text=True, encoding="utf-8", creationflags=NO_WINDOW, check=False)
    if picked.returncode:
        return {"path": "", "manual": True}
    path = picked.stdout.strip()
    return {"path": str(Path(path).resolve()) if path else "", "manual": False}


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
    req = urllib.request.Request('http://127.0.0.1:11434/api/tags')
    try:
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req, timeout=3) as response:
            data = json.load(response)
    except (OSError, ValueError) as error:
        raise ValueError('Ollama is not responding. Start the Ollama app and try again.') from error
    return [item['name'] for item in data.get('models', [])
            if isinstance(item, dict) and isinstance(item.get('name'), str)
            and ':cloud' not in item['name'].lower() and not item.get('remote_model') and not item.get('remote_host')]


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


class Jobs:
    """One build at a time; the page polls its status."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.current: dict | None = None

    def start(self, project: Path, model: str | None = None) -> dict:
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
                return self._json(200, {"ok": True})
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
                return self._json(200, ask(body, self._known_project(body.get("project"))))
            if url.path == "/api/agent":
                return self._json(200, agent_answer(body, self._known_project(body.get("project"))))
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
    """URL of a server already running for this skill, if it still answers."""
    try:
        state = json.loads(STATE.read_text(encoding="utf-8"))
        request = urllib.request.Request(f"http://127.0.0.1:{state['port']}/api/ping", headers={"X-Token-Graph": state["token"]})
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(request, timeout=2) as response:
            if response.status == 200:
                return f"http://127.0.0.1:{state['port']}/?token={state['token']}"
    except Exception:
        return None
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
        STATE.unlink(missing_ok=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
