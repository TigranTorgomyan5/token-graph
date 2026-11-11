"""`--action review`: git diff -> changed graph symbols -> callers, tests, hotspots (review.py)."""
from pathlib import Path
import importlib.util
import shutil
import subprocess
import sys
import tempfile
import unittest

SCRIPTS = Path(__file__).resolve().parents[1] / "token-graph" / "scripts"
sys.path.insert(0, str(SCRIPTS))
import review  # noqa: E402


def fn(nid, label, src, start, end):
    return {"id": nid, "label": label, "source_file": src, "source_location": f"L{start}", "end_line": end,
            "file_type": "code", "_callable": True}


def call(src, tgt):
    return {"source": src, "target": tgt, "relation": "calls"}


DIFF = """diff --git a/svc/app.py b/svc/app.py
--- a/svc/app.py
+++ b/svc/app.py
@@ -12,2 +12,3 @@ def x
-a
+b
+c
+d
@@ -40 +41,0 @@ def y
-gone
diff --git a/old.py b/old.py
--- a/old.py
+++ /dev/null
@@ -1,3 +0,0 @@
-x
"""


def graph():
    nodes = [fn("app", "app.py", "svc/app.py", 1, 1),
             fn("a", "load_widget()", "svc/app.py", 10, 20),
             fn("b", "other()", "svc/app.py", 30, 45),
             fn("cls", "Store", "svc/app.py", 50, 80),
             fn("m", ".save()", "svc/app.py", 60, 70),
             fn("caller", "handler()", "svc/api.py", 1, 9),
             fn("t", "test_load()", "tests/test_app.py", 1, 5)]
    links = [call("caller", "a"), call("t", "a"), call("caller", "m")]
    return {"nodes": nodes, "links": links}


class ParseDiff(unittest.TestCase):
    def test_new_side_ranges_and_deleted_files(self):
        files, deleted = review.parse_diff(DIFF)
        self.assertEqual(files["svc/app.py"], [(12, 14), (41, 41)])
        self.assertEqual(deleted, ["old.py"])


class Mapping(unittest.TestCase):
    def test_changed_lines_map_to_innermost_symbols(self):
        found, module = review.changed_symbols(graph(), {"svc/app.py": [(12, 13), (62, 62), (90, 91)]})
        labels = sorted(e["node"]["label"] for e in found)
        self.assertEqual(labels, [".save()", "load_widget()"])       # the class is not "changed", its method is
        self.assertEqual(module, {"svc/app.py": 1})                     # L90 sits outside every symbol


class EndToEnd(unittest.TestCase):
    @unittest.skipUnless(shutil.which("git"), "git is required")
    def test_review_of_a_real_working_tree(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "svc").mkdir()
            source = root / "svc" / "app.py"
            source.write_text("\n".join(f"line{i}" for i in range(1, 100)) + "\n", encoding="utf-8")
            def git(*a): subprocess.run(["git", "-C", tmp, *a], check=True, capture_output=True)
            git("init", "-q"); git("config", "user.email", "t@t"); git("config", "user.name", "t")
            git("add", "."); git("commit", "-qm", "base")
            lines = source.read_text(encoding="utf-8").splitlines()
            lines[14] = "changed inside load_widget"
            source.write_text("\n".join(lines) + "\n", encoding="utf-8")
            result = review.build_review(graph(), root)
            self.assertEqual(result["error"], "")
            [row] = result["rows"]
            self.assertEqual(row["node"]["label"], "load_widget()")
            self.assertEqual([n["label"] for n in row["callers"]], ["handler()"])
            self.assertEqual([n["label"] for n in row["tests"]], ["test_load()"])
            text = review.format_review(result)
            self.assertIn("load_widget", text)
            self.assertIn("tests: test_load", text)

    def test_not_a_repository_is_reported_not_raised(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIn("Review:", review.format_review(review.build_review(graph(), Path(tmp))))

    def test_ref_starting_with_a_dash_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = review.build_review(graph(), Path(tmp), ref="--output=/tmp/x")
        self.assertIn("Invalid git ref", result["error"])


if __name__ == "__main__":
    unittest.main()
