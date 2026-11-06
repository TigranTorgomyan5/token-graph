#!/usr/bin/env python3
"""Measure end-to-end agent latency using the same explicit inputs on repeated runs."""
import argparse
import json
from pathlib import Path
import statistics
import subprocess
import sys
import time
from explorer_evidence import engine_python

SCRIPTS = Path(__file__).resolve().parent


def benchmark(command: list[str], repeats: int, timeout: int) -> dict:
    durations, errors = [], []
    for _ in range(repeats):
        started = time.monotonic()
        try:
            result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    text=True, timeout=timeout, check=False)
        except subprocess.TimeoutExpired:
            errors.append({'kind': 'timeout', 'seconds': round(time.monotonic() - started, 3)})
            continue
        elapsed = round(time.monotonic() - started, 3)
        if result.returncode:
            # Error details can contain paths, service output or sensitive log data.
            errors.append({'kind': 'exit', 'code': result.returncode, 'seconds': elapsed})
        else:
            durations.append(elapsed)
    return {'successful_runs': len(durations), 'failed_runs': len(errors), 'wall_seconds': durations,
            'median_seconds': round(statistics.median(durations), 3) if durations else None,
            'min_seconds': min(durations) if durations else None,
            'max_seconds': max(durations) if durations else None, 'failures': errors}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project', type=Path, default=Path.cwd())
    parser.add_argument('--question', default='How does log analysis work?')
    parser.add_argument('--log', type=Path, help='local log file for log analyzer benchmark')
    parser.add_argument('--source', choices=('file', 'argocd', 'loki'), default='file')
    parser.add_argument('--source-service', default='')
    parser.add_argument('--model', default='qwen3:14b-q4_K_M')
    parser.add_argument('--agent', action='append', choices=('graph', 'explorer', 'scout', 'log', 'bridge'),
                        help='select agent(s); repeat to compare, defaults to graph, explorer, scout and log if supplied')
    parser.add_argument('--no-llm', action='store_true', help='benchmark deterministic scout and log modes; explorer is skipped')
    parser.add_argument('--bridge-source', choices=('grafana', 'argocd'), help='benchmark live MCP retrieval explicitly')
    parser.add_argument('--service', help='service for live MCP retrieval')
    parser.add_argument('--application', help='Argo CD application for live MCP retrieval')
    parser.add_argument('--app-namespace', default='argocd')
    parser.add_argument('--namespace')
    parser.add_argument('--repeats', type=int, default=3)
    parser.add_argument('--timeout', type=int, default=600)
    args = parser.parse_args()
    if not 1 <= args.repeats <= 20 or args.timeout < 1:
        parser.error('repeats must be 1–20 and timeout must be positive')
    project = args.project.expanduser().resolve()
    if not project.is_dir() or not (project / 'graphify-out' / 'graph.json').is_file():
        parser.error('project must have an existing graphify-out/graph.json')
    agents = args.agent or ['graph', 'explorer', 'scout'] + (['log'] if args.log else [])
    if 'log' in agents and (not args.log or not args.log.is_file()):
        parser.error('--log must name an existing file when benchmarking the log analyzer')
    if 'bridge' in agents and (not args.bridge_source or not args.service or args.no_llm or
                                (args.bridge_source == 'argocd' and not args.application)):
        parser.error('bridge requires --bridge-source, --service, a local model, and --application for Argo CD')
    python = sys.executable
    commands = {
        'graph': [engine_python(), str(SCRIPTS / 'ask_engine.py'), str(project), 'query', args.question, '800'],
        'explorer': [python, str(SCRIPTS / 'explore_agent.py'), str(project), args.question, '--model', args.model],
        'scout': [python, str(SCRIPTS / 'local_scout.py'), str(project), args.question, '--model', args.model],
    }
    if args.no_llm:
        commands['scout'].append('--no-llm')
    if args.log:
        commands['log'] = [python, str(SCRIPTS / 'log_analyzer.py'), str(args.log), args.question,
                           '--source', args.source, '--source-service', args.source_service, '--model', args.model]
        if args.no_llm:
            commands['log'].append('--no-llm')
    if 'bridge' in agents:
        commands['bridge'] = [python, str(SCRIPTS / 'local_log_agent.py'), args.bridge_source,
                              '--service', args.service, '--model', args.model, '--question', args.question]
        if args.application:
            commands['bridge'] += ['--application', args.application, '--app-namespace', args.app_namespace]
        if args.namespace:
            commands['bridge'] += ['--namespace', args.namespace]
    results = {}
    for agent in dict.fromkeys(agents):
        if agent == 'explorer' and args.no_llm:
            results[agent] = {'skipped': 'explorer requires a local model'}
            continue
        print(f'Benchmarking {agent} ({args.repeats} run(s))…', file=sys.stderr, flush=True)
        results[agent] = benchmark(commands[agent], args.repeats, args.timeout)
    print(json.dumps({'model': None if args.no_llm else args.model, 'repeats': args.repeats,
                      'timing': 'end-to-end wall time including process startup; model cache state is not controlled',
                      'results': results}, indent=2))
    return int(any(value.get('failed_runs', 0) for value in results.values()))


if __name__ == '__main__':
    raise SystemExit(main())
