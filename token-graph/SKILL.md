---
name: token-graph
description: Build, refresh, explore, and query a local project knowledge graph with a bundled offline engine, returning focused code context and token savings estimates.
---

# Token Graph

Use the bundled Python launcher, not an external graph CLI. Token Graph includes its graph engine and multi-language AST parsers. No model is needed for code extraction, cross-file relationships, communities, graph queries, paths, impact analysis, reports, or exports.

## Workflow

Run `python3 <skill>/scripts/token_graph.py launch <project>` once for the inferred operation. Use the workspace root when no project is named. Add `--refresh` for a fresh build. The default builds code relationships locally; it does not probe Ollama, download models, open a browser, or launch another assistant.

The runtime is `<skill>/.runtime`. Graphify itself is never installed. For a new installation, follow `INSTALL.md`: the user can explicitly authorize `scripts/setup_runtime.py --download-deps` for a one-time install of parser dependencies from PyPI, or use `--wheel-dir` for offline installation. Standard Python with pip is sufficient; uv is optional. With no flags setup remains offline and never silently downloads. Do not install packages or models during normal project analysis; report missing dependencies with the setup command.

| User intent | Launcher arguments |
|---|---|
| Build/view graph artifacts | default `--action graph`; `--refresh` to rebuild |
| Incrementally update | `--action update` |
| Ask a repository question | `--action query --question "..."` (budget defaults to 800) |
| Trace a relationship | `--action path --source "A" --target "B"` |
| Explain a symbol | `--action explain --node "Name"` |
| Inspect impact | `--action affected --node "Name" --depth 2`; optional repeatable `--relation` |
| Architectural hubs | `--action hubs --top 10` |
| Counts | `--action stats` |
| Context savings estimate | `--action benchmark`; optional `--question` and `--queries` |
| File hierarchy | `--action tree` |
| Recompute communities | `--action recluster` |
| Local export | `--action export --format html\|callflow-html\|svg\|graphml\|wiki\|obsidian\|neo4j\|falkordb` |
| Merge local graphs | `--action merge --merge-graph /path/graph.json` (repeatable) |
| Diagnostics | `--action diagnose` |
| Local query feedback | `--action reflect` |
| Explicit Git hooks | `--action hook-install` or `--action hook-uninstall` |
| Explicit file watcher | `--action watch-start`, `watch-status`, or `watch-stop` |

Use exact IDs or `path::symbol` when a symbol name is ambiguous. For questions, answer from the bounded query output; use another focused query if needed instead of returning a whole graph or whole source files.

Keep answers small — every line of tool output costs the user tokens:
- Prefer the narrowest action: `path`, `affected` or `explain` on a named symbol before an open `query`.
- `query` defaults to `--budget 800`. Raise it (1500, 3000) only when the output says TRUNCATED and the needed node is not in it.
- `query`, `path`, `explain`, `affected` and `hubs` answer from the graph **without generated code** (gen/ folders, `*.pb.go`, `*.g.cs`, `*.Designer.cs`, EF migrations …). Add `--include-generated` only when the question is about generated code. Tests are included; add `--no-tests` when the question is clearly about production code only and test nodes crowd the answer.

The source engine is bundled under `vendor/`. Local compatibility commands are available through `scripts/offline_engine.py`; only local analysis operations are exposed. Normal skill use should go through the launcher, which creates the offline viewer and benchmark alongside the graph.

## Output and privacy

- Keep each project's graph separate. Output uses `graphify-out/` for compatibility with existing project graphs; this folder name does not imply any external service.
- Before building, extend `.graphifyignore` to exclude environment files, credentials/private keys, vendor/runtime/build/cache output and `graphify-out/`. Preserve existing exclusions. Keep generated output ignored by Git. The launcher applies baseline exclusions too.
- Do not open generated HTML or app previews automatically. Return the output paths so the user can open them manually.
- Use the central viewer's Projects folder picker to switch local project graphs, or load JSON files individually. No upload is performed.
- The engine runner blocks external network connections, hosted backends, cloud commands, URL ingestion and remote database pushes. It disables the upstream skill installer/version checks and home query log. Export Cypher locally without connecting to a database.
- The benchmark is a local heuristic estimate, not measured Codex/Claude usage. It is saved in `graph.json` and `benchmark.json` and embedded in generated HTML. Negative savings are possible for small corpora.
- The assistant conversation still sends prompts and selected tool output to the assistant's normal provider. Do not describe a hosted assistant conversation as entirely on-device.

## Optional documents and media

When the user explicitly requests semantic document mapping, the invoking assistant can read the selected documents, produce grounded nodes/edges with source references as a local JSON fragment, and merge it using the bundled engine's `merge-chunks`/`merge-graphs` commands. This uses the assistant's existing model and needs no separate model server. Keep actual usage counts unknown unless supplied by the assistant runtime; do not invent them. For fragment shape and validation rules, read `vendor/graphify/skills/opencode/references/extraction-spec.md` if present, or the bundled `vendor/graphify/skill.md` extraction section. Never run another assistant process to perform this work.

Alternatively, only when explicitly requested, use `scripts/token_graph.py build-local <project> --model <installed-name>` for semantic document processing through a preinstalled local Ollama model at `127.0.0.1:11434`. This is separate from the default code build. Never download models or use a remote model endpoint. If local inference is unavailable, report it and continue with code-only extraction when useful.

PDF, Office, SVG plotting and transcription use optional local Python packages. Audio/video additionally needs a cached faster-whisper model directory via `--whisper-model-dir`. Missing extras should be reported, not installed from the network automatically. Remote service integrations are deliberately unavailable in the offline skill.
