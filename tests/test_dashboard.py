"""Dashboard backend: figures derived from a real graph, activity log, build reports, AI status."""
from pathlib import Path
from unittest.mock import patch
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = Path(os.environ.get('TOKEN_GRAPH_TEST_SKILL_PATH', str(ROOT / 'token-graph'))) / 'scripts'
FIXTURES = Path(__file__).resolve().parent / 'fixtures' / 'languages'


def load(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def build(project: Path) -> None:
    result = subprocess.run([sys.executable, str(SCRIPTS / 'token_graph.py'), 'build', str(project)],
                            capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=300)
    if result.returncode:
        raise AssertionError(result.stdout + result.stderr)


def engine_python() -> str:
    """Same interpreter the server runs dashboard_engine.py with (explorer_server.engine_python):
    the skill runtime, which has the engine's libraries; the test runner's Python may not."""
    runtime = SCRIPTS.parent / '.runtime' / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    return str(runtime) if runtime.is_file() else sys.executable


def summarize(project: Path) -> dict:
    result = subprocess.run([engine_python(), str(SCRIPTS / 'dashboard_engine.py'), str(project)],
                            capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=300)
    if result.returncode:
        raise AssertionError(result.stderr)
    return json.loads(result.stdout)


class DashboardEngineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix='token-graph-dashboard-')
        cls.project = Path(cls.temp.name) / 'python'
        shutil.copytree(FIXTURES / 'python', cls.project)
        # A real file-level import cycle: billing <-> invoices.
        (cls.project / 'billing').mkdir()
        (cls.project / 'billing' / '__init__.py').write_text('', encoding='utf-8')
        (cls.project / 'billing' / 'billing.py').write_text('from billing.invoices import invoice\n\ndef charge():\n    return invoice()\n', encoding='utf-8')
        (cls.project / 'billing' / 'invoices.py').write_text('from billing.billing import charge\n\ndef invoice():\n    return 1\n\ndef refund():\n    return charge()\n', encoding='utf-8')
        # Indexed by a code-only build, but a project file rather than program code.
        (cls.project / 'tools.csproj').write_text('<Project Sdk="Microsoft.NET.Sdk"><PropertyGroup><TargetFramework>net10.0</TargetFramework></PropertyGroup></Project>\n', encoding='utf-8')
        build(cls.project)
        cls.summary = summarize(cls.project)
        cls.graph = json.loads((cls.project / 'graphify-out' / 'graph.json').read_text(encoding='utf-8'))

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_graph_figures_match_graph_json(self):
        graph = self.summary['graph']
        self.assertEqual(graph['nodes'], len(self.graph['nodes']))
        self.assertEqual(graph['edges'], len(self.graph.get('links', self.graph.get('edges', []))))
        self.assertEqual(graph['communities'], len({n.get('community') for n in self.graph['nodes'] if n.get('community') is not None}))
        self.assertEqual(sum(graph['relations'].values()), graph['edges'])

    def test_languages_come_from_the_engine_dispatch_table(self):
        languages = {l['id']: l for l in self.summary['languages']}
        self.assertTrue(languages['python']['code'])
        self.assertEqual(languages['python']['name'], 'Python')
        self.assertIn('.py', languages['python']['extensions'])
        self.assertFalse(languages['csproj']['code'])
        files = {n['source_file'] for n in self.graph['nodes'] if n.get('source_file') and n['source_file'].endswith('.py')}
        self.assertEqual(languages['python']['files'], len(files))

    def test_import_cycles_use_the_engine_detector(self):
        cycles = self.summary['quality']['cycles']
        self.assertTrue(cycles['supported'])
        self.assertGreaterEqual(cycles['count'], 1)
        self.assertIn(sorted(cycles['items'][0]['files']), [sorted(['billing/billing.py', 'billing/invoices.py'])])

    def test_quality_and_architecture_figures(self):
        quality, arch = self.summary['quality'], self.summary['architecture']
        self.assertEqual(quality['test_files'], 1)       # tests/test_wallet.py
        self.assertEqual(quality['generated_files'], 1)  # wallet/wallet_pb2.py
        self.assertTrue(quality['hotspots'])
        self.assertEqual({m['name'] for m in arch['modules']}, {'wallet', 'tests', 'billing'})
        self.assertEqual(sum(m['files'] for m in arch['modules']), quality['code_files'])

    def test_index_reads_the_engine_manifest(self):
        manifest = json.loads((self.project / 'graphify-out' / 'manifest.json').read_text(encoding='utf-8'))
        self.assertEqual(self.summary['index']['manifest_files'], len(manifest))
        self.assertIsNotNone(self.summary['index']['last_indexed'])

    def test_project_without_graph_is_reported(self):
        with tempfile.TemporaryDirectory() as empty:
            result = subprocess.run([engine_python(), str(SCRIPTS / 'dashboard_engine.py'), empty], capture_output=True, text=True, timeout=120)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('no graph yet', result.stderr)


