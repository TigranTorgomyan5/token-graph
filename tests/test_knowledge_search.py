"""Documentation-first retrieval stays bounded, local, and project-scoped."""
import sys
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'token-graph' / 'scripts'))
from knowledge_search import search
import explorer_server


class KnowledgeSearchTests(unittest.TestCase):
    def test_sections_are_ranked_bounded_and_unverified_without_graph(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'README.md').write_text('# Overview\nUnrelated information.\n# Wallet balance\nRead balance from wallet service.\n# Other\nOther text.\n')
            result = search(root, 'wallet balance')
            entry = result['documentation'][0]
            self.assertEqual((entry['heading'], entry['start_line'], entry['end_line']), ('Wallet balance', 3, 4))
            self.assertEqual(entry['verification'], 'unverified')
            self.assertNotIn('Other text', entry['text'])
            self.assertNotIn('graph_fallback', result)

    def test_ignores_secrets_ignored_files_and_symlinks(self):
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as outside:
            root = Path(directory)
            (root / '.gitignore').write_text('private/\n')
            (root / 'private').mkdir()
            (root / 'private' / 'notes.md').write_text('# Wallet\nPrivate wallet info')
            (root / 'credentials.md').write_text('# Wallet\nSecret wallet info')
            external = Path(outside) / 'external.md'
            external.write_text('# Wallet\nExternal wallet info')
            (root / 'external.md').symlink_to(external)
            with patch('knowledge_search.search_evidence', return_value={'status': 'missing_graph'}) as fallback:
                result = search(root, 'wallet')
            self.assertEqual(result['documentation'], [])
            self.assertEqual(result['graph_fallback']['status'], 'missing_graph')
            fallback.assert_called_once()

    def test_fenced_headings_do_not_split_sections(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'README.md').write_text('# Wallet\n```md\n# example\n```\nwallet balance\n# Next\n')
            entry = search(root, 'wallet balance')['documentation'][0]
            self.assertEqual(entry['end_line'], 5)

    def test_filename_match_does_not_return_every_section(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'migration.md').write_text('# Overview\nIntro.\n# Setup\nSetup details.\n# Operation\nOperation details.\n')
            entries = search(root, 'migration')['documentation']
            self.assertEqual(len(entries), 1)
            self.assertEqual(entries[0]['heading'], 'Overview')

    def test_recorded_source_freshness_including_uncommitted_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            def git(*args):
                return subprocess.run(['git', '-C', str(root), *args], capture_output=True, text=True, check=True).stdout.strip()
            git('init', '-q')
            (root / 'source.py').write_text('value = 1\n')
            git('add', 'source.py')
            git('-c', 'user.name=Test', '-c', 'user.email=test@example.invalid', 'commit', '-qm', 'source')
            revision = git('rev-parse', 'HEAD')
            (root / 'README.md').write_text(f'# Balance\n<!-- token-graph: source=source.py; revision={revision} -->\nBalance calculation.\n')
            entry = search(root, 'balance')['documentation'][0]
            self.assertEqual(entry['freshness']['status'], 'source_unchanged')
            self.assertEqual(entry['verification'], 'unverified')
            (root / 'source.py').write_text('value = 2\n')
            stale = search(root, 'balance')
            self.assertEqual(stale['documentation'][0]['freshness']['status'], 'possibly_stale')
            self.assertEqual(stale['graph_fallback']['status'], 'missing_graph')
            (root / 'source.py').unlink()
            self.assertEqual(search(root, 'balance')['documentation'][0]['freshness']['status'], 'unknown')

    def test_superseded_corrections_do_not_appear_as_current_knowledge(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'README.md').write_text('# Correction: wallet\n- Status: superseded\nOld wallet assumption.\n# Correction: wallet storage\n- Status: proposed\nNew wallet information.\n')
            results = search(root, 'wallet')['documentation']
            self.assertEqual([item['declared_status'] for item in results], ['proposed'])
            self.assertNotIn('Old wallet assumption', results[0]['text'])

    def test_server_search_requires_authorization_and_known_project(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            (root / 'README.md').write_text('# Wallet\nWallet balance is read here.\n')
            handler = object.__new__(explorer_server.Handler)
            handler.path = '/api/knowledge/search'
            handler.headers = {'Host': '127.0.0.1:12345', 'X-Token-Graph': 'test'}
            handler.port = 12345
            handler.token = 'test'
            handler._body = lambda: {'project': str(root), 'question': 'wallet balance'}
            replies = []
            handler._json = lambda status, payload: replies.append((status, payload))
            with patch.object(explorer_server, 'load_projects', return_value=[str(root)]):
                handler.do_POST()
                self.assertEqual(replies[-1][0], 200)
                self.assertEqual(replies[-1][1]['documentation'][0]['heading'], 'Wallet')
                handler.headers['X-Token-Graph'] = 'wrong'
                handler.do_POST()
                self.assertEqual(replies[-1][0], 403)
            handler.headers['X-Token-Graph'] = 'test'
            with patch.object(explorer_server, 'load_projects', return_value=[]):
                handler.do_POST()
                self.assertEqual(replies[-1][0], 400)

    def test_log_analyzer_can_list_project_knowledge_without_model(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            (root / 'README.md').write_text('# Wallet timeout\nCheck the wallet service.\n')
            log = root / 'app.log'
            log.write_text('2026-01-01T00:00:00Z ERROR wallet timeout\n')
            result = subprocess.run([sys.executable, str(Path(explorer_server.SCRIPTS) / 'log_analyzer.py'),
                                     str(log), 'wallet timeout', '--no-llm', '--knowledge-project', str(root)],
                                    text=True, capture_output=True, check=True)
            self.assertIn('PROJECT KNOWLEDGE REFERENCES', result.stdout)
            self.assertIn('README.md:1-2', result.stdout)
            self.assertNotIn('Check the wallet service.', result.stdout)


if __name__ == '__main__':
    unittest.main()
