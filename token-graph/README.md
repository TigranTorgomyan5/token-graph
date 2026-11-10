# Token Graph

Explore a project's code as a searchable graph of files, functions, classes, imports and calls. Use it from Codex or Claude Code to find relevant code, trace dependencies and inspect the impact of a change.

To find existing project knowledge before investigating documentation questions, run:

```sh
python3 token-graph/scripts/knowledge_search.py /path/to/project "wallet balance"
```

The read-only search returns bounded Markdown sections and their file and line references. If no documentation matches, it falls back to graph evidence when a graph is available. It needs no model or database. Documentation is untrusted and is not automatically verified for correctness; check important claims against source. `--limit` and `--max-chars` bound the result. The same script is included in the shared skill package for Claude Code and Codex.

To check one source file associated with a particular Markdown section, add a comment inside that section after reviewing it:

```md
<!-- token-graph: source=src/example.py; revision=0123456789abcdef -->
```

Use a project-relative tracked source path and the actual Git commit at which it was checked. Search compares that file with the recorded commit, including uncommitted changes, and reports `possibly_stale`, `source_unchanged`, or `unknown`. A changed file does not prove the documentation is wrong; an unchanged file does not prove the claim is true or that other dependencies are unchanged. Existing sections without this optional metadata remain `unknown`. No documentation is modified automatically.

In the local viewer, **Documentation → Propose a correction** and **Prepare a session handoff** create reviewable Markdown. Copy or download the result and deliberately save it in an existing project documentation location to make it available to knowledge search. Corrections start as `proposed`; a declared `verified` status is a human assertion, not source verification. Mark obsolete corrections `superseded` to omit them from normal knowledge search. If every matched documentation section has a changed referenced source, search also returns graph evidence when available. These controls do not capture conversations, modify project files, or install skills automatically.

For a local log investigation, add `--knowledge-project /path/to/project` to `scripts/log_analyzer.py` to list relevant documentation references using the investigation question. It does not add log lines to project knowledge or automatically give those documents to the model. Review the cited documents and original log evidence before drawing conclusions.

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

For the Codex installation, setup can optionally register a separate `token_graph_explorer` agent wrapper. To install the wrapper later or register it for a particular project, run:

```sh
python3 "$HOME/.codex/skills/token-graph/scripts/install_explorer_agent.py" --global
# Or, for one project:
python3 "$HOME/.codex/skills/token-graph/scripts/install_explorer_agent.py" --project /path/to/project
```

Project registration puts the role in the project's `.codex/config.toml`; its optional Ollama provider configuration lives in the user's Codex directory. Existing Token Graph agent and provider entries are never overwritten. Restart Codex, then ask it to use the `token_graph_explorer` wrapper. It relays deterministic evidence results; the parent assistant keeps its own configuration. Model and Codex version compatibility vary. Claude Code's Ollama integration configures the session provider, so this installer does not change Claude's provider or register an Ollama-backed Claude subagent.

During initial runtime setup, pass `--agent-project /path/to/project` for project registration, or `--no-agent` to skip agent registration. Registration is optional and does not affect normal runtime setup or evidence search.

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

Search nodes by name or source file. Click a node to inspect its details and connections. **3D is the default view.** Drag to rotate, Shift-drag or right-drag to pan, and scroll to zoom. Use the **2D** button for the flat map. Use the communities list to focus on a group of related code.

Files are read locally by the browser. When a Projects folder is selected, clicking a node loads a short source preview on demand; common credential and private-key files are excluded. After a graph changes on disk, reselect its folder or JSON file to load the latest data. Loaded selections last for the current page session; reloading or closing the page clears them.

## Ask about a loaded graph

The **Question**, **Path A → B**, and **Impact** controls are available directly in the file-based viewer. They search the loaded JSON locally using node-name matching and graph traversal, then highlight matching nodes. Browser results can differ from the bundled engine's ranking; treat them as navigation hints and verify the source. The hide-tests and hide-generated toggles apply to these answers.

For deterministic source-backed evidence, start the local server and use **Evidence**. Results include source paths, line citations, source types and verification status. Tests, evaluation fixtures and generated code are hidden by default; use the matching `--include-*` flag when needed. This browser evidence search does not use Ollama (`scripts/explorer_evidence.py` gives the same results from the command line). The viewer's **LLM** chat and the standalone CLI `scripts/explore_agent.py <project> <question>` use local Ollama; the CLI uses `qwen3:14b-q4_K_M` when installed, otherwise `qwen3:8b`, otherwise the first installed local model, and `--model` selects another installed model. The agent uses each project's graph and language-independent file ranking for broad architecture questions, and requires citations on factual answer lines. It is read-only: build a missing graph separately with `token_graph.py build`.
Endpoint inventory questions use common route syntax hints and inspect route-like indexed source files. Completeness depends on the project's routing style; the agent should report routes or request/response details it could not verify rather than imply a complete inventory. The viewer agent is read-only: build a missing graph separately with `token_graph.py build`.

