# Token Graph — How to Work

Updated 2026-10-29 · First time? Follow `INSTALL-WINDOWS.md` in this folder.

## Overview

Token Graph turns a project's code into a searchable map of files, types, methods and the calls between them, so Claude (and you) can find the right code in a few hundred tokens instead of reading whole packages.

- **What it does:** builds a graph per project on this computer, answers questions from it (where is X, what calls Y, what breaks if Z changes) and shows it in a browser.
- **Why it saves tokens:** a focused graph answer is about 400–800 tokens; reading one package of a mid-size service is about 16,000. These are estimates, not measured model usage.
- **What it is not:** it locates code, it does not replace reading it. Before any edit, the code itself is read.
- **Privacy:** graph building, queries and the viewer run locally. Only the answer you or Claude choose to use goes into a Claude conversation.

You use it two ways: the **Project Explorer** in the browser (desktop shortcut) and **`/token-graph`** inside Claude Code. Both read the same graph files.

```
Project Explorer (browser) ──> explorer_server.py ──┐
                               127.0.0.1 only        │
                                                     ├──> Graphify engine ──> graphify-out/graph.json
Claude Code: /token-graph  ──> token_graph.py ───────┘    (bundled, local,      in each project
                                                           network blocked)
```

## Quick start

Double-click **Token Graph Projects** on the desktop, click **Choose project & build graph**, pick the project folder — the graph builds and opens by itself.

1. Double-click **Token Graph Projects** (purple "G" icon). A local server starts in the background and the explorer opens in your browser.
2. In the left sidebar, click **Choose project & build graph** and pick a project folder, e.g. `C:\path\to\your-project`.
3. The button shows progress ("Building your-project… 12s"). A first build took 20–30 s on services of 200–400 source files; later updates re-parse only changed files.
4. When it finishes, the graph opens. The project stays in the **Projects** dropdown for next time.

The first build of a project also adds `graphify-out/` to the project's `.gitignore` and creates a `.graphifyignore` with safe exclusions (secrets, `bin`, `obj`, `node_modules`, `vendor`).

Clicking the shortcut again reuses the running server and just opens a new tab.

## Project Explorer tour

The page has four areas: the sidebar (search, groups, projects), the summary at the top, the graph in the middle and node details on the right.

| Area | What is there | What you do with it |
| --- | --- | --- |
| Sidebar — top | Search box, kind filter, search results list | Find a node by name; click a result to jump to it |
| Sidebar — middle | Communities / Folders toggle and the group list | Click a group to show only it; click again to show all |
| Sidebar — bottom | Choose project & build graph, Choose Projects folder, Update current graph, Add graph JSON files, Projects dropdown, Stop server | Build, switch and refresh project graphs |
| Top bar | Freshness chip, Changes since last build, snapshot, reset view (⟲) | See whether the graph is current and what changed |
| Summary cards | Nodes, connections, communities or folders, source files | Size of what is currently shown (filters apply) |
| Context-reduction strip | Estimated savings, e.g. 14.4× | A rough heuristic — see Limits |
| Ask bar | Question / Path A → B / Impact | Ask the graph; answers light up on the canvas |
| Graph canvas | Dots = nodes, lines = relationships, colour = group | Drag to rotate in 3D, Shift-drag to pan, scroll to zoom, click for details, double-click to focus; use 2D for a flat map |
| Details panel | Location, community, description, source lines, connections | Focus, open in editor, copy for Claude |

Press `/` anywhere to jump to the search box.

## Ask bar

Pick the narrowest mode that fits the question: Path and Impact on a named symbol give short, precise answers; Question is for when you don't know the name yet.

| Mode | You type | You get | Typical size |
| --- | --- | --- | --- |
| Path A → B | Two classes, methods or files | The chain that connects them, drawn as a yellow line | 100–300 tokens |
| Impact | One class, method or file + depth (1–3 steps) | Everything that depends on it and could break | 200–800 tokens |
| Question | Plain words, e.g. "how are bets settled" | The best-matching nodes and their neighbours | the answer size you pick |

Sizes are estimates.

- **Answer size** (Question only): ~400, ~800 (default), ~1,500 or ~3,000 tokens. Start small; go bigger only when the answer says TRUNCATED and the thing you need is missing.
- **Name suggestions:** in Path and Impact, typing shows matching nodes as "name — file". Picking one sends its exact id, so it can't be ambiguous. The ◎ button uses the node selected on the graph.
- **Ambiguous names:** if a name exists in several files (e.g. four methods called `Place`), the answer lists them as buttons. Click one and the question re-runs with that node.
- **Hide tests / Hide generated** above the graph also scope the answers; the hint under the bar says what is skipped.
- **Under the answer:** *Show only these* hides everything else, *Copy text* copies the answer (e.g. into Claude), *Clear* removes the highlight.

Starting points are ringed in yellow; other nodes from the answer are lit, the rest of the graph is dimmed.

## Filters and views

Filters decide what is shown and what answers draw from; views decide how the rest is arranged. Your choices are remembered in this browser.

