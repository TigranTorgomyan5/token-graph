#!/usr/bin/env python3
"""Retrieve logs using configured local MCP servers and analyze them with local Ollama."""
import argparse
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import tempfile
import threading
import tomllib

SCRIPTS = Path(__file__).resolve().parent


class MCP:
    def __init__(self, config):
        command = [config['command'], *config.get('args', [])]
        # Refuse implicit package or image downloads.
        if Path(command[0]).name == 'npx':
            command = [command[0], '--no-install', *[x for x in command[1:] if x not in ('-y', '--yes')]]
        if Path(command[0]).name == 'docker' and len(command) > 1 and command[1] == 'run':
            command.insert(2, '--pull=never')
        env = os.environ.copy()
        env.update({k: str(v) for k, v in config.get('env', {}).items()})
        self.process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        stderr=subprocess.DEVNULL, text=True, env=env, bufsize=1)
        self.responses = queue.Queue()
        threading.Thread(target=self._read, daemon=True).start()
        self.next_id = 0
        self.request('initialize', {'protocolVersion': '2025-03-26', 'capabilities': {},
                                    'clientInfo': {'name': 'token-graph-log-agent', 'version': '1'}})
        self.send({'jsonrpc': '2.0', 'method': 'notifications/initialized'})

    def _read(self):
        for line in self.process.stdout:
            try:
                self.responses.put(json.loads(line))
            except ValueError:
                continue
        self.responses.put(None)

    def send(self, message):
        self.process.stdin.write(json.dumps(message) + '\n')
        self.process.stdin.flush()

    def request(self, method, params):
        self.next_id += 1
        ident = self.next_id
        self.send({'jsonrpc': '2.0', 'id': ident, 'method': method, 'params': params})
        while True:
            try:
                response = self.responses.get(timeout=45)
            except queue.Empty:
                raise RuntimeError('MCP server did not respond within 45 seconds.') from None
            if response is None:
                raise RuntimeError('MCP server stopped; check that its local package or image is installed.')
            if response.get('id') != ident:
                continue
            if response.get('error'):
                raise RuntimeError('MCP request failed: ' + str(response['error'].get('message', 'unknown error')))
            return response.get('result', {})

    def call(self, name, arguments):
        result = self.request('tools/call', {'name': name, 'arguments': arguments})
        if result.get('isError'):
            raise RuntimeError('MCP tool failed: ' + str(result.get('content', [{}])[0].get('text', 'unknown error'))[:300])
        blocks = result.get('content', [])
        for block in blocks:
            if block.get('type') == 'text':
                return json.loads(block['text'])
        raise RuntimeError('MCP tool returned no JSON text.')

    def close(self):
        self.process.terminate()
        try:
            self.process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', choices=('grafana', 'argocd'))
    parser.add_argument('--service', required=True)
    parser.add_argument('--application', help='exact Argo CD application name')
    parser.add_argument('--app-namespace', default='argocd')
    parser.add_argument('--namespace', help='workload namespace; required when ambiguous')
    parser.add_argument('--container', help='container name; defaults to service')
    parser.add_argument('--datasource', default='loki')
    parser.add_argument('--since', default='now-1h')
    parser.add_argument('--limit', type=int, default=100)
    parser.add_argument('--model', default='qwen3:14b-q4_K_M')
    parser.add_argument('--question', default='Summarize failures and suggest investigation steps.')
    parser.add_argument('--config', type=Path, default=Path.home() / '.codex' / 'config.toml')
    args = parser.parse_args()
    if not 1 <= args.limit <= 100:
        parser.error('--limit must be between 1 and 100')
    if not all(c.isalnum() or c in '-_.' for c in args.service):
        parser.error('service must contain only letters, digits, dots, underscores and hyphens')
    source_name = 'grafana-dev' if args.source == 'grafana' else 'argocd-mcp'
    client = None
    try:
        config = tomllib.loads(args.config.read_text())['mcp_servers'][source_name]
        client = MCP(config)
        if args.source == 'grafana':
            response = client.call('query_loki_logs', {'datasourceUid': args.datasource,
                'logql': '{service_name="' + args.service + '"}', 'startRfc3339': args.since, 'limit': args.limit})
            fmt = 'loki'
        else:
            if not args.application:
                parser.error('--application is required for Argo CD')
            tree = client.call('get_application_resource_tree', {'applicationName': args.application,
                               'applicationNamespace': args.app_namespace})
            pods = [n for n in tree.get('nodes', []) if n.get('kind') == 'Pod'
                    and n.get('name', '').startswith(args.service + '-')
                    and (not args.namespace or n.get('namespace') == args.namespace)]
            if len(pods) != 1:
                raise ValueError(f'Found {len(pods)} matching pods; select a unique workload before analysis.')
            pod = pods[0]
            ref = {k: pod.get(k, '') for k in ('group', 'version', 'kind', 'namespace', 'name', 'uid')}
            response = client.call('get_application_workload_logs', {'applicationName': args.application,
                'applicationNamespace': args.app_namespace, 'resourceRef': ref, 'container': args.container or args.service})
            if not isinstance(response, list):
                raise ValueError('Argo CD returned an unexpected log response.')
            response = response[-args.limit:]
            fmt = 'argocd'
        with tempfile.TemporaryDirectory(prefix='local-log-agent-') as directory:
            path = Path(directory) / 'response.json'
            path.write_text(json.dumps(response), encoding='utf-8')
            print(f'SOURCE: {args.source}, service={args.service}; requested maximum {args.limit} records.', flush=True)
            if args.source == 'argocd':
                print('Argo CD may return a bounded batch without a specified time window.', flush=True)
            command = [sys.executable, str(SCRIPTS / 'log_analyzer.py'), str(path), args.question,
                       '--source', fmt, '--source-service', args.service, '--model', args.model]
            return subprocess.run(command, check=False).returncode
    except (OSError, KeyError, ValueError, RuntimeError, json.JSONDecodeError) as error:
        print(f'Local log agent failed: {error}', file=sys.stderr)
        return 1
    finally:
        if client:
            client.close()


if __name__ == '__main__':
    raise SystemExit(main())
