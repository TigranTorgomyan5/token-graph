# Token Graph sharing review — 2026-10-30

The core local workflow is ready to share for the tested platform. Recipients do not need the Graphify CLI/package, a Graphify account, Graphify servers, Ollama or an AI model for code analysis.

## Fixed during review

- Setup previously required uv and an already populated local package cache. It now supports standard Python/pip and an explicit `--download-deps` mode for installing parser dependencies from PyPI.
- Offline installation remains available through local wheels or an existing uv cache. An empty offline install fails clearly without downloading silently.
- The ZIP now includes the central multi-project viewer as `assets/project-explorer.html`.
- Optional-dependency advice points at Token Graph setup instead of telling recipients to install the upstream package.
- Spawned Python workers cannot use `-S`, `-I` or `-E` to suppress the inherited offline policy.

## Verification

- Extracted the actual ZIP into a fresh temporary directory with no runtime.
- Installed its dependencies using standard Python/pip, without uv or an installed Graphify distribution.
- Ran all 21 tests against that fresh installation: passed.
- Tested code extraction and cross-file calls, communities, queries, paths, explain, impact, hubs, updates/deletions, local exports, benchmarks, merging, feedback, hooks and file watching.
- Tested network/hosted backend rejection and the empty offline-install error path.
- Confirmed bundled source imports resolve inside the extracted ZIP and no `graphifyy` distribution is installed.
- Standard skill validator: passed.
- Checked the ZIP for personal absolute paths, runtime directories and bytecode caches: none present.

## Recipient prerequisites and scope

Python 3.11+ with venv/pip is required. A one-time setup installs the parser libraries, not Graphify. Python 3.14.7 on macOS arm64 is the tested environment. Windows/Linux and other Python versions are not release-tested. All parser modules import successfully, but end-to-end language fixtures cover Python and Go, not every language. Optional document/media packages and advanced features are not exhaustively tested.

Dependency installation may access PyPI only when explicitly requested. Analysis uses the bundled offline runner. This Python policy is not an OS sandbox for arbitrary untrusted code. The invoking assistant still receives normal conversation prompts and selected query context. Benchmark savings are estimates, not measured provider usage.

## Artifact

- File: `token-graph.zip`
- Size: 1,661,726 bytes
- SHA-256: `78b189b314a213a6e69d4f7f7529786b9ef534661e1539c7dd350a4bf8943082`
- Recipient instructions: `token-graph/INSTALL.md` inside the ZIP.

## Reviewed changes merged on 2026-10-30

Added Windows audit compatibility, graph questions/highlighting, generated/test filters, focus/folder grouping, freshness/diff views, editor links and an optional loopback server. The root index and packaged project viewer now contain the enhanced interface.

Fixed four reproduced merge issues: private server state/history is excluded by a distribution allowlist; packaging never overwrites viewer sources; scoped graph caches use unique atomic temporary files; and a reused server honors --no-browser. Also aligned filters/budgets across CLI query entrypoints, validated request bodies, and added manual path entry when Tk is absent.

All 21 unit/regression tests passed. An additional HTTP smoke test of the actual ZIP passed authorization checks, build, benchmark, graph query/highlights, source preview, update, graph diff and shutdown. No browser was opened. JavaScript syntax and skill validation passed. Native Windows testing remains outstanding; the Windows changes have not been represented as verified on Windows.

## 3D viewer update — 2026-10-30

All three viewers now use a shared, self-contained perspective-projected 3D scene by default, with orbit, Shift/right-drag pan, scroll zoom, depth-sorted points, projected hit testing and a persistent 2D fallback. Existing project switching, filters, highlighting, focus and selection remain available. No remote graphics libraries or new runtime dependencies were added. The embedded single-project template now shares the same interface.

All 22 tests passed. The added frontend tests initialize each viewer with DOM/canvas doubles and exercise 3D coordinates, projection, orbit, hit testing, pan, zoom, selection, 2D fallback and a single-node graph. These mathematical/runtime checks are not a substitute for manual browser visual testing.

Documentation empty-state fix: code-only graphs now show an explanatory overlay with a Show all graph recovery button. Switching from Documentation to a project with no document nodes restores All graph. Regression checks passed in all three viewer variants.
