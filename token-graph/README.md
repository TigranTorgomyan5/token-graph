# Token Graph

Explore a project's code as a searchable graph of files, functions, classes, imports and calls. Use it from Codex or Claude Code to find relevant code, trace dependencies and inspect the impact of a change.

**You do not need to install Graphify, create an account, or run a Graphify server.** The local engine is bundled. Normal code analysis needs no Ollama or separate AI model.

## Quick start

### 1. Install the skill

Extract `token-graph.zip`. Copy the complete `token-graph` folder into the directory for your assistant:

| Assistant | Skill directory | How to invoke |
|---|---|---|
| Codex | `~/.codex/skills/token-graph/` | `$token-graph` |
| Claude Code | `~/.claude/skills/token-graph/` | `/token-graph` |

If you use both assistants, copy the folder to both locations and run setup for each copy. Keep the folder contents together, including `vendor`, `scripts`, `assets` and `SKILL.md`.

### 2. Prepare the dependencies once

You need Python 3.11 or newer with `venv` and pip. Python 3.14 is the tested version. Run the command for the assistant you installed:

**Codex:**

```sh
python3 "$HOME/.codex/skills/token-graph/scripts/setup_runtime.py" --download-deps
```

**Claude Code:**

```sh
python3 "$HOME/.claude/skills/token-graph/scripts/setup_runtime.py" --download-deps
```

Setup creates a private `.runtime` folder and downloads the parser libraries from PyPI. It does not install Graphify, download AI models, or read/upload your project files. You do not need uv.

For offline installation from local wheels, Windows notes, and optional features, see [INSTALL.md](INSTALL.md). This release was tested on macOS with Python 3.14; Windows and Linux have not been verified.

### 3. Use the skill in your project

Open your project in your assistant and invoke the skill. For example, in Codex:

```text
$token-graph build a graph for this project
```

In Claude Code:

```text
/token-graph build a graph for this project
```

The assistant runs the bundled tools and returns the generated file paths. HTML is left for you to open manually. If a newly installed skill is not visible, start a new conversation or restart the assistant application.

## Things you can ask

Use these requests after invoking the skill:

- “Explain how authentication works in this project.”
- “Find the callers of PaymentService.”
- “Trace the path from the API handler to the wallet adapter.”
- “What could be affected if I change this function?”
- “Show the most connected parts of this codebase.”
- “Update the graph after my code changes.”
- “Calculate the context reduction estimate.”
- “Export this graph as SVG or GraphML.”

Each project keeps its own graph. Projects are not merged unless you explicitly request a merge.

## View all your projects in one place

1. Open `assets/project-explorer.html` from the installed skill folder in your browser.
2. Click **Choose Projects folder** and select the directory containing your projects.
3. Use the **Projects** dropdown to switch between discovered graphs.

The viewer finds existing `graphify-out/graph.json` files, including graphs created previously with Graphify. You can also use **Add graph JSON files** to load selected files from different locations.

Search nodes by name or source file. Click a node to inspect its details and connections. Drag to pan and scroll to zoom. Use the communities list to focus on a group of related code.

Files are read locally by the browser. After a graph changes on disk, reselect its folder or JSON file to load the latest data. Loaded selections last for the current page session; reloading or closing the page clears them.

## Optional local server

The viewer also supports building/updating graphs, graph questions with highlighted answers, source previews, freshness checks and diffs through a local server:

```sh
python3 /path/to/token-graph/scripts/explorer_server.py --no-browser
```

Open the printed loopback URL manually. If the native folder picker is unavailable, the page lets you enter the project path instead. It contains a per-run access token; keep that URL private. All requests stay on your computer. The server does not require Graphify, Ollama or an AI model. The server's **Ask** tool searches graph relationships; it is not an AI chat.

You can hide tests or generated code, group by folder, and focus on a node's neighbours. The assistant's graph answers exclude generated code by default; use `--include-generated` for generated-code questions. Tests remain included unless `--no-tests` is requested. The usual query budget is 800 estimated tokens; raise it if the needed information is truncated.

Windows users can optionally create a desktop shortcut; see [INSTALL-WINDOWS.md](INSTALL-WINDOWS.md). The shortcut opens the browser when clicked. Starting the skill's normal graph commands still never opens a browser.

## Generated files

The main files are stored inside your project:

```text
graphify-out/
  graph.json          Graph data, plus benchmark metadata when available
  graph.html          Interactive viewer with this graph embedded
  GRAPH_REPORT.md     Graph summary
  benchmark.json      Estimated context savings
```

Additional commands may create exports, logs, or other result files. The `graphify-out` name is retained for compatibility with older graphs; no Graphify service is involved. Generated output is excluded from Git by the launcher.

An existing graph can be queried immediately. Run an update after code changes, or request a fresh build when you want it rebuilt. For graphs created by other tools, old benchmark metadata may be absent after an extraction; calculate the benchmark again if necessary.

## Context reduction and token estimates

The benchmark compares an estimated full-project text size with estimated graph-query context size. Both are local heuristics. These numbers are **not** actual Codex or Claude token usage, billing totals, or guaranteed savings.

If the viewer says **Not included**, ask the skill to calculate the benchmark for that project, then reload the JSON or reselect the Projects folder. Small projects can have negative estimated savings.

## Privacy and optional models

Code extraction, relationships, communities, traversal, and the viewer operate locally. The bundled analysis runner blocks external network connections and hosted model backends. Dependency installation is a separate, explicitly requested setup step.

Your Codex or Claude conversation still uses that assistant's normal service. Prompts and selected graph-query output are part of the conversation and may be sent to its provider. Token Graph does not make a hosted assistant conversation fully offline.

If you explicitly request semantic mapping of documents or images, the invoking assistant can interpret them using its existing model. Local Ollama and cached media transcription are optional alternatives with additional local dependencies. They are not needed for the normal code workflow. See [INSTALL.md](INSTALL.md) for optional setup flags.

## Troubleshooting

| Problem | What to do |
|---|---|
| `python3` is missing or too old | Install Python 3.11+ with venv/pip support. Python 3.14 is the tested version. |
| Missing Python module or runtime | Run the bundled `setup_runtime.py --download-deps` command for that installed copy. |
| Offline setup cannot find dependencies | Supply compatible wheels with `--wheel-dir`, or explicitly use `--download-deps`. No silent download is attempted. |
| No projects appear in the viewer | Select a folder containing projects with `graphify-out/graph.json`, or add graph JSON files directly. Build a graph first if none exists. |
| A project is missing recent code changes | Ask the skill to update its graph, then reselect the folder or JSON in the viewer. |
| Context reduction says “Not included” | Calculate that project's benchmark and reload its data. |
| A symbol name is ambiguous | Include its source path, such as `src/payments.py::charge`, or use the exact node ID. |
| An optional parser or document feature is unavailable | Install the corresponding extra with the bundled setup script; see [INSTALL.md](INSTALL.md). No Graphify installation is needed. |

## Manual terminal use

The assistant normally runs these commands for you. If needed, from inside the skill folder:

```sh
# Create or load graph artifacts for a project
python3 scripts/token_graph.py launch /path/to/project

# Query an existing graph
python3 scripts/token_graph.py query /path/to/project "authentication flow" --budget 1500

# Update after code changes
python3 scripts/token_graph.py update /path/to/project

# Calculate and save a context reduction estimate
python3 scripts/token_graph.py benchmark /path/to/project
```

Replace `/path/to/project` with your project's local path. Quote paths that contain spaces. None of these commands opens HTML automatically.
