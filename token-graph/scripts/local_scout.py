#!/usr/bin/env python3
"""Local repo-scout: answer one question about one repo in the nh-agent-kit repo-scout format, locally.

    python3 local_scout.py <repo> "<question>" [--ref origin/V5.0] [--model qwen3:14b-q4_K_M] [--no-llm]

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
import explore_agent as explorer  # noqa: E402
from explorer_evidence import classify_source  # noqa: E402

SCRIPTS = Path(__file__).resolve().parent
CACHE = Path.home() / '.token-graph' / 'scout'
INDENT = ' ' * 11
MAX_TESTS = 5
MAX_RULES = 4
MAX_STEPS = 6


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


def callers(project: Path, graph: dict, file: str, line: int) -> list[str]:
    """Production functions the graph says call the function declared at file:line (its node, by location)."""
    nodes = {str(node.get('id')): node for node in graph.get('nodes', []) if isinstance(node, dict)}
    targets = {node_id for node_id, node in nodes.items() if node.get('source_file') == file
               and re.fullmatch(r'L(\d+)', str(node.get('source_location', ''))) and abs(int(str(node['source_location'])[1:]) - line) <= 3}
    found = []
    for edge in graph.get('links', graph.get('edges', [])):
        if isinstance(edge, dict) and edge.get('relation') in {'calls', 'indirect_call'} and str(edge.get('target')) in targets:
            caller = nodes.get(str(edge.get('source')), {})
            source = str(caller.get('source_file', ''))
            if source and classify_source(source) == 'production':
                found.append(f'{caller.get("label", "?")} ({source}:{str(caller.get("source_location", "L?"))[1:]})')
    return list(dict.fromkeys(found))


ENCLOSING_TEST = re.compile(r'^\s*(?:func\s+(Test\w+)|def\s+(test_\w+)|(?:it|test|describe)\(\s*[\'"`]([^\'"`]+)|'
                            r'(?:public\s+)?(?:async\s+)?(?:void|Task)\s+(\w+)\s*\(|fn\s+(\w+)\s*\()')


TABLE_ROW = re.compile(r'^\s*\{\s*"([^"]+)"\s*,')          # Go table-driven test row: {"CaseName", "/path", ...}
ENCLOSING_FUNC = re.compile(r'^\s*func\s+(?:\([^)]*\)\s*)?(\w+)\s*\(')


def pinned_by(project: Path, graph: dict, needles: list[str]) -> list[str]:
    """Tests that name the route path or the handler, one line per test file: the test functions, or the
    table rows (case names) when the mention is a row of a table-driven test."""
    files = sorted({str(node.get('source_file', '')) for node in graph.get('nodes', []) if isinstance(node, dict)
                    and classify_source(str(node.get('source_file', ''))) == 'test'})
    found = []
    for file in files:
        lines = read(project, file)
        names, numbers = [], []
        for number, text in enumerate(lines, 1):
            if not any(needle in text for needle in needles):
                continue
            numbers.append(number)
            row = TABLE_ROW.match(text)
            if row:
                owner = next((m.group(1) for n in range(number, max(0, number - 400), -1) if (m := ENCLOSING_FUNC.match(lines[n - 1]))), None)
                names.append(f'{row.group(1)}' + (f' (table in {owner})' if owner else ''))
                continue
            # The nearest enclosing function of any kind: a mention inside a helper is the helper's, never the
            # test declared above it (nh-player-service's write-route guard sits below unrelated tests).
            for previous in range(number, max(0, number - 200), -1):
                match = ENCLOSING_TEST.match(lines[previous - 1])
                if match:
                    name = next(group for group in match.groups() if group)
                    if name != 'TestMain':          # shared setup, not a test of this route
                        names.append(name)
                    break
                helper = ENCLOSING_FUNC.match(lines[previous - 1])
                if helper:
                    names.append(f'helper `{helper.group(1)}` (not a test)')
                    break
        if numbers:
            unique = list(dict.fromkeys(names)) or ['a mention outside any test function']
            span = f'{numbers[0]}' + (f'-{numbers[-1]}' if numbers[-1] != numbers[0] else '')
            found.append(', '.join(unique[:6]) + (f' and {len(unique) - 6} more' if len(unique) > 6 else '') + f' ({file}:{span})')
        if len(found) >= MAX_TESTS:
            break
    return found


def rules_for(project: Path, needles: list[str]) -> list[str]:
    """Sections of docs/rules/*.md that mention the route path or the handler: file and nearest heading."""
    found = []
    for path in sorted((project / 'docs' / 'rules').glob('*.md')):
        lines = read(project, str(path.relative_to(project)))
        for number, text in enumerate(lines, 1):
            if any(needle in text for needle in needles):
                heading = next((lines[n - 1].lstrip('# ').strip() for n in range(number, 0, -1) if lines[n - 1].startswith('#')), '')
                found.append(f'{path.relative_to(project)}' + (f' § {heading}' if heading else '') + f' ({path.relative_to(project)}:{number})')
                break
        if len(found) >= MAX_RULES:
            break
    return found


def field(label: str, values: list[str], empty: str) -> str:
    values = values or [empty]
    return f'{label + ":":<11}' + values[0] + ''.join('\n' + INDENT + value for value in values[1:])


def scout(project: Path, repo_label: str, question: str, model: str | None) -> str:
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
        for text in contracts:
            if text.startswith(('- Request body', '  - ', '- POST', '- GET', '- PUT', '- DELETE', '- PATCH')) and len(behaviour) < 40:
                behaviour.append(text.strip() if not text.startswith('  - ') else '  ' + text.strip())
            elif text.startswith('**Related endpoints'):
                behaviour.append('Related endpoints (same module; their request takes a field an earlier step returns):')
        route_paths = [why.split(' of ', 1)[1].split()[1] for why, _, _ in handlers]
        needles = route_paths + [why.split()[1] for why, _, _ in handlers]
        caller_lines = [f'{why.split(" of ", 1)[1]} route, registered at {file}:{line}'
                        for file, line, _, why in sources if why.startswith('registration of ')]
        for why, file, line in handlers[:3]:
            caller_lines += callers(project, graph, file, line)
        tests = pinned_by(project, graph, needles)
        rules = rules_for(project, route_paths + [why.split()[1] for why, _, _ in handlers])
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
    print(scout(project, label, args.question, None if args.no_llm else args.model))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