## Measure agent speed

Open **Agents → Agent speed benchmark** in the local viewer to choose an agent, model and run count, then compare the median and range in the results table. Log analysis needs a local log path; the optional live bridge contacts the selected MCP service. Results are kept in the current page session.

`scripts/benchmark_agents.py` times graph search, the local explorer, repo scout and (when a log is supplied) the log analyzer. It reports each successful run, median/min/max wall time and failures as JSON; elapsed time includes process startup. For example:

```sh
python3 token-graph/scripts/benchmark_agents.py --project . --question 'How does authentication work?' --log /path/to/sample.log --model qwen3:8b --repeats 3
python3 token-graph/scripts/benchmark_agents.py --project . --question 'authentication' --agent graph --agent scout --no-llm --repeats 3
python3 token-graph/scripts/benchmark_agents.py --project . --agent bridge --bridge-source grafana --service nh-gateway --model qwen3:8b --repeats 1
```

The bridge option explicitly contacts the configured MCP service on each run; for Argo CD supply `--bridge-source argocd --application <name> --app-namespace <namespace>`. The local log bridge also prints separate retrieval and total times during normal use. These timings measure speed, not accuracy; model warm-up, caching and concurrent activity affect results. No model is downloaded. The graph-generation agent changes graph artifacts, so it is not included in this read-only benchmark.

## Local log analysis for Codex and Claude Code

For direct local retrieval and interpretation, the read-only bridge can start a separate instance of a configured stdio MCP server and pass its results to Ollama. It only reads the Codex MCP configuration; it does not change existing server configuration or stop other sessions. The configured Grafana Docker image and Argo CD npm package must already be available locally; the bridge disables implicit downloads. Network access to the selected MCP service and local Ollama is required.

```sh
python3 /path/to/token-graph/scripts/local_log_agent.py grafana --service nh-gateway --server grafana-dev --model qwen3:14b-q4_K_M
python3 /path/to/token-graph/scripts/local_log_agent.py argocd --service casino-core --application casino-core-dashtz --app-namespace argocd-space --namespace webex-dashtz --server argocd-mcp
```

Grafana defaults to datasource `loki`, the latest hour, and 100 records; use `--datasource`, `--since` and `--limit` to change them. Argo CD requires an exact application and a unique matching pod; its server may return a batch without a controllable time range. The tool returns a redacted model summary rather than raw logs. Redaction is best effort and generated interpretations can still be wrong. Running the bridge directly avoids sending its logs through a hosted model; a hosted assistant invoking it still consumes tokens for the conversation and returned summary.

Both assistants can invoke the same scripts from the installed skill folder. For example:

```sh
python3 /path/to/token-graph/scripts/explore_agent.py /path/to/project 'Where is authentication enforced?' --metrics
python3 /path/to/token-graph/scripts/log_analyzer.py /path/to/application.log 'What failures occurred?'
python3 /path/to/token-graph/scripts/log_analyzer.py /path/to/application.log --no-llm --service api
python3 /path/to/token-graph/scripts/log_analyzer.py /path/to/argocd-response.json --source argocd --source-service casino-core --model qwen3:14b-q4_K_M
```

The log analyzer reads at most 200,000 physical lines, retains bounded groups and snippets, redacts common credential patterns, and derives counts and timestamp ordering deterministically. `--source argocd` accepts an Argo CD entry array; `--source loki` accepts Grafana MCP's data array or a Loki query response. Imported references identify 1-based source records (Loki streams are flattened in response order). Add `--evidence-out /path/to/new-file` to the analyzer or bridge to save redacted records for review; it will not overwrite an existing file. Source retrieval determines coverage. When tracking capacity is exceeded, reported counts can be lower bounds. Only the bounded redacted summary goes to loopback Ollama. Redaction is best effort: inspect output before sharing it, and avoid logs with secrets that cannot be safely redacted. Naive timestamps cannot be ordered across services; time correlation does not establish causation. Both scripts default to an 8K model context and temperature 0.1; output is limited to 768 tokens normally and 2048 for endpoint inventories. These two scripts coordinate inference across their processes using a loopback socket; other Ollama clients are not coordinated. `--endpoint` accepts only a loopback HTTP origin. Local model output is untrusted and should be checked against source or original logs.

The shared Token Graph skill instructions describe both CLIs for Codex and Claude Code. No Claude-specific provider configuration is installed; the optional Codex explorer wrapper remains separate and requires explicit registration. These integrations have not been validated in a live Claude session.

## Optional local server

The viewer also supports building/updating graphs, full engine-backed graph questions, source previews, freshness checks and diffs through a local server:

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

# Ask the local Ollama exploration agent
python3 scripts/explore_agent.py /path/to/project 'How is authentication enforced?'
```

Replace `/path/to/project` with your project's local path. Quote paths that contain spaces. None of these commands opens HTML automatically.