- **Hide tests** — off by default. Hides `_test.go`, `tests/` and `testdata/` folders, `*.Tests` projects, `*Tests.cs`, `*.spec.ts`. In one Go service it was 1,640 of 3,885 nodes.
- **Hide generated** — on by default, the same as `/token-graph` answers. Hides `gen/` folders, `*.pb.go`, `*.g.cs`, `*.Designer.cs`, EF `Migrations/`.
- **Kind filter** (under search) — Types (class / struct / interface together), Methods, Functions, Files, Docs, External, each with its count. The graph stores no explicit kind, so it is inferred from names and links; classes, structs and interfaces can't be told apart reliably.
- **Search results** — typing lists up to 40 matches, exact names first, then most-connected. Click one to select it and centre on it.
- **Communities / Folders** — Communities are clusters of tightly linked code found by the engine. Folders group by source folder three levels deep, e.g. `internal/place/debit`. Nodes the engine recorded without a file sit in *(no file)*.
- **Focus mode** — double-click a node (or *Focus here*) to show only it and its neighbours. Switch *1 step / 2 steps* in the bar above the graph; *Exit focus*, `Esc` or ⟲ shows everything again.

## From graph to code

Click a node, then use the buttons under its name in the details panel.

- **Open in VS Code** — opens the file at the node's line. The browser may ask once whether to open Visual Studio Code; allow it.
- **Open in Rider** — same, for JetBrains Rider. If Rider is not installed, the button reads "not installed"; it starts working by itself once Rider is installed.
- **Copy for Claude** — copies a short text block: node name and kind, `file:line`, community and folder, up to 60 connections with direction, and the source lines shown in the panel. Paste it into Claude instead of whole files.
- **Source content** — the panel shows about 30 lines around the node, read from the file on disk.

The buttons only open files inside a project in the explorer's list; anything else is refused.

## Keeping graphs fresh

A stale graph gives wrong answers, so update it whenever the top bar turns yellow.

- **Freshness chip** — green "Graph is up to date", or yellow "N files changed since this graph · Update". It checks when you open a project and every minute; hover it to see which files changed. Click the yellow chip to update.
- **Update current graph** (sidebar) — re-parses only changed files. On a 230-file .NET service an update took about 19 s.
- **Changes since last build** — before every build or update the previous graph is kept (`graphify-out/graph.prev.json`). The button lists new and removed nodes and connections, rings new nodes and lights existing nodes whose connections changed. Until a project has been updated once, it says no earlier version exists.
- **Full rebuild** — after changing `.graphifyignore`, rebuild from scratch (`/token-graph` with `--refresh`); a plain update may keep nodes you just excluded.
- **Stop server** — stops the background server. The graph on screen stays visible; building and switching stop working until you open the shortcut again. It refuses to stop while a graph is being built.

The first update after a fresh build may also add documentation (`.md`) nodes: the first build indexes code only. That is the engine's behaviour, not a change in your project.

## Using the graph from Claude Code

In any project, type `/token-graph` and say what you want in plain words; Claude picks the action and runs it. You don't need to remember the flags below — they are here so you know what Claude can do.

| You ask | Action Claude runs | Notes |
| --- | --- | --- |
| "Build / open the graph" | `launch <project>` | `--refresh` rebuilds from scratch |
| "Update the graph" | `--action update` | changed files only |
| "How is X connected to Y?" | `--action path --source X --target Y` | shortest, cheapest answer |
| "What breaks if I change X?" | `--action affected --node X` | `--depth 1–3` |
| "What is X?" | `--action explain --node X` | |
| "How does … work?" | `--action query --question "…"` | budget 800 by default |
| "Most connected code" | `--action hubs` | |
| "How big is the graph?" | `--action stats` | |

Defaults that keep answers small:

- **Budget 800** for `query`. Claude raises it to 1,500 or 3,000 only when the answer is TRUNCATED and lacks what is needed. On one Go service this cut a typical answer from about 1,650 to about 740 tokens.
- **Generated code excluded** from `query`, `path`, `explain`, `affected` and `hubs`. Add `--include-generated` when the question is about generated code.
- **Tests included.** Add `--no-tests` when only production code matters; in one .NET service this moved the answer's starting point from a test method to the real production method.
- **Ambiguous names** — retry with the node id the answer lists, e.g. `place_service_place`.

To keep something out of a project's graph permanently, add a pattern to `.graphifyignore` in the project root (one per line, e.g. `internal/gen/**`) and rebuild with `--refresh`.

## Token economy rules (all projects)

The optional rules in `extras\CLAUDE-token-economy.md`, once added to your global `%USERPROFILE%\.claude\CLAUDE.md`, tell Claude, in every project, to find the right code first and read only that — and never to trade a correct answer for fewer tokens.

What Claude does on a code question:

