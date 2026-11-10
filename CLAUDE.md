# Token Graph — notes for Claude

Local code knowledge-graph tool: offline engine (Tree-sitter, ~35 languages) + 2D/3D Project Explorer UI + optional local Ollama agent.

## Save tokens
- Find code with the graph first (`/token-graph` skill or `python token-graph/scripts/token_graph.py launch <project> --action query|explain|affected ...`). Open only the files you will edit.
- Never read `index.html` or `token-graph/assets/project-explorer.html` — they are build outputs (158 KB). Edit `viewer-src/` instead.
- Never read `token-graph/.runtime/`, `token-graph/vendor/graphify/skills/`, `token-graph.zip`, `tests/snapshots/`.
- One feature per session. Stop when the "Done when" criteria pass.

## Map
- `viewer-src/` — Project Explorer source. `template.html` + `body.html` (markup) + `css/` + `js/` → `python viewer-src/build.py` → writes both `token-graph/assets/project-explorer.html` and root `index.html`. `js/*.js` are fragments of ONE IIFE (01 opens it, 11 closes it), concatenated in order:
  01-core (state, api(), helpers, filters, 2D/3D projection, normalizeGraph) · 02-grouping · 03-graph-data (kinds, search, metrics, communities) · 04-render (draw, hit test) · 05-details (node panel, VS Code, select, copy context) · 06-focus-view (focus mode with depth 1–3 and direction both/in/out, filters, setView) · 06b-clusters (cluster bubbles for graphs >500 nodes, expand/collapse, cluster panel, Node Inspector relations) · 06c-lenses (Full / Architecture / Dependencies / Execution flow: relation classes, modules, arrows) · 07-projects (add/switch/import graphs) · 08-ask-agent (ask bar, browser fallback, Ollama agent chat) · 09-freshness (stale/diff) · 10-server (build/update jobs, server mode) · 10b-shell (side nav, AI chip, Overview cards: Ask, How it is built module map, Key components) · 10c-system (System page: saved systems of service folders, SVG service map, connection evidence) · 11-dashboard (showPage: dashboard=Overview | graph | ask | health | system | settings — one task per page; server cards, docs generation, init)
