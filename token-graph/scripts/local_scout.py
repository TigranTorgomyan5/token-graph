#!/usr/bin/env python3
"""Local repo-scout: answer one question about one repo in the nh-agent-kit repo-scout format, locally.

    python3 local_scout.py <repo> "<question>" [--ref origin/V5.0] [--model qwen3:14b-q4_K_M] [--no-llm] [--with REPO ...]

With --ref, the scout reads that commit, not the working tree (other sessions use the working tree):
it takes a `git archive` snapshot of the commit, which holds committed files only, builds its graph
once, and caches both under ~/.token-graph/scout/<repo>@<sha>. The repo itself is never written.

Every field is filled by code except Behaviour's steps, which come from the explore agent's checked
per-function extraction (each step cites the lines it was checked against; checks do not prove a
step is true). Output, one block per question, as repo-scout writes it:

    Repo:      <name> (<branch or commit>)
    Question:  <the question>
    Behaviour: <what the code does, in caller terms> (<path>:<line>)
    Callers:   <who calls it> (<path>:<line>)
    Pinned by: <tests that cover it, or "no test">
    Rules:     <rule file and section that governs it, or "none">
    Unsure:    <what could not be confirmed, or "none">
    Graph:     <the graph queries run, or "no graph">
"""
from __future__ import annotations
import argparse
import io
import json
import re
import subprocess
import sys
import tarfile
from pathlib import Path

sys.dont_write_bytecode = True
import agent_frameworks as frameworks  # noqa: E402
import system_links  # noqa: E402
import explore_agent as explorer  # noqa: E402
from explorer_evidence import classify_source  # noqa: E402

SCRIPTS = Path(__file__).resolve().parent
CACHE = Path.home() / '.token-graph' / 'scout'
INDENT = ' ' * 11
MAX_TESTS = 5
MAX_RULES = 4
MAX_STEPS = 10           # steps per handler; selfExclude's insert and session revoke come after six others


def git(repo: Path, *args: str) -> str:
    return subprocess.run(['git', '-C', str(repo), *args], capture_output=True, text=True, check=True).stdout.strip()


def snapshot(repo: Path, ref: str) -> tuple[Path, str]:
    """The commit ref names, extracted once (committed files only) with its graph built: (folder, sha)."""
    sha = git(repo, 'rev-parse', '--verify', f'{ref}^{{commit}}')
    target = CACHE / f'{repo.name}@{sha[:12]}'
    if not (target / 'graphify-out' / 'graph.json').is_file():
        target.mkdir(parents=True, exist_ok=True)
        archive = subprocess.run(['git', '-C', str(repo), 'archive', sha], capture_output=True, check=True).stdout
        with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
            kwargs = {'filter': 'data'} if hasattr(tarfile, 'data_filter') else {}
            tar.extractall(target, **kwargs)
        built = subprocess.run([sys.executable, str(SCRIPTS / 'token_graph.py'), 'launch', str(target)], capture_output=True, text=True)
        if built.returncode != 0 or not (target / 'graphify-out' / 'graph.json').is_file():
            raise SystemExit(f'graph build failed for {repo.name}@{sha[:12]}: {built.stderr.strip()[-400:]}')
    return target, sha


