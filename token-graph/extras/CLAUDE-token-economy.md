<!-- Optional: paste the section below into %USERPROFILE%\.claude\CLAUDE.md so Claude saves tokens in every project. -->

## Token Economy (all projects)

Spend as few tokens as possible without lowering correctness — find the right code first, then read only that.

- **Locate with the code graph before reading files.** For "where / how / what calls / what breaks" questions in a code project, use `/token-graph`: the narrowest action first (`path`, `affected` or `explain` on a named symbol); an open `query` (budget 800) only when no symbol is known. No `graphify-out/graph.json` yet → build it once. Source files changed since the build → `--action update` before trusting answers.
- **Then read only what it points to:** `Read` with offset/limit around the reported lines, not whole files; `Grep` with a narrow path or glob before any broad search.
- **The graph locates code; it does not prove anything.** Read the code before editing it. `INFERRED` edges and an empty `affected` result are hints, not proof — calls through interfaces are often missing. Ambiguous name → retry with the node id the graph lists.
- Graph answers exclude generated code by default and include tests; add `--no-tests` when only production code matters.
- **Correctness beats savings.** If the graph answer is TRUNCATED or does not contain what is needed, widen it (`--budget 1500`/`3000`, another action) or fall back to `Grep`/`Read` — never answer from a partial graph. Every claim about behaviour must rest on code read in this session, cited as `file:line`. After a change, run the project's build/tests when they exist and report the real result.
- Keep output short: quote the relevant lines of code or logs, never re-dump whole files or outputs already shown.