class DashboardServerTests(unittest.TestCase):
    def setUp(self):
        self.server = load('explorer_server')
        self.temp = tempfile.TemporaryDirectory()
        self.project = Path(self.temp.name)
        (self.project / 'graphify-out').mkdir()

    def tearDown(self):
        self.temp.cleanup()

    def test_activity_log_is_bounded_newest_first_and_clearable(self):
        with patch.object(self.server, 'MAX_ACTIVITY', 5):
            for i in range(8):
                self.server.record_activity(self.project, 'question', f'Question {i}')
        items = self.server.read_activity(self.project)
        self.assertEqual([item['title'] for item in items], [f'Question {i}' for i in range(7, 2, -1)])
        self.server.clear_activity(self.project)
        self.assertEqual(self.server.read_activity(self.project), [])

    def test_build_report_keeps_outcome_warnings_and_activity(self):
        graph = self.project / 'graphify-out' / 'graph.json'
        graph.write_text(json.dumps({'nodes': [{'id': 'a'}, {'id': 'b'}], 'links': [{'source': 'a', 'target': 'b'}]}), encoding='utf-8')
        log = self.project / 'graphify-out' / 'token-graph.log'
        log.write_text('old warning: from an earlier build\n', encoding='utf-8')
        start = log.stat().st_size
        with log.open('a', encoding='utf-8') as stream:
            stream.write('  warning: 1 .tf file(s) contributed nothing to the graph\n')
        job = {'state': 'done', 'mode': 'update', 'started': time.time() - 3, 'finished': time.time()}
        result = subprocess.CompletedProcess([], 0, 'ok\n  warning: 1 code file(s) yielded no symbols\n', '')
        self.server.Jobs._report(job, self.project, result, log, start)
        report = json.loads((self.project / 'graphify-out' / self.server.LAST_BUILD_FILE).read_text(encoding='utf-8'))
        self.assertTrue(report['ok'])
        self.assertEqual((report['nodes'], report['edges']), (2, 1))
        self.assertEqual(len(report['warnings']), 2)
        self.assertFalse(any('earlier build' in w for w in report['warnings']))
        activity = self.server.read_activity(self.project)[0]
        self.assertEqual(activity['title'], 'Graph update finished')
        self.assertIn('2 warning(s)', activity['detail'])

    def test_failed_build_report_is_recorded_as_failure(self):
        job = {'state': 'error', 'mode': 'build', 'started': time.time(), 'finished': time.time(), 'message': 'line one\nParser libraries are not installed'}
        self.server.Jobs._report(job, self.project, None, self.project / 'graphify-out' / 'missing.log', 0)
        activity = self.server.read_activity(self.project)[0]
        self.assertFalse(activity['ok'])
        self.assertEqual(activity['detail'], 'Parser libraries are not installed')

    def test_ai_status_reports_unreachable_provider_without_inventing_models(self):
        with patch.object(self.server, '_ollama_get', side_effect=OSError('connection refused')), \
             patch.object(self.server, 'cached_runtime_problems', return_value=[]):
            status = self.server.ai_status()
        self.assertFalse(status['reachable'])
        self.assertEqual(status['chat_models'], [])
        self.assertFalse(status['vector_index']['available'])

    def test_ai_status_separates_embedding_models_and_reports_processor(self):
        def fake(path, payload=None, timeout=3):
            if path == '/api/version': return {'version': '9.9'}
            if path == '/api/show': return {'capabilities': ['embedding']} if 'embed' in payload['model'] else {'capabilities': ['completion', 'tools']}
            if path == '/api/ps': return {'models': [{'name': 'qwen3:8b', 'size': 100, 'size_vram': 0}]}
            raise AssertionError(path)
        with patch.object(self.server, '_ollama_get', side_effect=fake), \
             patch.object(self.server, 'ollama_models', return_value=['qwen3:8b', 'nomic-embed-text:latest']), \
             patch.object(self.server, 'cached_runtime_problems', return_value=[]):
            status = self.server.ai_status()
        self.assertTrue(status['reachable'])
        self.assertEqual(status['chat_models'], ['qwen3:8b'])
        self.assertEqual(status['embedding_models'], ['nomic-embed-text:latest'])
        self.assertEqual(status['loaded'][0]['processor'], 'CPU')

    def test_summary_is_cached_until_the_graph_changes(self):
        graph = self.project / 'graphify-out' / 'graph.json'
        graph.write_text('{}', encoding='utf-8')
        calls = []
        def fake_run(*args, **kwargs):
            calls.append(1)
            return subprocess.CompletedProcess([], 0, json.dumps({'quality': {'hotspots': [], 'cycles': {}}}), '')
        with patch.object(self.server.subprocess, 'run', side_effect=fake_run):
            self.server.project_summary(self.project)
            self.server.project_summary(self.project)
            self.assertEqual(len(calls), 1)
            os.utime(graph, (time.time() + 5, time.time() + 5))
            self.server.project_summary(self.project)
            self.assertEqual(len(calls), 2)
        kinds = [item['kind'] for item in self.server.read_activity(self.project)]
        self.assertEqual(kinds.count('analysis'), 2)


if __name__ == '__main__':
    unittest.main()
