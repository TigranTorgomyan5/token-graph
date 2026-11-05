"""session_savings.py: tokens and percent saved by graph answers in a Claude Code transcript."""
import json
import sys
import tempfile
import threading
import unittest
import urllib.parse
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'token-graph' / 'scripts'))
import session_savings  # noqa: E402
import explorer_server  # noqa: E402


class SessionSavingsTest(unittest.TestCase):
    def test_graphify_query_with_cd_and_project_filter(self):
        self.call('Bash', {'command': f'cd "{self.project}" && ~/.local/bin/graphify query "where is run" --budget 1500'}, 'NODE run() [src=src/big.py loc=L10]')
        result = self.analyze()
        self.assertEqual(result['graph_answers'], 1)
        self.assertEqual(result['without_graph_tokens'], 1000)
        self.assertEqual(session_savings.analyze(self.transcript, self.project)['graph_answers'], 1)
        self.assertEqual(session_savings.analyze(self.transcript, self.project / 'other')['graph_answers'], 0)

    def test_graphify_uses_cwd_and_explicit_graph(self):
        self.call('Bash', {'command': 'graphify explain run'}, 'Source: src/big.py L1')
        self.entries[0]['cwd'] = str(self.project)
        self.assertEqual(self.analyze()['graph_answers'], 1)
        root = session_savings.project_root('graphify query x --graph "graphify-out/graph.json"', str(self.project))
        self.assertEqual(root, self.project)

    def test_subagent_sessions_are_included_beyond_latest_30(self):
        transcripts = Path(self.temp.name) / 'sessions'
        nested = transcripts / 'workspace' / 'parent' / 'subagents'
        nested.mkdir(parents=True)
        self.transcript = nested / 'agent-one.jsonl'
        self.call('Bash', {'command': f'cd "{self.project}" && graphify query x'}, 'NODE run() [src=src/big.py loc=L1]')
        self.analyze()
        for i in range(35):
            (transcripts / 'workspace' / f'newer-{i}.jsonl').write_text('{}\n')
        with mock.patch.object(session_savings, 'PROJECTS', transcripts):
            session_savings._CACHE.clear()
            sessions = session_savings.recent_savings(self.project)
        self.assertEqual(len(sessions), 1)
        self.assertEqual(sessions[0]['scope'], 'subagent')

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.project = Path(self.temp.name) / 'proj'
        (self.project / 'src').mkdir(parents=True)
        (self.project / 'src' / 'big.py').write_text('x' * 4000, encoding='utf-8')     # 1,000 tokens
        (self.project / 'src' / 'other.py').write_text('y' * 800, encoding='utf-8')    # 200 tokens
        self.transcript = Path(self.temp.name) / 'abcd1234-session.jsonl'
        self.entries = []

    def tearDown(self):
        self.temp.cleanup()

    def call(self, name, tool_input, output):
        uid = f'toolu_{len(self.entries)}'
        self.entries.append({'type': 'assistant', 'message': {'content': [{'type': 'tool_use', 'id': uid, 'name': name, 'input': tool_input}]}})
        self.entries.append({'type': 'user', 'message': {'content': [{'type': 'tool_result', 'tool_use_id': uid, 'content': output}]}})

    def analyze(self):
        self.transcript.write_text('\n'.join(json.dumps(e) for e in self.entries) + '\nnot json\n', encoding='utf-8')
        return session_savings.analyze(self.transcript)

    def graph(self, args, output, shell='PowerShell'):
        self.call(shell, {'command': f'python "C:/skill/scripts/token_graph.py" launch "{self.project}" {args}'}, output)

    def test_answer_then_range_read(self):
        answer = 'NODE run() [src=src/big.py loc=L10-20 community=Community 1]\nNODE helper() [src=src/other.py loc=L3]'
        self.graph('--action query --question "how does run work"', answer)
        self.call('Read', {'file_path': str(self.project / 'src' / 'big.py'), 'offset': 10, 'limit': 11}, 'r' * 400)
        result = self.analyze()
        self.assertEqual(result['graph_answers'], 1)
        self.assertEqual(result['without_graph_tokens'], 1000)          # big.py in full; other.py was not needed
        self.assertEqual(result['with_graph_tokens'], session_savings.tokens(answer) + 100)
        self.assertEqual(result['saved_tokens'], 1000 - result['with_graph_tokens'])
        self.assertEqual(result['saved_percent'], round(100 * result['saved_tokens'] / 1000, 1))

    def test_answer_only_counts_its_most_cited_file_once(self):
        explain = 'Node: run()\n  Source:    src/big.py L10-20\n  --> helper() [calls] src/big.py:L12\n  --> x src/other.py:L3'
        self.graph('--action explain --node run', explain)
        self.graph('--action explain --node run', explain)
        result = self.analyze()
        self.assertEqual(result['graph_answers'], 2)
        self.assertEqual(result['without_graph_tokens'], 1000)  # the same file is not counted twice
        self.assertEqual(result['read_tokens'], 0)

    def test_shell_read_with_variables_and_other_tools_ignored(self):
        command = f'$tg="C:/skill/scripts/token_graph.py"; $r="{self.project}"; python $tg launch $r --action affected --node run'
        self.call('PowerShell', {'command': command}, 'src/big.py:L10 run()')
        self.call('Bash', {'command': 'cd proj/src && sed -n 10,20p big.py'}, 's' * 80)
        self.call('Bash', {'command': 'python token_graph.py launch . --action stats'}, 'Nodes: 5')  # not an answer
        self.call('Grep', {'pattern': 'run'}, 'src/big.py:10')
        result = self.analyze()
        self.assertEqual(result['graph_answers'], 1)
        self.assertEqual(result['answers'][0]['action'], 'affected')
        self.assertEqual(result['read_tokens'], 20)
        self.assertEqual(result['without_graph_tokens'], 1000)

    def test_answers_without_files_have_no_percent(self):
        self.graph('--action explain --node nothing', "No node matching 'nothing' found.")
        result = self.analyze()
        self.assertEqual(result['without_graph_tokens'], 0)
        self.assertIsNone(result['saved_percent'])
        self.assertIn('n/a', session_savings.report(result))

    def test_where_and_when_the_session_ran(self):
        self.entries.append({'type': 'user', 'cwd': 'C:/work/shop', 'timestamp': '2026-11-03T13:51:09Z', 'message': {'content': 'hi'}})
        self.entries.append({'type': 'ai-title', 'aiTitle': 'First name'})
        self.graph('--action explain --node run', 'Source:    src/big.py L1-5')
        self.entries.append({'type': 'ai-title', 'aiTitle': 'Fix wallet bug'})
        self.entries.append({'type': 'last-prompt', 'lastPrompt': 'run the tests'})
        result = self.analyze()
        self.assertEqual((result['title'], result['folder'], result['started'], result['last_prompt']),
                         ('Fix wallet bug', 'C:/work/shop', '2026-11-03T13:51:09Z', 'run the tests'))
        self.assertIn('"Fix wallet bug"', session_savings.report(result))

    def test_relative_project_uses_cd_or_session_folder(self):
        self.entries.append({'type': 'user', 'cwd': str(self.project), 'message': {'content': 'hi'}})
        self.call('Bash', {'command': 'python "C:/skill/token_graph.py" launch . --action explain --node run'}, 'Source:    src/big.py L1-5')
        self.assertEqual(self.analyze()['without_graph_tokens'], 1000)   # `.` = the folder the session started in
        self.assertEqual(session_savings.project_root('cd "C:/x/Slot" && python t/token_graph.py launch . --action query', 'C:/elsewhere'),
                         Path('C:/x/Slot'))
        self.assertEqual(session_savings.project_root('cd /c/x; python token_graph.py launch "$PWD" --question q'), Path('C:/x'))

    def test_git_bash_project_path(self):
        self.assertEqual(session_savings.project_root('python t/token_graph.py launch /c/Users/me/proj --action query'),
                         Path('C:/Users/me/proj'))


