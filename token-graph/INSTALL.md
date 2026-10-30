# Share and install Token Graph

Recipients do **not** install Graphify, create a Graphify account, or run a Graphify server. Its local engine source is included in this skill.

## Requirements

- Python 3.11+ with `venv` and `pip` support. Python 3.14 is the tested runtime.
- A browser for the optional HTML viewer.
- Git only for Git-aware features and hooks.

No Ollama, AI model download, or separate `uv` installation is needed for code analysis.

## Install

1. Unzip `token-graph.zip` and copy the complete `token-graph` folder to:
   - Codex: `~/.codex/skills/token-graph/`
   - Claude Code: `~/.claude/skills/token-graph/`
2. Run the included setup script once, explicitly allowing dependency downloads:

   ```sh
   python3 /path/to/token-graph/scripts/setup_runtime.py --download-deps
   ```

   It creates the skill's own `.runtime` and installs the parser libraries from PyPI. It does **not** install the `graphifyy` package. Setup requests public dependency packages; it does not scan or upload project files. The standard Python installer works without `uv`.
3. On the next turn, invoke `$token-graph` in Codex or `/token-graph` in Claude Code.

On Windows, use your Python command (for example `py -3.14`) and the equivalent skill directory under your user profile. Windows and Linux have not been tested in this release.

## Offline installation

Provide wheels compatible with the recipient's Python version, OS and architecture:

```sh
python3 /path/to/token-graph/scripts/setup_runtime.py --wheel-dir /path/to/wheels
```

With no flags, setup stays offline and can use an existing uv cache when uv is available. If dependencies are absent, setup fails with instructions instead of downloading silently. `--locked` selects the exact dependency versions tested on Python 3.14; omit it on other Python versions. The machine-specific `.runtime` is intentionally excluded from the portable ZIP.

## Use

The skill builds, updates, queries and exports local project graphs. Existing `graphify-out/graph.json` files remain compatible. That legacy folder name does not imply an external service. Generated HTML is never opened automatically.

Open `assets/project-explorer.html` manually for the central multi-project viewer. Choose your Projects folder to discover and switch existing graphs. The browser reads local files; it does not upload them. Reselect the folder after graph data changes on disk.

## Optional features

Use setup's `--extras` only for the features you need, for example:

```sh
python3 /path/to/token-graph/scripts/setup_runtime.py --download-deps --extras sql pdf office
```

Available extras: `sql`, `pdf`, `office`, `plot`, `ollama`, `media`. Media also needs a separately supplied, cached local model. A local polling watcher is included without watchdog. None of these extras is required for normal code graph creation or queries.

## Network behavior and limits

Analysis runs through the bundled offline runner. It blocks external IP connections, hosted backends, cloud commands and URL ingestion. Explicit local Ollama actions may connect only to loopback port 11434. This Python execution policy is not an OS sandbox for arbitrary untrusted programs.

The invoking Codex/Claude conversation still sends its normal prompts and selected graph-query context to that assistant provider. Semantic extraction by the invoking assistant uses that normal conversation. Cloud and remote database integrations are deliberately unavailable. Token savings are heuristic estimates, not measured provider usage. Optional packages and advanced engine features are not all covered by the release tests.
