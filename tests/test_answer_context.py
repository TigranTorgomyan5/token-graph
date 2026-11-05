"""Answer context: symbol line ranges, no external names, refresh before a stale answer."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / 'token-graph' / 'scripts'
sys.path.insert(0, str(SCRIPTS))
import graph_freshness  # noqa: E402
import node_filters  # noqa: E402

RUNTIME = ROOT / 'token-graph' / '.runtime' / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
ENGINE_PYTHON = str(RUNTIME) if RUNTIME.is_file() else sys.executable


def has_grammars() -> bool:
    probe = 'import tree_sitter, tree_sitter_python, tree_sitter_c_sharp, tree_sitter_javascript'
    return subprocess.run([ENGINE_PYTHON, '-c', probe], capture_output=True).returncode == 0


def node(nid, label, file, line):
    return {'id': nid, 'label': label, 'source_file': file, 'source_location': f'L{line}', '_origin': 'ast', 'file_type': 'code'}


@unittest.skipUnless(has_grammars(), 'Tree-sitter grammars are installed by setup_runtime.py')
class LineRangesTest(unittest.TestCase):
    def run_ranges(self, files: dict, nodes: list) -> dict:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for name, text in files.items():
                (root / name).write_text(textwrap.dedent(text).lstrip('\n'), encoding='utf-8')
            graph = root / 'graph.json'
            graph.write_text(json.dumps({'nodes': nodes, 'links': []}), encoding='utf-8')
            result = subprocess.run([ENGINE_PYTHON, str(SCRIPTS / 'line_ranges.py'), str(graph), str(root)],
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            return {n['id']: n.get('end_line') for n in json.loads(graph.read_text(encoding='utf-8'))['nodes']}

    def test_python_functions_classes_and_decorators(self):
        ends = self.run_ranges({'app.py': '''
            import os

            class Store:
                def get(self, key):
                    value = key
                    return value

            @route("/x")
            def handler():
                return 1
            '''}, [
            node('file', 'app.py', 'app.py', 1),
            node('store', 'Store', 'app.py', 3),
            node('get', '.get()', 'app.py', 4),
            node('handler', 'handler()', 'app.py', 9),
        ])
        self.assertIsNone(ends['file'])  # the file node keeps L1
        self.assertEqual(ends['store'], 6)
        self.assertEqual(ends['get'], 6)
        self.assertEqual(ends['handler'], 10)

    def test_csharp_method_under_an_attribute_and_property_inside_class(self):
        ends = self.run_ranges({'Api.cs': '''
            public class PaymentsController
            {
                public int Count { get; set; }

                [HttpPost]
                public IActionResult Pay(int amount)
                {
                    return Ok(amount);
                }
            }
            '''}, [
            node('cls', 'PaymentsController', 'Api.cs', 1),
            node('count', '.Count', 'Api.cs', 3),
            node('pay', '.Pay()', 'Api.cs', 6),
        ])
        self.assertEqual(ends['cls'], 10)
        self.assertIsNone(ends['count'])  # one line: no range, and never the enclosing class
        self.assertEqual(ends['pay'], 9)

    def test_javascript_and_unknown_language(self):
        ends = self.run_ranges({'a.js': 'function a() {\n  return 1;\n}\n', 'notes.xyz': 'x\ny\n'},
                               [node('a', 'a()', 'a.js', 1), node('x', 'x', 'notes.xyz', 1)])
        self.assertEqual(ends['a'], 3)
        self.assertIsNone(ends['x'])

    def test_stale_line_gets_no_range_from_an_enclosing_class(self):
        ends = self.run_ranges({'m.py': 'class A:\n    x = 1\n    y = 2\n\n    z = 3\n'},
                               [node('z', 'z', 'm.py', 4)])  # blank line: nothing declared there
        self.assertIsNone(ends['z'])


class AnswerGraphTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.graph = Path(self.temp.name) / 'graph.json'
        nodes = [
            dict(node('impact', 'impact()', 'src/links.py', 1200), end_line=1219),
            node('one', 'ONE', 'src/links.py', 5),
            {'id': 'os', 'label': 'os', '_origin': 'semantic', 'external': True, 'type': 'external', 'file_type': 'concept', 'source_file': ''},
            {'id': 'path', 'label': 'Path', '_origin': 'ast', 'file_type': 'code', 'source_file': '', 'source_location': ''},
            {'id': 'idea', 'label': 'Payment flow', '_origin': 'semantic', 'file_type': 'concept', 'source_file': ''},
        ]
        links = [{'source': 'impact', 'target': 'os'}, {'source': 'impact', 'target': 'one'}]
        self.graph.write_text(json.dumps({'nodes': nodes, 'links': links}), encoding='utf-8')

    def tearDown(self):
        self.temp.cleanup()

    def load(self, path):
        data = json.loads(Path(path).read_text(encoding='utf-8'))
        return {n['id']: n for n in data['nodes']}, data['links']

    def test_external_names_dropped_and_ranges_shown(self):
        scoped = node_filters.scoped_graph(self.graph, tests=True, generated=True, external=False, line_ranges=True)
        self.assertEqual(scoped.name, 'graph.scope-noext-ranges.json')
        nodes, links = self.load(scoped)
        self.assertEqual(set(nodes), {'impact', 'one', 'idea'})  # document concepts stay
        self.assertEqual(nodes['impact']['source_location'], 'L1200-1219')
        self.assertEqual(nodes['one']['source_location'], 'L5')
        self.assertEqual(links, [{'source': 'impact', 'target': 'one'}])
        stored, _ = self.load(self.graph)
        self.assertEqual(stored['impact']['source_location'], 'L1200')  # graph.json keeps start lines

    def test_defaults_keep_the_full_graph(self):
        self.assertEqual(node_filters.scoped_graph(self.graph, tests=True, generated=True), self.graph)
        nodes, _ = self.load(node_filters.scoped_graph(self.graph, tests=True, generated=True, line_ranges=True))
        self.assertIn('os', nodes)


@unittest.skipUnless(shutil.which('git'), 'git not installed')
class FreshnessTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.git('init', '-q')
        self.git('config', 'user.email', 'test@example.com')
        self.git('config', 'user.name', 'Test')
        (self.root / 'a.py').write_text('x = 1\n', encoding='utf-8')
        (self.root / '.gitignore').write_text('graphify-out/\n', encoding='utf-8')
        self.git('add', '.')
        self.git('commit', '-qm', 'first')
        self.graph = self.root / 'graphify-out' / 'graph.json'
        self.graph.parent.mkdir()
        self.write_graph(self.head())

    def tearDown(self):
        self.temp.cleanup()

    def git(self, *args):
        return subprocess.run(['git', *args], cwd=self.root, capture_output=True, text=True, check=True).stdout

    def head(self):
        return self.git('rev-parse', 'HEAD').strip()

    def write_graph(self, commit):
        self.graph.write_text(json.dumps({'nodes': [], 'links': [], 'built_at_commit': commit}, indent=2), encoding='utf-8')
        later = time.time() + 2
        os.utime(self.graph, (later, later))

    def test_current_graph(self):
        self.assertIsNone(graph_freshness.stale_reason(self.root, self.graph))

    def test_new_commit(self):
        built = self.head()
        (self.root / 'b.py').write_text('y = 2\n', encoding='utf-8')
        self.git('add', '.')
        self.git('commit', '-qm', 'second')
        reason = graph_freshness.stale_reason(self.root, self.graph)
        self.assertEqual(reason, f'graph built at {built[:7]}, code is at {self.head()[:7]}, 1 files changed')

    def test_edit_after_build_and_refreshed_graph(self):
        path = self.root / 'a.py'
        path.write_text('x = 2\n', encoding='utf-8')
        later = time.time() + 5
        os.utime(path, (later, later))
        self.assertEqual(graph_freshness.stale_reason(self.root, self.graph), '1 files edited after the graph was built')
        os.utime(self.graph, (later + 1, later + 1))  # what the launcher does after a refresh
        self.assertIsNone(graph_freshness.stale_reason(self.root, self.graph))

    def test_built_commit_from_compact_json(self):
        self.graph.write_text(json.dumps({'nodes': [], 'built_at_commit': 'abc1234'}), encoding='utf-8')
        self.assertEqual(graph_freshness.built_commit(self.graph), 'abc1234')


class FreshnessWithoutGitTest(unittest.TestCase):
    def test_manifest_files_newer_than_graph(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            graph = root / 'graphify-out' / 'graph.json'
            graph.parent.mkdir()
            graph.write_text('{}', encoding='utf-8')
            graph.with_name('manifest.json').write_text(json.dumps({'a.py': 1}), encoding='utf-8')
            (root / 'a.py').write_text('x = 1\n', encoding='utf-8')
            past = time.time() - 60
            os.utime(root / 'a.py', (past, past))
            self.assertIsNone(graph_freshness.stale_reason(root, graph))
            os.utime(root / 'a.py', (past + 120, past + 120))
            self.assertEqual(graph_freshness.stale_reason(root, graph), '1 files edited after the graph was built')


if __name__ == '__main__':
    unittest.main()
