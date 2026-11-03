"""Phase 0 safety net: lock today's graph output for one small project per language.

Each fixture under tests/fixtures/languages/<language>/ models the same wallet domain
(interface, implementation, subclass, cross-file call, test file, generated file), is
built with the real launcher, and is reduced to a stable summary: nodes, edges,
relation counts and the test/generated flags from node_filters. The summary must match
tests/snapshots/<language>.json exactly, so any refactor that changes what the graph
contains (plugins, adapters, normalizers) shows up as a reviewable diff.

Refresh after an intended change:
    TOKEN_GRAPH_UPDATE_SNAPSHOTS=1 python -m unittest tests.test_language_snapshots
"""
from __future__ import annotations

from collections import Counter
from pathlib import Path
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = Path(os.environ.get('TOKEN_GRAPH_TEST_SKILL_PATH', str(ROOT / 'token-graph'))) / 'scripts'
FIXTURES = Path(__file__).resolve().parent / 'fixtures' / 'languages'
SNAPSHOTS = Path(__file__).resolve().parent / 'snapshots'
LANGUAGES = ('csharp', 'go', 'java', 'python', 'typescript')
UPDATE = os.environ.get('TOKEN_GRAPH_UPDATE_SNAPSHOTS') == '1'

sys.path.insert(0, str(SCRIPTS))
from node_filters import is_generated, is_test  # noqa: E402


def build_graph(fixture: Path, workdir: Path) -> dict:
    project = workdir / fixture.name
    shutil.copytree(fixture, project)
    result = subprocess.run([sys.executable, str(SCRIPTS / 'token_graph.py'), 'build', str(project)],
                            capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=300)
    if result.returncode:
        raise AssertionError(f'build failed for {fixture.name}:\n{result.stdout}\n{result.stderr}')
    return json.loads((project / 'graphify-out' / 'graph.json').read_text(encoding='utf-8'))


def summarize(graph: dict) -> dict:
    """Everything that defines the graph's meaning, minus volatile fields (community ids, ordering)."""
    nodes = graph.get('nodes', [])
    edges = graph.get('links', graph.get('edges', []))
    by_id = {node['id']: node for node in nodes}

    def name(node_id: str) -> str:
        node = by_id.get(node_id)
        if node is None:
            return f'?{node_id}'
        file = str(node.get('source_file') or '').replace('\\', '/')
        return f"{node.get('label', node_id)} @ {file or '<external>'}"

    def describe(node: dict) -> str:
        flags = [flag for flag, on in (('test', is_test(node)), ('generated', is_generated(node))) if on]
        line = name(node['id'])
        if node.get('source_location'):
            line += f" {node['source_location']}"
        if node.get('file_type') and node['file_type'] != 'code':
            line += f" <{node['file_type']}>"
        return line + (f" [{','.join(flags)}]" if flags else '')

    relations = Counter(str(edge.get('relation')) for edge in edges)
    return {
        'node_count': len(nodes),
        'edge_count': len(edges),
        'relations': dict(sorted(relations.items())),
        'test_files': sorted({str(n.get('source_file')) for n in nodes if is_test(n)}),
        'generated_files': sorted({str(n.get('source_file')) for n in nodes if is_generated(n)}),
        'nodes': sorted(describe(node) for node in nodes),
        'edges': sorted(f"{name(e['source'])} --{e.get('relation')}--> {name(e['target'])}" for e in edges),
    }


class LanguageSnapshotTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix='token-graph-snapshots-')
        cls.workdir = Path(cls.temp.name)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def check(self, language: str) -> dict:
        summary = summarize(build_graph(FIXTURES / language, self.workdir / language))
        snapshot = SNAPSHOTS / f'{language}.json'
        if UPDATE:
            snapshot.parent.mkdir(parents=True, exist_ok=True)
            snapshot.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
        self.assertTrue(snapshot.is_file(), f'No snapshot for {language}; run with TOKEN_GRAPH_UPDATE_SNAPSHOTS=1 to create it.')
        expected = json.loads(snapshot.read_text(encoding='utf-8'))
        self.maxDiff = None
        self.assertEqual(expected, summary, f'{language} graph changed; if intended, refresh with TOKEN_GRAPH_UPDATE_SNAPSHOTS=1')
        return summary

    def assert_contract(self, summary: dict, *, test_file: str, generated_file: str) -> None:
        """Language-independent expectations every fixture meets today."""
        labels = ' | '.join(summary['nodes'])
        for symbol in ('Wallet', 'BonusWallet', 'Ledger' if 'Ledger' in labels else 'ledger'):
            self.assertIn(symbol, labels)
        self.assertIn(test_file, summary['test_files'])
        self.assertIn(generated_file, summary['generated_files'])
        self.assertGreater(summary['relations'].get('calls', 0), 0)

    def test_csharp(self):
        summary = self.check('csharp')
        self.assert_contract(summary, test_file='tests/Wallet.Tests/WalletTests.cs', generated_file='src/Wallet.Core/Generated/WalletDto.g.cs')

    def test_go(self):
        summary = self.check('go')
        self.assert_contract(summary, test_file='wallet/wallet_test.go', generated_file='wallet/wallet.pb.go')

    def test_java(self):
        summary = self.check('java')
        self.assert_contract(summary, test_file='src/test/java/com/example/wallet/WalletTest.java',
                             generated_file='src/main/java/com/example/wallet/generated/WalletDto.java')

    def test_python(self):
        summary = self.check('python')
        self.assert_contract(summary, test_file='tests/test_wallet.py', generated_file='wallet/wallet_pb2.py')

    def test_typescript(self):
        summary = self.check('typescript')
        self.assert_contract(summary, test_file='src/wallet.test.ts', generated_file='src/generated/walletDto.ts')

    def test_build_is_deterministic(self):
        """Snapshots are only useful if two builds of the same tree are identical."""
        first = summarize(build_graph(FIXTURES / 'csharp', self.workdir / 'determinism-1'))
        second = summarize(build_graph(FIXTURES / 'csharp', self.workdir / 'determinism-2'))
        self.assertEqual(first, second)


if __name__ == '__main__':
    unittest.main()