class SavingsEndpointTest(SessionSavingsTest):
    """GET /api/savings: the Overview card's data, only for this project's graph answers."""
    def test_endpoint_counts_only_this_project(self):
        transcripts = Path(self.temp.name) / 'claude-projects'
        (transcripts / 'p').mkdir(parents=True)
        self.transcript = transcripts / 'p' / 'abcd1234-session.jsonl'
        self.graph('--action query --question q', 'NODE run() [src=src/big.py loc=L1-5]')
        self.call('Bash', {'command': 'python token_graph.py launch "C:/elsewhere" --action query --question q'}, 'NODE a [src=a.py loc=L1]')
        self.analyze()
        projects = Path(self.temp.name) / 'projects.json'
        projects.write_text(json.dumps([str(self.project.resolve())]), encoding='utf-8')
        patches = [mock.patch.object(session_savings, 'PROJECTS', transcripts),
                   mock.patch.object(explorer_server, 'PROJECTS', projects)]
        for patch in patches:
            patch.start()
        self.addCleanup(lambda: [patch.stop() for patch in patches])
        session_savings._CACHE.clear()
        explorer_server.Handler.token = 'tok'
        server = ThreadingHTTPServer(('127.0.0.1', 0), explorer_server.Handler)
        explorer_server.Handler.port = server.server_address[1]
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(lambda: (server.shutdown(), server.server_close()))
        url = f'http://127.0.0.1:{server.server_address[1]}/api/savings?project=' + urllib.parse.quote(str(self.project.resolve()))
        with urllib.request.urlopen(urllib.request.Request(url, headers={'X-Token-Graph': 'tok'}), timeout=30) as response:
            sessions = json.loads(response.read())['sessions']
        self.assertEqual(len(sessions), 1)
        self.assertEqual(sessions[0]['session'], 'abcd1234-session')
        self.assertEqual(sessions[0]['graph_answers'], 1)            # the other project's answer is not counted
        self.assertEqual(sessions[0]['without_graph_tokens'], 1000)
        self.assertNotIn('transcript', sessions[0])


if __name__ == '__main__':
    unittest.main()
