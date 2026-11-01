# Token Graph

A local code knowledge graph skill for **Codex** and **Claude Code**, with an offline, interactive 3D multi-project viewer.

Build graphs of files, classes, functions, imports and calls. Query the relevant code, trace relationships, inspect change impact, and explore projects from one place.

**No separate Graphify installation, account, or server is required.** The engine source is bundled. Normal code analysis needs no Ollama or additional AI model.

## Install the skill

Download [token-graph.zip](token-graph.zip), extract it, and copy the complete `token-graph` directory to:

| Assistant | Directory | Invocation |
|---|---|---|
| Codex | `~/.codex/skills/token-graph/` | `$token-graph` |
| Claude Code | `~/.claude/skills/token-graph/` | `/token-graph` |

Prepare the parser dependencies once, using Python 3.11+ with venv/pip support:

```sh
python3 /path/to/token-graph/scripts/setup_runtime.py --download-deps
```

This installs parser libraries from PyPI into the skill's own runtime. It does not install the Graphify package or upload project files. Offline installation from local wheels is also supported.

Read the [user guide](token-graph/README.md), [installation details](token-graph/INSTALL.md), or [Windows instructions](token-graph/INSTALL-WINDOWS.md).

## Explore projects

Open [index.html](index.html) locally in a browser. Choose your Projects folder to discover existing `graphify-out/graph.json` files and switch projects. The default 3D view supports drag-to-rotate, Shift-drag/right-drag to pan, scrolling to zoom, node selection and focus. A 2D toggle is also available. Existing Graphify graphs remain compatible.

The standalone viewer includes Question, Path and Impact controls that search and traverse the loaded graph in your browser. These results use simple name matching and graph relationships and can differ from the full engine. Select a Projects folder to preview source files when inspecting nodes. **Open in VS Code** prompts once for each project’s full folder path, then opens the selected source file at its graph line; the local server opens files directly.

For building, updating, freshness checks and full engine queries from the viewer, start the optional loopback server:

```sh
python3 /path/to/token-graph/scripts/explorer_server.py --no-browser
```

Open the printed URL manually. It includes a private per-run access token. The server listens only on `127.0.0.1`.

## Local exploration agent

The standalone command-line explorer uses a local Ollama model to query the project's graph and inspect bounded source excerpts. It defaults to `qwen3.8:27b-q4_K_M`; select another installed local model with `--model`. The agent only connects to Ollama at `127.0.0.1:11434` and cannot edit project files or run shell commands:

```sh
python3 token-graph/scripts/explore_agent.py /path/to/project 'Where is authentication enforced?'
python3 token-graph/scripts/explore_agent.py /path/to/project 'Where is authentication enforced?' --model qwen3:14b
```

Start Ollama first and confirm the selected model is installed. Use `--build` to build a missing code graph locally before exploration. The viewer's **Evidence** control remains deterministic graph and source retrieval; it does not call Ollama. The static viewer's question/path/impact controls are local navigation hints.

Ollama remains optional for the separate AI graph-generation workflow. Choose **Generate AI graph** in the viewer, or run:

```sh
python3 token-graph/scripts/generate_agent.py /path/to/project
```

The generator detects an installed local model (preferring `qwen3:8b`); use `--model` to choose another. Install its optional Python dependency once with `python3 token-graph/scripts/setup_runtime.py --download-deps --extras ollama`. The parser extracts code structure, while Ollama analyzes supported documents and labels communities. A code-only project may have no document semantics to extract. The operation updates graph artifacts and ignore settings in the project; a failed generation restores an existing graph JSON. Model-generated relationships can be inaccurate. Audio and video require a separately prepared local transcription model, and image analysis is not enabled by this button.

The evidence explorer is read-only unless `--build` is supplied. It excludes common secret files, but source code may contain embedded sensitive information. Use the source paths and line citations to verify findings in the project.

## Work from source

```sh
git clone https://github.com/TigranTorgomyan5/token-graph.git
cd token-graph
python3 token-graph/scripts/setup_runtime.py --download-deps
python3 token-graph/scripts/token_graph.py launch /path/to/project
```

Run tests and build the shareable ZIP:

```sh
python3 -m unittest discover -s tests -v
python3 token-graph/scripts/package_skill.py
```

The skill's canonical packaged project viewer is `token-graph/assets/project-explorer.html`. Keep the root `index.html` in sync when editing it; packaging does not overwrite either file.

## Privacy and scope

Graph analysis runs locally through a bundled runner that blocks external network connections and hosted model backends. Dependency downloads happen only during an explicitly requested setup step. Optional local Ollama processing is separate from the normal code workflow.

Codex/Claude still handles its normal conversation through its configured provider; selected graph-query context is part of that conversation. The Python offline policy is not an OS sandbox for arbitrary untrusted programs.

Context-reduction figures are heuristic estimates, not measured token usage or guaranteed savings. Cloud and remote database integrations are disabled. Some document/media features require optional local packages.

The release has 22 passing automated tests and a local-server HTTP smoke test on macOS/Python 3.14. Native Windows/Linux validation is still outstanding. See the [review report](RELEASE_REVIEW.md) for details.

The bundled engine's attribution and license files are preserved under [token-graph/vendor](token-graph/vendor).
