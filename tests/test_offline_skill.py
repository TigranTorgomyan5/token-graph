"""End-to-end checks for the bundled engine and its offline execution boundary."""
from pathlib import Path
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = Path(os.environ.get('TOKEN_GRAPH_TEST_SKILL_PATH', str(ROOT / 'token-graph'))) / 'scripts'
spec = importlib.util.spec_from_file_location('token_graph', SCRIPTS / 'token_graph.py')
launcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(launcher)


class OfflineSkillTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix='token-graph-test-')
        cls.project = Path(cls.temp.name)
        (cls.project / 'service.py').write_text('def debit(amount):\n    return amount - 1\n\ndef place_bet(amount):\n    return debit(amount)\n')
        (cls.project / 'wallet.go').write_text('package wallet\nfunc Balance() int { return 12 }\nfunc Debit() int { return Balance() - 1 }\n')
        (cls.project / 'worker.py').write_text('from service import debit\n\ndef settle(amount):\n    return debit(amount)\n')
        (cls.project / '.env').write_text('SECRET=must_not_be_indexed\n')
        (cls.project / 'vendor').mkdir()
        (cls.project / 'vendor' / 'ignored.py').write_text('def should_not_appear(): pass\n')
        (cls.project / 'schema.sql').write_text('CREATE TABLE example (id INTEGER);\n')
        cls.build_output = cls.cli('build')

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    @classmethod
    def cli(cls, command, *args):
        env = dict(os.environ)
        # No Graphify binary is resolvable in the child. Python paths are explicit.
        env['PATH'] = '/usr/bin:/bin'
        result = subprocess.run([sys.executable, str(SCRIPTS / 'token_graph.py'), command, str(cls.project), *args], env=env, capture_output=True, text=True, timeout=90)
        if result.returncode:
            raise AssertionError(result.stdout + '\n' + result.stderr)
        return result.stdout

    def graph(self):
        return json.loads((self.project / 'graphify-out' / 'graph.json').read_text())

    def test_optional_dependency_advice_does_not_require_graphify(self):
        result = subprocess.run([*launcher.engine_command(), 'extract', str(self.project), '--code-only', '--no-cluster'], env=launcher.clean_env(), capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn('pip install "graphifyy', result.stdout + result.stderr)
        self.assertNotIn("pip install 'graphifyy", result.stdout + result.stderr)

    def test_engine_is_bundled_without_upstream_distribution(self):
        code = "import graphify,importlib.metadata as m; print(graphify.__file__); assert not any(d.metadata['Name'].lower() == 'graphifyy' for d in m.distributions())"
        result = subprocess.run([launcher.engine_python(), '-c', code], env=launcher.clean_env(), capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('/token-graph/vendor/graphify/', result.stdout)

    def test_multilanguage_calls_and_exclusions(self):
        data = self.graph()
        self.assertTrue(any(n.get('source_file') == 'wallet.go' for n in data['nodes']))
        self.assertTrue(any(e['relation'] == 'calls' and e['source'] == 'service_place_bet' and e['target'] == 'service_debit' for e in data.get('links', data.get('edges', []))))
        self.assertTrue(any(e['relation'] == 'calls' and e['source'] == 'worker_settle' and e['target'] == 'service_debit' for e in data.get('links', data.get('edges', []))))
        self.assertFalse(any('ignored.py' in n.get('source_file', '') or '.env' in n.get('source_file', '') for n in data['nodes']))
        self.assertTrue(all('community' in n for n in data['nodes']))

    def test_query_path_explain_impact_hubs(self):
        self.assertIn('debit', self.cli('query', 'debit', '--budget', '300'))
        self.assertIn('calls', self.cli('path', 'service_place_bet', 'service_debit'))
        self.assertIn('debit', self.cli('explain', 'service_debit'))
        self.assertIn('place_bet', self.cli('affected', 'service_debit', '--depth', '2'))
        self.assertTrue(self.cli('hubs', '--top', '2').strip())

    def test_benchmark_viewer_and_exports(self):
        self.cli('launch')
        self.assertIn('benchmark', self.graph())
        page = (self.project / 'graphify-out' / 'graph.html').read_text()
        self.assertIn('id="graph-selector"', page)
        self.assertNotIn('https://cdn', page)
        self.assertNotIn('webbrowser.open', page)
        for format in ('graphml', 'svg', 'wiki', 'obsidian', 'callflow-html'):
            self.cli('export', format)
        self.cli('launch', '--action', 'tree')
        self.cli('launch', '--action', 'diagnose')
        self.assertTrue((self.project / 'graphify-out' / 'graph.graphml').exists())
        self.assertTrue((self.project / 'graphify-out' / 'graph.svg').exists())

    def test_update_removes_deleted_files(self):
        added = self.project / 'temporary.py'
        added.write_text('def transient_node(): return 2\n')
        self.cli('update')
        self.assertTrue(any(n.get('source_file') == 'temporary.py' for n in self.graph()['nodes']))
        added.unlink()
        self.cli('update')
        self.assertFalse(any(n.get('source_file') == 'temporary.py' for n in self.graph()['nodes']))
        self.assertIn('benchmark', self.graph())

    def test_merge_recluster_reflect_and_cypher(self):
        graph = str(self.project / 'graphify-out' / 'graph.json')
        self.cli('launch', '--action', 'merge', '--merge-graph', graph, '--merge-graph', graph)
        merged = json.loads((self.project / 'graphify-out' / 'merged-graph.json').read_text())
        self.assertTrue({n['label'] for n in self.graph()['nodes']}.issubset({n['label'] for n in merged['nodes']}))
        self.cli('launch', '--action', 'recluster')
        self.cli('launch', '--action', 'reflect')
        self.cli('launch', '--action', 'export', '--format', 'neo4j')
        self.assertTrue((self.project / 'graphify-out' / 'cypher.txt').exists())

    def test_explicit_hooks_use_bundled_launcher(self):
        subprocess.run(['git', 'init', '-q', str(self.project)], check=True)
        hooks = self.project / '.git' / 'hooks'
        custom = '#!/bin/sh\necho existing_hook\n'
        (hooks / 'post-commit').write_text(custom)
        self.cli('launch', '--action', 'hook-install')
        hook = (hooks / 'post-commit').read_text()
        self.assertIn('existing_hook', hook)
        self.assertIn(str(SCRIPTS / 'token_graph.py'), hook)
        self.assertNotIn('command -v graphify', hook)
        self.cli('launch', '--action', 'hook-uninstall')
        self.assertEqual((hooks / 'post-commit').read_text(), custom)

    def test_watcher_without_watchdog(self):
        import time
        self.cli('launch', '--action', 'watch-start')
        try:
            time.sleep(1)
            added = self.project / 'watched.py'
            added.write_text('def watch_probe(): return 1\n')
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                time.sleep(.3)
                if any(n.get('source_file') == 'watched.py' for n in self.graph()['nodes']): break
            self.assertTrue(any(n.get('source_file') == 'watched.py' for n in self.graph()['nodes']))
        finally:
            self.cli('launch', '--action', 'watch-stop')
            time.sleep(1)
            (self.project / 'watched.py').unlink(missing_ok=True)

    def test_external_network_dns_and_agents_are_blocked(self):
        code = """
import socket,subprocess
for action in (
 lambda: socket.getaddrinfo('app.graphify.com',443),
 lambda: socket.socket().connect(('203.0.113.1',443)),
 lambda: socket.socket().connect(('127.0.0.1',11434)),
 lambda: subprocess.run(['curl','https://example.com']),
 lambda: subprocess.run(['git','fetch']),
 lambda: subprocess.run([__import__('sys').executable,'-S','-c','pass']),
):
 try: action()
 except PermissionError: pass
 else: raise AssertionError('offline boundary was bypassed')
print('blocked')
"""
        result = subprocess.run([launcher.engine_python(), '-c', code], env=launcher.clean_env(), capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('blocked', result.stdout)

    def test_cloud_and_remote_backend_commands_rejected(self):
        for args in (['cloud'], ['extract', str(self.project), '--backend', 'openai'], ['extract', 'https://example.com']):
            result = subprocess.run([*launcher.engine_command(), *args], env=launcher.clean_env(), capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)


if __name__ == '__main__':
    unittest.main()