def graph_of(project: Path) -> dict:
    try:
        return json.loads((project / 'graphify-out' / 'graph.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}


def read(project: Path, file: str) -> list[str]:
    try:
        return (project / file).read_text(encoding='utf-8').split('\n')
    except (OSError, UnicodeError):
        return []


def handler_node(graph: dict, file: str, line: int, name: str) -> str | None:
    """The graph node of the function name declared at file:line: the node of that name in the file nearest the
    line (graph lines can be stale by several lines), else the only node within 3 lines of it."""
    located = []
    for node in graph.get('nodes', []):
        location = re.fullmatch(r'L(\d+)', str(node.get('source_location', ''))) if isinstance(node, dict) else None
        if location and node.get('source_file') == file:
            label = re.fullmatch(r'\.?(\w+)\(\)', str(node.get('label', '')))
            located.append((abs(int(location.group(1)) - line), bool(label and label.group(1) == name), str(node.get('id'))))
    named = sorted(item for item in located if item[1])
    if named:
        return named[0][2]
    near = [item for item in located if item[0] <= 3]
    return near[0][2] if len(near) == 1 else None


def callers(project: Path, graph: dict, file: str, line: int, name: str) -> list[str]:
    """Production functions the graph says call the function name declared at file:line."""
    nodes = {str(node.get('id')): node for node in graph.get('nodes', []) if isinstance(node, dict)}
    targets = {handler_node(graph, file, line, name)} - {None}
    found = []
    for edge in graph.get('links', graph.get('edges', [])):
        if isinstance(edge, dict) and edge.get('relation') in {'calls', 'indirect_call'} and str(edge.get('target')) in targets:
            caller = nodes.get(str(edge.get('source')), {})
            source = str(caller.get('source_file', ''))
            if source and classify_source(source) == 'production':
                found.append(f'{caller.get("label", "?")} ({source}:{str(caller.get("source_location", "L?"))[1:]})')
    return list(dict.fromkeys(found))


def mentions(route_paths: list[str], handler_names: list[str]) -> re.Pattern | None:
    """What counts as naming the route: its path as a whole path (/v1/user, /v1/user/x; not /v1/users or
    /v1/user-settings), or the handler's name as a whole word when it is distinctive (selfExclude, not create
    or Handler, which unrelated code is full of)."""
    parts = [re.escape(path.rstrip('/')) + r'(?![\w-])' for path in route_paths if len(path.strip('/')) >= 2]
    for name in handler_names:
        words = re.findall(r'[A-Z]?[a-z]+|[A-Z]+(?![a-z])|\d+', name)
        if len(name) >= 8 and len(words) >= 2 or len(name) >= 10:
            parts.append(r'\b' + re.escape(name) + r'\b')
    return re.compile('|'.join(parts)) if parts else None


def pinned_by(project: Path, graph: dict, pattern: re.Pattern | None) -> list[str]:
    """Tests that name the route path or the handler, one line per test file: the test functions, or the
    table rows (case names) when the mention is a row of a table-driven test. A test helper is listed only
    in a file where no test names the route itself. The adapter knows each language's test declarations."""
    files = sorted({str(node.get('source_file', '')) for node in graph.get('nodes', []) if isinstance(node, dict)
                    and classify_source(str(node.get('source_file', ''))) == 'test'})
    found = []
    for file in files if pattern else []:
        lines = read(project, file)
        # A name the file gives the path counts as the path: selfExcludePath = "/v1/user/security/selfExclude",
        # then e.postJSON(selfExcludePath, ...) in each test.
        aliases = {match.group(1) for text in lines if pattern.search(text)
                   for match in [re.search(r'\b([A-Za-z_]\w*)\s*:?=\s*["\'`][^"\'`]*["\'`]\s*[;,]?\s*$', text)] if match}
        named = re.compile(pattern.pattern + ''.join(r'|\b' + re.escape(alias) + r'\b' for alias in sorted(aliases))) if aliases else pattern
        names, helpers, numbers = [], [], []
        for number, text in enumerate(lines, 1):
            if not named.search(text):
                continue
            numbers.append(number)
            case = frameworks.table_case(file, text)
            if case:
                owner = next((declared[1] for n in range(number, max(0, number - 400), -1)
                              if (declared := frameworks.test_function(file, lines[n - 1]))), None)
                names.append(case + (f' (table in {owner})' if owner else ''))
                continue
            # The nearest enclosing function of any kind: a mention inside a helper is the helper's, never the
            # test declared above it (nh-player-service's write-route guard sits below unrelated tests).
            for previous in range(number, max(0, number - 200), -1):
                declared = frameworks.test_function(file, lines[previous - 1])
                if declared:
                    if declared[0] == 'test':
                        names.append(declared[1])
                    elif declared[1] != 'TestMain':          # shared setup, not a test of this route
                        helpers.append(f'helper `{declared[1]}` (not a test)')
                    break
        if numbers:
            unique = list(dict.fromkeys(names)) or list(dict.fromkeys(helpers)) or ['a mention outside any test function']
            span = f'{numbers[0]}' + (f'-{numbers[-1]}' if numbers[-1] != numbers[0] else '')
            found.append(', '.join(unique[:6]) + (f' and {len(unique) - 6} more' if len(unique) > 6 else '') + f' ({file}:{span})')
        if len(found) >= MAX_TESTS:
            break
    return found


def rules_for(project: Path, pattern: re.Pattern | None) -> list[str]:
    """Sections of docs/rules/*.md that mention the route path or the handler: file and nearest heading."""
    found = []
    for path in sorted((project / 'docs' / 'rules').glob('*.md')) if pattern else []:
        lines = read(project, str(path.relative_to(project)))
        for number, text in enumerate(lines, 1):
            if pattern.search(text):
                heading = next((lines[n - 1].lstrip('# ').strip() for n in range(number, 0, -1) if lines[n - 1].startswith('#')), '')
                found.append(f'{path.relative_to(project)}' + (f' § {heading}' if heading else '') + f' ({path.relative_to(project)}:{number})')
                break
        if len(found) >= MAX_RULES:
            break
    return found


FORWARD_ROUTE = re.compile(r'^\S+ (/\S*) \(and \1/\* via (\w+)\) — (.+):(\d+)$')    # POST /v1/user (and /v1/user/* via catchAll) — file:39


def other_services(project: Path, siblings: list[tuple[str, Path]], route_paths: list[str], notes: list[str]) -> list[str]:
    """Callers in other services: which of the given services call this one (system_links' scan: a base-URL config
    key or a matched route) and which forward a route prefix that covers the routes asked about
    (catchAll "/v1/user" -> toPlayerService covers POST /v1/user/security/selfExclude)."""
    name = project.name.split('@')[0]
    scan = system_links.scan_system([{'name': name, 'path': str(project)}, *({'name': n, 'path': str(p)} for n, p in siblings)])
    lines = []
    for edge in scan['edges']:
        if edge['target'] != 0:
            continue
        caller, path = siblings[edge['source'] - 1]
        # The channel named like a config key (NH_PLAYER_SERVICE_URI) says more than a host name does.
        channels = sorted(edge['channels'], key=lambda channel: not re.fullmatch(r'[A-Z][A-Z0-9_]+', channel['name']))
        channel = next((c for c in channels if c['from']), None)
        evidence = channel['from'][0] if channel else None
        how = (f" ({edge['tech']}, {channel['name']}: {evidence['file']}:{evidence['line']})" if evidence else f" ({edge['tech']})")
        lines.append(f'{caller} calls this service{how}')
        for hint in explorer.endpoint_route_hints(path).splitlines():
            forward = FORWARD_ROUTE.match(hint)
            if forward and any(route == forward.group(1) or route.startswith(forward.group(1) + '/') for route in route_paths):
                lines.append(f'{caller} forwards {forward.group(1)}/* here via `{forward.group(2)}` ({forward.group(3)}:{forward.group(4)})')
    if not lines:
        notes.append('none of the other services given calls this one by a base URL or a route the scan could match')
    else:
        notes.append('other services: found by a config key or route match in their files, so a caller that reaches this service another way is missing')
    return lines


def field(label: str, values: list[str], empty: str) -> str:
    values = values or [empty]
    return f'{label + ":":<11}' + values[0] + ''.join('\n' + INDENT + value for value in values[1:])


def scout(project: Path, repo_label: str, question: str, model: str | None, siblings: list[tuple[str, Path]] | None = None) -> str:
    graph = graph_of(project)
    sources = explorer.question_route_sources(project, question) if graph else []
    unsure, behaviour, graph_used = [], [], []
    if not graph:
        return '\n'.join([field('Repo', [repo_label], ''), field('Question', [question], ''),
                          field('Behaviour', [], 'not answered: the repo has no graph'), field('Callers', [], 'unknown'),
                          field('Pinned by', [], 'unknown'), field('Rules', [], 'unknown'),
                          field('Unsure', ['everything: no graph was available'], 'none'), field('Graph', [], 'no graph')])
    graph_used.append('graph.json of the commit read; route declarations scanned for the routes the question names')
    handlers = [(why, file, line) for file, line, _, why in sources if why.startswith('handler ')]
    answer = ''
    if model:
        answer = explorer.explore(project, question, model)
    if handlers:
        graph_used.append('call edges followed from ' + ', '.join(f'`{why.split()[1]}`' for why, _, _ in handlers[:3]))
        contracts = explorer.endpoint_contracts(project, sources)
        # One block per route ("**Request and responses: POST /x** ..."), shown under that route's own handler.
        blocks: dict[str, list[str]] = {}
        current = None
        for text in contracts:
            header = re.match(r'\*\*Request and responses: (.+?)\*\*', text)
            if header:
                current = header.group(1)
            blocks.setdefault(current, []).append(text)
        for why, file, line in handlers[:3]:
            name, route = why.split()[1], why.split(' of ', 1)[1]
            behaviour.append(f'{route} is handled by `{name}` ({file}:{line})')
            # The model's checked steps for this handler, from the flow answer's section for it.
            section = re.search(r'^\*\*' + re.escape(why) + r'\*\* \([^)]*\)\n((?:- .*\n?)+)', answer, re.M) if answer else None
            for step in (section.group(1).splitlines() if section else [])[:MAX_STEPS]:
                cite = explorer.CITATION.findall(step)
                text = re.sub(r'\s*(`[^`]*`\s*)*[\w./-]+\.\w+:\d+(?:-\d+)?\s*$', '', step[2:]).rstrip('. ')
                if cite and not step.startswith('- Outcomes'):
                    path, first, last = cite[-1]
                    behaviour.append(f'- {text} ({path}:{first}' + (f'-{last}' if last else '') + ')')
            for text in blocks.get(' '.join(route.split()[:2]), []):      # its header names method and path only
                if text.startswith(('- Request body', '  - ', '- POST', '- GET', '- PUT', '- DELETE', '- PATCH', '- HANDLE')):
                    behaviour.append(text.strip() if not text.startswith('  - ') else '  ' + text.strip())
                elif text.startswith('**Related endpoints'):
                    behaviour.append('Related endpoints (same module; their request takes a field an earlier step returns):')
        route_paths = [why.split(' of ', 1)[1].split()[1] for why, _, _ in handlers]
        pattern = mentions(route_paths, [why.split()[1] for why, _, _ in handlers])
        caller_lines = []
        # The route list gives each registration's own line (the source is a window starting before it, and a
        # path built from a constant, "/"+TokenRoute, cannot be searched for).
        registered = {}
        for hint in explorer.endpoint_route_hints(project).splitlines():
            match = re.match(r'(.+?) — (.+):(\d+)$', hint)
            if match:
                registered.setdefault((match.group(1), match.group(2)), int(match.group(3)))
        for file, start, count, why in sources:
            if why.startswith('registration of '):
                route = why.split(' of ', 1)[1]
                line = registered.get((route, file), start)
                caller_lines.append(f'{route} route, registered at {file}:{line}')
        for why, file, line in handlers[:3]:
            caller_lines += callers(project, graph, file, line, why.split()[1])
        if siblings:
            caller_lines += other_services(project, siblings, route_paths, unsure)
        tests = pinned_by(project, graph, pattern)
        rules = rules_for(project, pattern)
        unsure.append('only the functions on the code path were read; deeper calls were not followed')
        if any('status set in' in text for text in contracts):
            unsure.append('some response statuses are chosen at runtime by an error-mapping writer (shown as "status set in")')
    else:
        # No route named: the explore agent's own answer, or nothing without a model.
        lines = [line for line in answer.splitlines() if line.startswith('- ')][:MAX_STEPS] if answer else []
        behaviour = lines or ['no route matched the question; run with a model for a graph-search answer']
        caller_lines, tests, rules = [], [], []
        unsure.append('no route matched the question, so callers, tests and rules were not looked up')
    dropped = re.search(r'(\d+) candidate claim\(s\) were dropped', answer)
    if dropped and int(dropped.group(1)):
        unsure.append(f'{dropped.group(1)} model step(s) were dropped for failing citation checks')
    if not model:
        unsure.append('run without a model: Behaviour lists the code path, bodies and statuses only, no steps')
    unsure.append('cited lines were checked to exist and name what a step says; that does not prove a step is true')
    return '\n'.join([field('Repo', [repo_label], ''), field('Question', [question], ''),
                      field('Behaviour', behaviour, 'not found'), field('Callers', caller_lines, 'none found in this repo'),
                      field('Pinned by', tests, 'no test'), field('Rules', rules, 'none'),
                      field('Unsure', unsure, 'none'), field('Graph', graph_used, 'no graph')])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('repo', type=Path)
    parser.add_argument('question')
    parser.add_argument('--ref', help='read this commit (e.g. origin/V5.0) instead of the working tree')
    parser.add_argument('--model', default=explorer.DEFAULT_MODEL)
    parser.add_argument('--no-llm', action='store_true', help='code-found fields only')
    parser.add_argument('--with', dest='others', nargs='*', default=[], metavar='REPO',
                        help='other services (paths, or names next to REPO) to look for callers in; read at the same --ref')
    args = parser.parse_args()
    repo = args.repo.expanduser().resolve()
    if args.ref:
        project, sha = snapshot(repo, args.ref)
        label = f'{repo.name} ({args.ref} @ {sha[:12]})'
    else:
        project = repo
        try:
            label = f'{repo.name} (working tree, {git(repo, "rev-parse", "--abbrev-ref", "HEAD")} @ {git(repo, "rev-parse", "--short=12", "HEAD")})'
        except (OSError, subprocess.CalledProcessError):
            label = f'{repo.name} (working tree)'
    siblings = []
    for item in args.others:
        other = Path(item).expanduser()
        other = (other if other.exists() else repo.parent / item).resolve()
        if args.ref:
            try:
                siblings.append((other.name, snapshot(other, args.ref)[0]))
            except (OSError, subprocess.CalledProcessError):
                print(f'note: {other.name} has no {args.ref}; not searched for callers', file=sys.stderr)
        else:
            siblings.append((other.name, other))
    print(scout(project, label, args.question, None if args.no_llm else args.model, siblings))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