1. **Locate** with `/token-graph`, narrowest action first (`path`, `affected`, `explain`); an open `query` only when no name is known. No graph yet → build it once; files changed → update first.
2. **Read only what it points to** — the reported lines with offset/limit, not whole files; a narrow search before a broad one.
3. **If the answer is incomplete** (TRUNCATED, or the needed node is missing) → widen the budget or switch action, or fall back to search and read. Never answer from a partial graph.
4. **Answer from code, not from the graph** — every claim about behaviour cites `file:line` of code read in that conversation. `INFERRED` edges and an empty `affected` result are hints, not proof.
5. **Before editing, read the code being changed.** After the change, run the project's build or tests when they exist and report the real result (e.g. `make test`, `dotnet test`).

The section adds about 600 tokens to every conversation (estimate). Anything else already in that file stays as it is.

Habits that save more than any setting:

- `/clear` between unrelated tasks; `/compact` when one task gets long.
- Name the place: "fix the check in `ValidateAsync` in `SettlementService.cs`" beats "look at the project".
- Paste the error and 10–20 lines around it, not whole files or logs.

## Limits and troubleshooting

The graph is a map built from code structure; it misses some links, and every token number in it is an estimate.

**Known limits**

- **Calls through interfaces** are often missing. Impact on a method that is only called through an interface can report 0 dependents. An empty result is not proof.
- **About 5% of edges are guesses** (`INFERRED`), matched by name.
- **Bodies are not in the graph** — only names, locations and links. The explorer reads source lines from disk when you click.
- **Token numbers are estimates.** The context-reduction strip compares an answer with the whole project, so it overstates savings (it showed 14.4× on one service; a realistic figure is 3–10× versus reading the relevant package). Budgets count about 3 characters per token.
- **Not indexed by default:** PDFs, images, Office files (optional extras not installed), secrets and anything in `.graphifyignore`.
- **Windows** was not tested by the skill's authors. One start-up bug was fixed here; Git hooks and the file watcher were not tried on Windows.

**Troubleshooting**

| Symptom | Cause | Fix |
| --- | --- | --- |
| Buttons do nothing, "Server unavailable" | Tab from an older server run | Close the tab, open the desktop shortcut again |
| Build fails, log mentions `No such file or directory` under `graphify-out\cache` | Project path too long (Windows 260-character limit) | Move the project to a shorter path, or enable Windows `LongPathsEnabled` |
| Open in VS Code does nothing | Browser blocked the `vscode://` link | Allow it when the browser asks; keep "always allow" |
| Open in Rider says not installed | Rider not installed | Install Rider; the button detects it |
| Answer seems wrong or old | Graph is stale | Click the yellow freshness chip, or Update current graph |
| "Ambiguous: 'X' matches N nodes" | Same name in several files | Click one of the offered choices, or use its node id |
| Excluded files still appear | Only updated, not rebuilt | Rebuild with `--refresh` |

## Reference: where things live

The tool lives in the skill folder; each project's graph lives inside that project. Deleting the skill folder leaves the graphs; a new computer needs the skill installed and graphs rebuilt.

Skill folder: `%USERPROFILE%\.claude\skills\token-graph\`

| Path (in the skill folder) | What it is |
| --- | --- |
| `GUIDE.md` | This guide |
| `INSTALL-WINDOWS.md` | Setup steps for Windows |
| `create-shortcut.ps1` | Creates the desktop shortcut |
| `extras\CLAUDE-token-economy.md` | Optional token-saving rules for your global CLAUDE.md |
| `SKILL.md` | Instructions Claude reads when you use `/token-graph` |
| `README.md`, `INSTALL.md` | The authors' install and usage notes (original version) |
| `scripts\token_graph.py` | The launcher behind `/token-graph` |
| `scripts\explorer_server.py` | Local server for the desktop explorer (127.0.0.1 only, per-run key) |
| `scripts\ask_engine.py` | Answers the Ask bar (Question / Path / Impact) |
| `scripts\node_filters.py` | What counts as test or generated code; builds the scoped graph copies |
| `scripts\offline_policy.py` | Blocks network access during analysis (Windows fix applied here) |
| `assets\project-explorer.html` | The explorer page |
| `assets\token-graph.ico` | Shortcut icon |
| `vendor\` | Bundled Graphify engine (graphifyy 0.9.77) |
| `.runtime\` | Private Python environment with the parsers (about 154 MB) |
| `.explorer-projects.json` | Projects in the explorer's list |
| `.explorer-server.json` | Port and key of the running server (internal) |

In each project:

| Path | What it is |
| --- | --- |
| `graphify-out\graph.json` | The graph |
| `graphify-out\graph.prev.json` | The previous graph, for *Changes since last build* |
| `graphify-out\graph.scope-*.json` | Copies without tests and/or generated code, used for answers |
| `graphify-out\benchmark.json`, `GRAPH_REPORT.md`, `token-graph.log` | Savings estimate, summary report, build log |
| `.graphifyignore` | What the graph skips |
| `.gitignore` | Gains a `graphify-out/` line on the first build |

Elsewhere: the **Token Graph Projects** shortcut on the desktop (`create-shortcut.ps1`), the optional rules in `%USERPROFILE%\.claude\CLAUDE.md`, and explorer preferences in your browser's storage.
