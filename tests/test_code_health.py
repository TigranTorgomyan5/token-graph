"""Dead-code, reuse-finder and hotspot reports (code_health.py)."""
from pathlib import Path
import importlib.util
import sys
import tempfile
import unittest

SCRIPTS = Path(__file__).resolve().parents[1] / "token-graph" / "scripts"
sys.path.insert(0, str(SCRIPTS))
spec = importlib.util.spec_from_file_location("code_health", SCRIPTS / "code_health.py")
ch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ch)


def fn(nid, label, src, start, end, **extra):
    return dict({"id": nid, "label": label, "source_file": src, "source_location": f"L{start}", "end_line": end,
                 "file_type": "code", "_callable": True}, **extra)


def call(src, tgt, relation="calls"):
    return {"source": src, "target": tgt, "relation": relation}


def graph():
    nodes = [
        fn("app", "app.py", "svc/app.py", 1, 1),
        fn("main", "main()", "svc/app.py", 1, 10),
        fn("used", "used_helper()", "svc/app.py", 12, 20),
        fn("lonely", "lonely_helper()", "svc/app.py", 22, 40),
        fn("chain_a", "chain_a()", "svc/app.py", 42, 50),
        fn("chain_b", "chain_b()", "svc/app.py", 52, 60),
        fn("dyn", "dynamic_target()", "svc/app.py", 62, 70),
        fn("only_test", "only_in_tests()", "svc/app.py", 72, 80),
        fn("t", "test_it()", "tests/test_app.py", 1, 5),
        {"id": "doc_used", "label": "Bring graph.json up to date with the code", "file_type": "rationale",
         "source_file": "svc/app.py", "source_location": "L13"},
    ]
    links = [call("main", "used"), call("chain_a", "chain_b"), call("t", "only_test"),
             {"source": "app", "target": "main", "relation": "contains"},
             {"source": "doc_used", "target": "used", "relation": "rationale_for"}]
    return {"nodes": nodes, "links": links}


class DeadCode(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "svc").mkdir()
        (self.root / "svc" / "app.py").write_text("def x(): getattr(m, 'dynamic_target')\n# dynamic_target is looked up by name\n", encoding="utf-8")
        (self.root / "page.html").write_text("<button onclick=\"lonely_helper_click()\">", encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def found(self, **kw):
        result = ch.find_dead(graph(), self.root, **kw)
        return {i["name"]: i for i in result["items"]}

    def test_unused_function_is_high_confidence(self):
        self.assertEqual(self.found()["lonely_helper"]["confidence"], "high")

    def test_entry_points_and_used_functions_are_never_reported(self):
        names = self.found()
        self.assertNotIn("main", names)
        self.assertNotIn("used_helper", names)
        self.assertNotIn("test_it", names)

    def test_dead_chain_is_reported_as_one_finding_group(self):
        found = self.found()
        self.assertEqual(found["chain_a"]["confidence"], "high")
        self.assertTrue(found["chain_b"]["chain"])

    def test_name_used_in_text_is_only_medium(self):
        self.assertEqual(self.found()["dynamic_target"]["confidence"], "medium")

    def test_symbol_used_only_by_tests_is_separate(self):
        self.assertEqual(self.found()["only_in_tests"]["confidence"], "tests")

    def test_path_prefix_limits_the_report(self):
        self.assertEqual(self.found(prefix="other/"), {})

    def test_ignore_pattern_skips_symbols(self):
        self.assertNotIn("lonely_helper", self.found(ignore=["^lonely"]))

    def test_route_handler_decorated_in_the_same_file_is_not_dead(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'app.py').write_text("from flask import Flask\napp = Flask(__name__)\n@app.route('/status')\ndef check_health():\n    return 'ok'\n", encoding='utf-8')
            data = {'nodes': [fn('h', 'check_health()', 'app.py', 4, 5)], 'links': []}
            self.assertEqual(ch.find_dead(data, root)['items'], [])

    def test_router_style_file_is_medium_never_a_dead_chain_root(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'routes').mkdir()
            (root / 'routes' / 'user.py').write_text("def load_user():\n    return 1\n", encoding='utf-8')
            data = {'nodes': [fn('u', 'load_user()', 'routes/user.py', 1, 2)], 'links': []}
            [item] = ch.find_dead(data, root)['items']
            self.assertEqual((item['name'], item['confidence']), ('load_user', 'medium'))

    def test_report_is_bounded_and_mentions_lines(self):
        text = ch.format_dead(ch.find_dead(graph(), self.root), top=1)
        self.assertIn("more", text)
        self.assertIn("lines", text)


class ReuseFinder(unittest.TestCase):
    def test_finds_by_name_words_and_docstring(self):
        rows = ch.find_similar(graph(), "make sure the graph is up to date with the code")
        self.assertEqual(rows[0]["node"]["label"], "used_helper()")

    def test_unrelated_idea_returns_nothing(self):
        self.assertEqual(ch.find_similar(graph(), "parse kubernetes manifest"), [])
        self.assertIn("Safe to write it new", ch.format_similar("parse kubernetes manifest", []))

    def test_camel_and_snake_names_tokenise_alike(self):
        self.assertEqual(ch.tokens("validateWidgetToken"), ch.tokens("validate_widget_tokens"))

    def test_tests_are_not_suggested(self):
        labels = [r["node"]["label"] for r in ch.find_similar(graph(), "test it")]
        self.assertNotIn("test_it()", labels)


class Hotspots(unittest.TestCase):
    def test_big_connected_symbol_ranks_first_and_git_absence_is_tolerated(self):
        data = graph()
        data["nodes"].append(fn("big", "big_function()", "svc/app.py", 100, 300))
        data["links"] += [call("main", "big"), call("big", "used"), call("big", "chain_a")]
        with tempfile.TemporaryDirectory() as tmp:
            result = ch.find_hotspots(data, Path(tmp), top=3)
        self.assertEqual(result["symbols"][0]["node"]["label"], "big_function()")
        self.assertIn("churn ignored", result["note"])
        self.assertIn("Hotspots", ch.format_hotspots(result))


if __name__ == "__main__":
    unittest.main()
