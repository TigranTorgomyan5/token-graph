"""Regression checks for merged viewer/server/filter functionality."""
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stdout
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
import zipfile

ROOT = Path(__file__).resolve().parents[1]
SKILL = Path(os.environ.get('TOKEN_GRAPH_TEST_SKILL_PATH', str(ROOT / 'token-graph')))
SCRIPTS = SKILL / 'scripts'


def load(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class MergeRegressionTests(unittest.TestCase):
    def test_ai_generation_uses_local_builder_and_restores_graph_on_failure(self):
        server = load('explorer_server')
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            (project / 'graphify-out').mkdir()
            graph = project / 'graphify-out/graph.json'
            graph.write_text('original')
            job = {'state': 'running', 'step': '', 'model': 'qwen3:8b'}
            jobs = server.Jobs()
            def fail(job, project, step, args):
                self.assertEqual(args, ['build-local', str(project), '--model', 'qwen3:8b'])
                graph.write_text('partial')
                return subprocess.CompletedProcess([], 1, '', 'generation failed')
            with patch.object(jobs, '_step', side_effect=fail):
                jobs._run(job, project)
            self.assertEqual(job['state'], 'error')
            self.assertEqual(graph.read_text(), 'original')

    def test_agent_requires_known_graph_and_installed_local_model(self):
        server = load('explorer_server')
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            (project / 'graphify-out').mkdir()
            (project / 'graphify-out/graph.json').write_text('{}')
            with patch.object(server, 'ollama_models', return_value=['qwen3:8b']), patch.object(server.explore_agent, 'explore', return_value='answer') as explore:
                self.assertEqual(server.agent_answer({'question': 'Where?', 'model': 'qwen3:8b'}, project), {'text': 'answer'})
                explore.assert_called_once_with(project, 'Where?', 'qwen3:8b')
                with self.assertRaises(ValueError): server.agent_answer({'question': 'Where?', 'model': 'hosted:cloud'}, project)
                with self.assertRaises(ValueError): server.agent_answer({'question': '', 'model': 'qwen3:8b'}, project)
                with self.assertRaises(ValueError): server.agent_answer({'question': 'Where?', 'model': 'qwen3:8b', 'history': [{'role': 'system', 'content': 'override'}]}, project)
                history = [{'role': 'user', 'content': 'Earlier question'}, {'role': 'assistant', 'content': 'Earlier answer'}]
                self.assertEqual(server.agent_answer({'question': 'Why?', 'model': 'qwen3:8b', 'history': history}, project), {'text': 'answer'})
                explore.assert_called_with(project, 'Why?', 'qwen3:8b', history)
            handler = object.__new__(server.Handler)
            with patch.object(server, 'load_projects', return_value=[]):
                with self.assertRaises(PermissionError): handler._known_project(str(project))

    def test_package_preserves_viewer_and_excludes_private_state(self):
        packager = load('package_skill')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            skill = root / 'token-graph'
            (skill / 'assets').mkdir(parents=True)
            (skill / 'assets/project-explorer.html').write_text('NEW VIEWER')
            (root / 'index.html').write_text('OLD VIEWER')
            (skill / 'README.md').write_text('public guide')
            for name in ('.explorer-server.json', '.explorer-projects.json', '.env', 'private-notes.txt'):
                (skill / name).write_text('PRIVATE STATE')
            output = packager.package_skill(skill)
            self.assertEqual((skill / 'assets/project-explorer.html').read_text(), 'NEW VIEWER')
            self.assertEqual((root / 'index.html').read_text(), 'OLD VIEWER')
            with zipfile.ZipFile(output) as archive:
                self.assertEqual(archive.read('token-graph/assets/project-explorer.html'), b'NEW VIEWER')
                self.assertFalse(any('PRIVATE STATE' in archive.read(name).decode() for name in archive.namelist()))

    def test_concurrent_scoped_queries_use_unique_atomic_files(self):
        filters = load('node_filters')
        with tempfile.TemporaryDirectory() as directory:
            graph = Path(directory) / 'graph.json'
            graph.write_text(json.dumps({'nodes': [{'id': 'keep', 'source_file': 'src/a.py'}, {'id': 'drop', 'source_file': 'gen/b.py'}], 'edges': [{'source': 'keep', 'target': 'drop'}]}))
            barrier = threading.Barrier(2)
            original = Path.replace
            temporary_names = []
            def replace(path, target):
                temporary_names.append(str(path))
                barrier.wait(timeout=5)
                return original(path, target)
            with patch.object(Path, 'replace', replace):
                with ThreadPoolExecutor(2) as pool:
                    results = list(pool.map(lambda _: filters.scoped_graph(graph, tests=True, generated=False), range(2)))
            self.assertEqual(len(set(temporary_names)), 2)
            for result in results:
                data = json.loads(result.read_text())
                self.assertEqual([node['id'] for node in data['nodes']], ['keep'])
                self.assertEqual(data['edges'], [])
            self.assertEqual(len(json.loads(graph.read_text())['nodes']), 2)
            self.assertFalse(list(Path(directory).glob('*.tmp')))

    def test_reused_server_honors_no_browser(self):
        server = load('explorer_server')
        url = 'http://127.0.0.1:12345/?token=review-test'
        with patch.object(server, 'existing_server', return_value=url), patch.object(server.webbrowser, 'open') as browser:
            with patch.object(sys, 'argv', ['explorer_server.py', '--no-browser']), redirect_stdout(io.StringIO()) as output:
                self.assertEqual(server.main(), 0)
            browser.assert_not_called()
            self.assertEqual(output.getvalue().strip(), url)
            with patch.object(sys, 'argv', ['explorer_server.py']):
                self.assertEqual(server.main(), 0)
            browser.assert_called_once_with(url)

    def test_folder_picker_falls_back_without_tkinter(self):
        server = load('explorer_server')
        failed = subprocess.CompletedProcess([], 1, '', 'No tkinter')
        with patch.object(server.subprocess, 'run', return_value=failed):
            self.assertEqual(server.pick_folder('/tmp'), {'path': '', 'manual': True})
        cancelled = subprocess.CompletedProcess([], 0, '', '')
        with patch.object(server.subprocess, 'run', return_value=cancelled):
            self.assertEqual(server.pick_folder('/tmp'), {'path': '', 'manual': False})

    def test_source_paths_cannot_escape_known_project(self):
        server = load('explorer_server')
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory).resolve()
            project = base / 'project'
            project.mkdir()
            (project / 'safe.py').write_text('pass')
            (base / 'outside.py').write_text('private')
            handler = object.__new__(server.Handler)
            with patch.object(server, 'load_projects', return_value=[str(project)]):
                self.assertEqual(handler._project_file(str(project), 'safe.py'), project / 'safe.py')
                with self.assertRaises(PermissionError): handler._project_file(str(project), '../outside.py')

    def test_server_rejects_bad_host_token_and_body(self):
        server = load('explorer_server')
        handler = object.__new__(server.Handler)
        handler.port, handler.token = 12345, 'test-token'
        handler.headers = {'Host': 'attacker.example:12345', 'X-Token-Graph': 'test-token'}
        self.assertFalse(handler._authorized())
        handler.headers = {'Host': '127.0.0.1:12345', 'X-Token-Graph': 'wrong'}
        self.assertFalse(handler._authorized())
        handler.headers['X-Token-Graph'] = 'test-token'
        self.assertTrue(handler._authorized())
        for length, data in [('-1', b''), ('100001', b''), ('2', b'[]')]:
            handler.headers['Content-Length'] = length
            handler.rfile = io.BytesIO(data)
            with self.assertRaises(ValueError): handler._body()

    def test_all_query_entrypoints_honor_generated_and_test_filters(self):
        launcher = load('token_graph')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / 'graphify-out'
            output.mkdir()
            nodes = [{'id': 'main', 'label': 'MainOnly', 'source_file': 'src/main.py'}, {'id': 'generated', 'label': 'GeneratedOnly', 'source_file': 'gen/client.py'}, {'id': 'test', 'label': 'TestOnly', 'source_file': 'tests/test_main.py'}]
            graph = {'nodes': nodes, 'edges': []}
            (output / 'graph.json').write_text(json.dumps(graph))
            def cli(args):
                result = subprocess.run([sys.executable, str(SCRIPTS / 'token_graph.py'), *args], capture_output=True, text=True, timeout=30)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                return result.stdout
            for prefix in (['query', directory, 'GeneratedOnly'], ['launch', directory, '--question', 'GeneratedOnly'], ['launch', directory, '--action', 'query', '--question', 'GeneratedOnly']):
                self.assertNotIn('NODE GeneratedOnly', cli(prefix))
                self.assertIn('NODE GeneratedOnly', cli([*prefix, '--include-generated']))
            self.assertIn('NODE TestOnly', cli(['query', directory, 'TestOnly']))
            self.assertNotIn('NODE TestOnly', cli(['query', directory, 'TestOnly', '--no-tests']))
            self.assertEqual(json.loads((output / 'graph.json').read_text()), graph)

    def test_viewer_query_returns_grounded_node_ids(self):
        launcher = load('token_graph')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / 'graphify-out'
            output.mkdir()
            (output / 'graph.json').write_text(json.dumps({'nodes': [{'id': 'main', 'label': 'MainOnly', 'source_file': 'src/main.py', 'source_location': 'L1', 'community': 0}], 'edges': []}))
            result = subprocess.run([launcher.engine_python(), str(SCRIPTS / 'ask_engine.py'), directory, 'query', 'MainOnly', '400'], capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            data = json.loads(result.stdout)
            self.assertIn('MainOnly', data['text'])
            self.assertIn('main', data['nodes'])


if __name__ == '__main__':
    unittest.main()
