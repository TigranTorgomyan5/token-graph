"""hook_guard.py: nudge/deny before search or read until a Token Graph query ran."""
from pathlib import Path
import json
import os
import subprocess
import sys
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / "token-graph" / "scripts" / "hook_guard.py"


class HookGuardTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.repo = root / "repo"
        (self.repo / "graphify-out").mkdir(parents=True)
        (self.repo / ".git").mkdir()
        (self.repo / "graphify-out" / "graph.json").write_text(" " * 200_000)
        self.home = root / "home"
        self.home.mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def run_hook(self, command, tool, args, session="s1", strict=False, cap=None):
        env = dict(os.environ, HOME=str(self.home))
        env.pop("TOKEN_GRAPH_HOOK_STRICT", None)
        if strict:
            env["TOKEN_GRAPH_HOOK_STRICT"] = "1"
        if cap is not None:
            env["TOKEN_GRAPH_HOOK_NUDGE_CAP"] = str(cap)
        event = {"tool_name": tool, "tool_input": args, "session_id": session, "cwd": str(self.repo)}
        out = subprocess.run([sys.executable, str(SCRIPT), command], input=json.dumps(event), capture_output=True, text=True, env=env, timeout=20)
        self.assertEqual(out.returncode, 0, out.stderr)
        return json.loads(out.stdout)["hookSpecificOutput"] if out.stdout.strip() else None

    def test_soft_nudge_then_cap(self):
        for _ in range(2):
            self.assertIn("Token Graph", self.run_hook("guard", "Grep", {"pattern": "x"}, cap=2)["additionalContext"])
        self.assertIsNone(self.run_hook("guard", "Grep", {"pattern": "x"}, cap=2))

    def test_query_silences_and_strict_denies_before_it(self):
        self.assertEqual(self.run_hook("guard", "Read", {"file_path": str(self.repo / "a.go")}, strict=True)["permissionDecision"], "deny")
        self.assertIsNone(self.run_hook("guard", "Bash", {"command": "python3 x/token_graph.py launch . --action query --question q"}, strict=True))
        self.assertIsNone(self.run_hook("guard", "Read", {"file_path": str(self.repo / "a.go")}, strict=True))

    def test_ignores_other_tools_docs_outside_repo_and_small_graphs(self):
        self.assertIsNone(self.run_hook("guard", "Bash", {"command": "ls -la"}))
        self.assertIsNone(self.run_hook("guard", "Read", {"file_path": str(self.repo / "README.md")}))
        self.assertIsNone(self.run_hook("guard", "Read", {"file_path": "/etc/hosts"}))
        (self.repo / "graphify-out" / "graph.json").write_text("{}")
        self.assertIsNone(self.run_hook("guard", "Grep", {"pattern": "x"}))

    def test_reset_restores_the_budget(self):
        self.run_hook("guard", "Grep", {"pattern": "x"}, cap=1)
        self.assertIsNone(self.run_hook("guard", "Grep", {"pattern": "x"}, cap=1))
        self.run_hook("reset", "", {}, cap=1)
        self.assertIsNotNone(self.run_hook("guard", "Grep", {"pattern": "x"}, cap=1))


if __name__ == "__main__":
    unittest.main()
