"""Build prerequisites are reported with a fix, and watcher process control is safe on Windows."""
from pathlib import Path
from unittest import mock
import importlib.util
import os
import subprocess
import sys
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = Path(os.environ.get('TOKEN_GRAPH_TEST_SKILL_PATH', str(ROOT / 'token-graph'))) / 'scripts'
spec = importlib.util.spec_from_file_location('token_graph_runtime_checks', SCRIPTS / 'token_graph.py')
launcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(launcher)


def probe_reports(missing: str):
    """Make the runtime probe report these modules as missing."""
    return mock.patch.object(launcher.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, missing + '\n', ''))


class RuntimeProblemTests(unittest.TestCase):
    def test_installed_runtime_has_no_problems(self):
        self.assertEqual(launcher.runtime_problems(), [])

    def test_missing_parser_libraries_name_the_setup_command(self):
        with probe_reports('networkx,tree_sitter'):
            problems = launcher.runtime_problems()
        self.assertEqual(len(problems), 1)
        self.assertIn('networkx, tree_sitter', problems[0])
        self.assertIn('setup_runtime.py', problems[0])
        self.assertIn('--download-deps', problems[0])

    def test_ai_build_without_openai_names_the_ollama_extra(self):
        with probe_reports('openai'):
            self.assertEqual(launcher.runtime_problems(), [])  # code-only builds do not need it
            problems = launcher.runtime_problems(ollama=True)
        self.assertEqual(len(problems), 1)
        self.assertIn("'openai'", problems[0])
        self.assertIn('--extras ollama', problems[0])

    def test_require_runtime_raises_the_problems(self):
        with probe_reports('numpy'), self.assertRaises(RuntimeError) as raised:
            launcher.require_runtime()
        self.assertIn('numpy', str(raised.exception))

    def test_child_processes_inherit_utf8_output(self):
        self.assertEqual(launcher.clean_env()['PYTHONIOENCODING'], 'utf-8')


class ProcessControlTests(unittest.TestCase):
    def test_alive_check_does_not_stop_the_process_and_stop_ends_its_tree(self):
        # The engine interpreter is a launcher with a child interpreter on Windows venvs.
        process = subprocess.Popen([launcher.engine_python(), '-c', 'import time; time.sleep(60)'])
        try:
            time.sleep(0.5)
            self.assertTrue(launcher.process_alive(process.pid))
            self.assertTrue(launcher.process_alive(process.pid), 'checking liveness must not terminate the process')
            self.assertIsNone(process.poll())
            launcher.stop_process_tree(process.pid)
            process.wait(timeout=15)
            self.assertFalse(launcher.process_alive(process.pid))
        finally:
            if process.poll() is None:
                process.kill()

    def test_unknown_pid_is_not_alive(self):
        self.assertFalse(launcher.process_alive(2 ** 30))  # far above any real pid (Windows ignores the low 2 bits)


if __name__ == '__main__':
    unittest.main()
