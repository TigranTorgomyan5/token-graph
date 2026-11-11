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
    def test_documentation_listing_respects_ignores_and_lists_generated_wiki(self):
        server = load('explorer_server')
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            (project / 'docs').mkdir()
            (project / 'docs/guide.md').write_text('# Guide')
            (project / 'docs/secret.md').write_text('private')
            (project / '.gitignore').write_text('docs/secret.md\n')
            (project / '.env').write_text('secret')
            (project / 'graphify-out/wiki').mkdir(parents=True)
            (project / 'graphify-out/wiki/index.md').write_text('# Wiki')
            files = server.documentation_files(project)
            self.assertEqual({(item['path'], item['kind']) for item in files}, {('docs/guide.md', 'Project document'), ('graphify-out/wiki/index.md', 'Generated wiki')})

    def test_claude_agent_metadata_from_user_and_project(self):
        server = load('explorer_server')
        with tempfile.TemporaryDirectory() as directory:
            home, project = Path(directory) / 'home', Path(directory) / 'project'
            for root in (home, project):
                (root / '.claude/agents').mkdir(parents=True)
            (home / '.claude/agents/user.md').write_text('---\nname: user-agent\ndescription: Reviews code\nmodel: sonnet\nhooks:\n  command: secret-hook\n---\nprivate instructions\n')
            (project / '.claude/agents/project.md').write_text('---\nname: project-agent\ndescription: Maps this project\ntools: Read, Grep\n---\nprivate instructions\n')
            result = server.claude_agents(project, home)
            self.assertEqual([(a['name'], a['scope']) for a in result], [('user-agent', 'User'), ('project-agent', 'Project')])
            self.assertEqual(result[0]['model'], 'sonnet')
            self.assertEqual(result[1]['tools'], 'Read, Grep')
            self.assertNotIn('private instructions', json.dumps(result))
            self.assertNotIn('secret-hook', json.dumps(result))

    def test_explorer_selects_installed_local_model(self):
        agent = load('explore_agent')
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory).resolve()
            (project / 'graphify-out').mkdir()
            (project / 'graphify-out/graph.json').write_text('{}')
            with patch.object(agent, 'installed_models', return_value={'zeta:1', 'qwen3:8b'}), patch.object(agent, 'explore', return_value='answer') as explore, redirect_stdout(io.StringIO()):
                with patch.object(sys, 'argv', ['explore_agent.py', str(project), 'question']):
                    self.assertEqual(agent.main(), 0)
                explore.assert_called_with(project, 'question', 'qwen3:8b', full_output=False, max_output_chars=3000)
                with patch.object(sys, 'argv', ['explore_agent.py', str(project), 'question', '--model', 'zeta:1']):
                    self.assertEqual(agent.main(), 0)
                explore.assert_called_with(project, 'question', 'zeta:1', full_output=False, max_output_chars=3000)

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

    def test_local_agent_uses_selected_installed_model_and_validates_request(self):
        server = load('explorer_server')
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            (project / 'graphify-out').mkdir()
            (project / 'graphify-out/graph.json').write_text('{}')
            # The viewer's LLM chat goes to llm_answer (/api/llm); /api/agent stays deterministic Evidence.
            with patch.object(server, 'ollama_models', return_value=['qwen3:14b-q4_K_M']),                  patch.object(server.explore_agent, 'explore', return_value='answer') as explore:
                result = server.llm_answer({'question': 'Where?', 'model': 'qwen3:14b-q4_K_M'}, project)
                self.assertEqual({k: result[k] for k in ('text', 'model')}, {'text': 'answer', 'model': 'qwen3:14b-q4_K_M'})
                explore.assert_called_once_with(project, 'Where?', 'qwen3:14b-q4_K_M', [])
                with self.assertRaises(ValueError):
                    server.llm_answer({'question': '', 'model': 'qwen3:14b-q4_K_M'}, project)
                with self.assertRaises(ValueError):
                    server.llm_answer({'question': 'Where?', 'model': 'qwen3:14b-q4_K_M', 'history': [{'role': 'system', 'content': 'override'}]}, project)
                with self.assertRaisesRegex(ValueError, 'installed'):
                    server.llm_answer({'question': 'Where?', 'model': 'qwen3:8b'}, project)
                with self.assertRaisesRegex(ValueError, 'installed'):
                    server.llm_answer({'question': 'Where?', 'model': 'qwen3:8b:cloud'}, project)
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

    def test_viewer_query_uses_ranked_edges_and_viewer_scope(self):
        launcher = load('token_graph')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / 'graphify-out'
            output.mkdir()
            nodes = [{'id': 'h', 'label': 'exchangeWidget()', 'source_file': 'svc/handler.go', 'source_location': 'L1', 'file_type': 'code'},
                     {'id': 'v', 'label': 'validateWidget()', 'source_file': 'svc/handler.go', 'source_location': 'L10', 'file_type': 'code'},
                     {'id': 'd', 'label': 'DocOnly', 'source_file': 'docs/readme.md', 'source_location': 'L1', 'file_type': 'document'}]
            (output / 'graph.json').write_text(json.dumps({'nodes': nodes, 'links': [{'source': 'h', 'target': 'v', 'relation': 'calls'}]}))
            result = subprocess.run([launcher.engine_python(), str(SCRIPTS / 'ask_engine.py'), directory, 'query',
                                     'exchangeWidget validateWidget', '800', '--no-docs', '--no-external', '--no-vendored'],
                                    capture_output=True, text=True, timeout=60)
            self.assertEqual(result.returncode, 0, result.stderr)
            answer = json.loads(result.stdout)
            self.assertIn('EDGE exchangeWidget() --calls--> validateWidget()', answer['text'])
            self.assertNotIn('DocOnly', answer['text'])
            # Every symbol the ranked text lists is highlighted, not only the ones this function traversed.
            self.assertTrue({'h', 'v'} <= set(answer['nodes']), answer['nodes'])

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

    def test_viewer_query_ignores_words_hidden_inside_names(self):
        """'you' sits inside 'PaYOUts' but names nothing: the answer says so and offers questions to ask."""
        launcher = load('token_graph')
        graph = {'nodes': [{'id': 'svc', 'label': 'BetService', 'source_file': 'src/BetService.cs', 'source_location': 'L5', 'community': 0},
                           {'id': 'pay', 'label': '.GetSentPayoutsAsync()', 'source_file': 'src/BetService.cs', 'source_location': 'L20', 'community': 0},
                           {'id': 'ext', 'label': 'Task', 'source_file': '', 'source_location': '', 'community': 0}],
                 'edges': [{'source': 'svc', 'target': 'pay', 'relation': 'method'}, {'source': 'pay', 'target': 'ext', 'relation': 'references'}]}
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / 'graphify-out').mkdir()
            (Path(directory) / 'graphify-out' / 'graph.json').write_text(json.dumps(graph))
            def ask(question):
                result = subprocess.run([launcher.engine_python(), str(SCRIPTS / 'ask_engine.py'), directory, 'query', question, '400'],
                                        capture_output=True, text=True, timeout=30)
                self.assertEqual(result.returncode, 0, result.stderr)
                return json.loads(result.stdout)
            meta = ask('what question i can give you?')
            self.assertEqual(meta['nodes'], {})
            self.assertIn('named like: question, give, you', meta['text'])
            self.assertEqual([s['a'] for s in meta['suggestions']], ['How does BetService work?', 'What calls GetSentPayoutsAsync?', 'BetService'])
            self.assertEqual(ask('help')['suggestions'], meta['suggestions'])
            for question in ('SentPayouts', 'how are payouts sent to you'):   # whole and partial names still match
                self.assertIn('pay', ask(question)['seeds'], question)


if __name__ == '__main__':
    unittest.main()