- `token-graph/assets/graph-viewer.html` — separate per-project viewer exported by `token_graph.py` (not built from viewer-src).
- `token-graph/scripts/`
  - `explorer_server.py` — loopback server, token auth. GET: /api/ping, projects, status, stale, diff, editors, agent/status, dashboard, ai/status, graph, source, systems, folders (sub-folder names for the in-page folder browser used by the System editor; any OS), savings. POST: systems/save|delete|discover|scan|graph (merged system graph), pick (native dialog: Windows UTF-8 + topmost owner, macOS activate, zenity/kdialog cancel = answer), build, agent/build, ask, agent (evidence, no LLM), llm (local Ollama answer via explore_agent.explore), docs, activity/clear, open, forget, shutdown
  - `token_graph.py` — CLI launcher for all engine actions · `offline_engine.py` / `offline_policy.py` / `sitecustomize.py` — run bundled engine with no network
  - `system_links.py` — systems (several service folders, saved in `.explorer-systems.json`): regex scan of any language for NATS/Kafka/RabbitMQ/Redis/MassTransit/gRPC/HTTP endpoints, links producer→consumer across services; `system_graph()` merges service graphs (ids `s<i>::`, community = service, node.project_path) + cross-service links (`tech`, `channel`). CLI: `scan <dirs...>`, `discover <parent>`, `impact` (git changes → BROKEN/CHANGED/CONTRACT/NEW/UNMATCHED across services), `hook` (Claude Code PostToolUse, exit 2 + stderr), `install-hook`/`uninstall-hook` (~/.claude/settings.json). Systems live in `~/.token-graph/systems.json` (+ `system-cache/`), shared by all skill copies. Tests: `tests/test_system_links.py` + `tests/fixtures/system/`
  - `graph_answer.py` — the `query` answer: reuses the engine's scoring/seeds/traversal, then ranks (hop, relevance, low degree), caps nodes per file, summarises bare file nodes, and prints call/reference edges before further nodes. `TOKEN_GRAPH_RAW_QUERY=1` restores the engine's own text. `node_filters.py` also drops documentation nodes and minified/vendored bundles (content statistics, not names) from answers unless `--include-docs` / `--include-vendored`; those two rules have no viewer mirror. Tests: `tests/test_graph_answer.py`
  - `ask_engine.py` (question/path/impact) · `dashboard_engine.py` · `explorer_evidence.py` (deterministic retrieval) · `node_filters.py` (test/generated rules — mirrored in viewer js/01-core.js, change both); also builds the answer copy: drops external names (no source file), shows `L<start>-<end>`
  - `line_ranges.py` — after each build/update adds `end_line` to code nodes (Tree-sitter, per extension; graph.json keeps `source_location` = start) · `graph_freshness.py` — `stale_reason()`: graph `built_at_commit` vs HEAD, or files edited after graph.json; the launcher auto-updates before query/path/explain/affected/hubs (`--no-auto-update` only warns). Tests: `tests/test_answer_context.py`
  - `code_health.py` — graph-only reports behind `launch --action dead|similar|hotspots`: dead code (no graph callers, dead chains, text check for dynamic/html use, confidence high/medium/tests-only), reuse finder (name/docstring word match), hotspots (lines x connections x `git log` churn). Needs no UI. Tests: `tests/test_code_health.py` · `review.py` — `--action review [--ref]`: `git diff` lines → innermost graph symbols → callers (other files), tests, hotspot rank, unused-after-edit, same-name twin. Tests: `tests/test_review.py`
  - `session_savings.py` — tokens and % saved by graph answers in a Claude Code transcript (`~/.claude/projects/*/*.jsonl`, offline, 4 chars = 1 token): baseline = cited files Claude then opened, read in full; actual = graph answers + reads. `--session <id>`, `--list N`, `--details`, `--json`. Tests: `tests/test_session_savings.py`
  - `explore_agent.py` (Ollama read-only agent, 127.0.0.1:11434) · `generate_agent.py` (AI graph) · `install_explorer_agent.py`
  - `local_scout.py` — local repo-scout in nh-agent-kit's scout block format (Repo/Question/Behaviour/Callers/Pinned by/Rules/Unsure/Graph): `local_scout.py <repo> "<question>" [--ref origin/V5.0] [--no-llm] [--with REPO ...]` (`--with`: other services to search for callers, via `system_links` config-key links and forwarded route prefixes). `--ref` reads a `git archive` snapshot in `~/.token-graph/scout/`, never the working tree. Tests: `tests/test_local_scout.py`. Model eval on real services: `tests/eval_agents.py` (needs Ollama + the nh repos)
  - `agent_frameworks.py` — the agents' language/framework adapter: routes, lifecycle lines, route handlers and string constants per file extension (Go, Python, JS/TS, C#, Java/Kotlin, Rust, PHP, Ruby). Framework patterns go here, never in explore_agent.py. Tests: `tests/test_agent_frameworks.py` + `tests/fixtures/services/<framework>/`
  - `setup_runtime.py` · `package_skill.py` (builds token-graph.zip; does NOT touch viewers) · `watch_local.py` · `token_savings.py` · `transcribe_local.py`
- `token-graph/vendor/graphify/` — bundled engine: `extractors/` + `extract.py` (parsers), `build.py`, `cluster.py` (communities), `affected.py` (impact), `analyze.py`, `callflow_html.py`, `diagnostics.py`, `benchmark.py`, `export.py`. Prefer reusing these over new code.
- `tests/` — unittest; `tests/fixtures/languages/*` + `tests/snapshots/*.json` per-language extraction snapshots.

## Commands
- Build viewer: `python viewer-src/build.py` (check only: `--check`)
- Tests: `python -m unittest discover -s tests -v`
- Package: `python token-graph/scripts/package_skill.py`
- Run app (Windows): `Start-TokenGraph.bat`

## Rules
- Preserve the existing 2D and 3D graph engines; extend, don't rewrite.
- Language-specific logic only in extractors/adapters, never in UI or core.
- Server stays 127.0.0.1-only and token-protected; agents are read-only (no shell, no file edits).
- No new dependencies without asking. Keep the viewer working from file:// (no ES modules, no CDN).
- After editing viewer-src: run build.py, then the tests.
