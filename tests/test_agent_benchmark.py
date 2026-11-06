"""Behavior checks for agent wall-time benchmarks."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'token-graph' / 'scripts'))
from benchmark_agents import benchmark
import explorer_server


class BenchmarkTests(unittest.TestCase):
    def test_server_runs_selected_graph_benchmark(self):
        project = Path(__file__).resolve().parents[1]
        if not (project / 'graphify-out' / 'graph.json').is_file():
            self.skipTest('no local graph')
        result = explorer_server.agent_benchmark({'agent': 'graph', 'question': 'log analyzer', 'repeats': 1, 'no_llm': True}, project)
        self.assertEqual(result['results']['graph']['successful_runs'], 1)
        self.assertGreaterEqual(result['results']['graph']['median_seconds'], 0)

    def test_server_rejects_unrecognized_agent_and_excessive_repeats(self):
        project = Path.cwd()
        with self.assertRaises(ValueError):
            explorer_server.agent_benchmark({'agent': 'shell', 'question': 'x'}, project)
        with self.assertRaises(ValueError):
            explorer_server.agent_benchmark({'agent': 'graph', 'question': 'x', 'repeats': 9}, project)

    def test_success_and_failure_are_reported_separately(self):
        ok = benchmark([sys.executable, '-c', 'print("ok")'], 2, 5)
        self.assertEqual(ok['successful_runs'], 2)
        self.assertEqual(ok['failed_runs'], 0)
        self.assertEqual(len(ok['wall_seconds']), 2)
        self.assertIsNotNone(ok['median_seconds'])
        bad = benchmark([sys.executable, '-c', 'raise SystemExit(7)'], 1, 5)
        self.assertEqual(bad['successful_runs'], 0)
        self.assertEqual(bad['failures'][0]['code'], 7)
        self.assertIsNone(bad['median_seconds'])

    def test_timeout_is_not_reported_as_a_success(self):
        result = benchmark([sys.executable, '-c', 'import time; time.sleep(2)'], 1, 0.05)
        self.assertEqual(result['successful_runs'], 0)
        self.assertEqual(result['failures'][0]['kind'], 'timeout')


if __name__ == '__main__':
    unittest.main()
