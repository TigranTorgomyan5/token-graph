"""The ranked, compact query answer (graph_answer.py) and the document/vendored-bundle filters."""
from pathlib import Path
import importlib.util
import json
import re
import subprocess
import sys
import tempfile
import unittest

SCRIPTS = Path(__file__).resolve().parents[1] / "token-graph" / "scripts"


def load(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(SCRIPTS))
    spec.loader.exec_module(module)
    return module


def node(nid, label, src, loc="L10-20"):
    return {"id": nid, "label": label, "source_file": src, "source_location": loc, "file_type": "code"}


def edge(src, tgt, relation="calls", loc="L12"):
    return {"source": src, "target": tgt, "relation": relation, "confidence": "EXTRACTED", "source_location": loc, "source_file": "svc/handler.go"}


def sample_graph():
    """A handler that calls a validator, a store and a signer; one big utility file full of helpers."""
    nodes = [node("f_handler", "handler.go", "svc/handler.go", "L1"),
             node("f_util", "util.go", "svc/util.go", "L1"),
             node("f_store", "store.go", "svc/store.go", "L1"),
             node("f_sign", "sign.go", "svc/sign.go", "L1"),
             node("handler", ".exchangeWidget()", "svc/handler.go"),
             node("validate", "validateWidget()", "svc/handler.go", "L40-50"),
             node("lookup", "WidgetByToken()", "svc/store.go"),
             node("sign", "signWidget()", "svc/sign.go")]
    links = [edge("handler", "validate"), edge("handler", "lookup"), edge("handler", "sign"),
             edge("f_handler", "handler", "contains"), edge("f_util", "f_handler", "imports_from")]
    for i in range(30):                     # many small helpers in one utility file, all touching the handler
        nodes.append(node(f"h{i}", f"widgetHelper{i}()", "svc/util.go", f"L{i * 5 + 1}-{i * 5 + 4}"))
        links.append(edge("handler", f"h{i}"))
        links.append(edge("f_util", f"h{i}", "contains"))
    return {"directed": False, "nodes": nodes, "links": links}


class GraphAnswerTests(unittest.TestCase):
    def query(self, graph, question, budget=800):
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory) / "graphify-out"
            out.mkdir()
            (out / "graph.json").write_text(json.dumps(graph))
            result = subprocess.run([sys.executable, str(SCRIPTS / "token_graph.py"), "query", directory, question, "--budget", str(budget)],
                                    capture_output=True, text=True, timeout=60)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            return result.stdout

    def test_relations_are_shown_and_helpers_do_not_crowd_out_other_files(self):
        text = self.query(sample_graph(), "exchange widget token lookup sign validate")
        self.assertRegex(text, r"EDGE \.exchangeWidget\(\) --calls--> validateWidget\(\)")
        self.assertRegex(text, r"EDGE \.exchangeWidget\(\) --calls--> WidgetByToken\(\)")
        files = set(re.findall(r"src=([^\s\]]+)", text))
        self.assertTrue({"svc/store.go", "svc/sign.go", "svc/handler.go"} <= files, files)
        util_lines = [l for l in text.splitlines() if l.startswith("NODE ") and "src=svc/util.go" in l]
        self.assertLessEqual(len(util_lines), 6, "one file must not take the whole budget")
        self.assertIn("Not shown", text)

    def test_bare_file_nodes_are_summarised_not_listed(self):
        text = self.query(sample_graph(), "exchange widget")
        listed = [l for l in text.splitlines() if l.startswith("NODE ") and "loc=L1]" in l]
        self.assertEqual(listed, [], listed)

    def test_output_stays_inside_the_budget(self):
        for budget in (300, 800):
            text = self.query(sample_graph(), "exchange widget helper", budget)
            body = text.split("\n\n", 1)[1] if "\n\n" in text else text
            self.assertLessEqual(len(body), budget * 3 + 400, (budget, len(body)))

    def test_raw_query_env_restores_the_engine_output(self):
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory) / "graphify-out"
            out.mkdir()
            (out / "graph.json").write_text(json.dumps(sample_graph()))
            env = dict(__import__("os").environ, TOKEN_GRAPH_RAW_QUERY="1")
            result = subprocess.run([sys.executable, str(SCRIPTS / "token_graph.py"), "query", directory, "exchange widget"],
                                    capture_output=True, text=True, timeout=60, env=env)
            self.assertIn("community=", result.stdout)

    def test_no_match_message(self):
        self.assertIn("No matching nodes", self.query(sample_graph(), "zzzqqq"))


class NodeFilterTests(unittest.TestCase):
    def test_is_doc(self):
        nf = load("node_filters")
        self.assertTrue(nf.is_doc({"source_file": "docs/rules/security.md"}))
        self.assertTrue(nf.is_doc({"source_file": "x", "file_type": "document"}))
        self.assertFalse(nf.is_doc({"source_file": "internal/auth.go", "file_type": "code"}))
        self.assertFalse(nf.is_doc({"label": "concept"}))

    def test_minified_detection_uses_content_not_names(self):
        nf = load("node_filters")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "app.js").write_text("\n".join("function f%d(a, b) { return a + b; }" % i for i in range(5000)))
            (root / "blob.js").write_text("var a=" + "x+1;" * 40000 + "\n")           # one 160 KB line
            (root / "vendor-thing.min.js").write_text("a")
            (root / "tiny.js").write_text("var a=" + "x+1;" * 100)                  # long line, but small
            self.assertFalse(nf.looks_minified(root / "app.js"))
            self.assertTrue(nf.looks_minified(root / "blob.js"))
            self.assertTrue(nf.looks_minified(root / "vendor-thing.min.js"))
            self.assertFalse(nf.looks_minified(root / "tiny.js"))
            self.assertFalse(nf.looks_minified(root / "missing.js"))

    def test_scoped_graph_drops_docs_and_bundles_but_keeps_them_on_request(self):
        nf = load("node_filters")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "graphify-out").mkdir()
            (root / "ui").mkdir()
            (root / "ui" / "bundle.js").write_text("var a=" + "x+1;" * 40000 + "\n")
            nodes = [node("a", "Auth()", "svc/auth.go"), node("d", "Rules", "docs/rules.md"), node("b", "chunk", "ui/bundle.js")]
            graph = root / "graphify-out" / "graph.json"
            graph.write_text(json.dumps({"nodes": nodes, "links": [edge("a", "d"), edge("a", "b")]}))

            def ids(**kw):
                path = nf.scoped_graph(graph, tests=True, generated=True, external=True, **kw)
                return sorted(n["id"] for n in json.loads(path.read_text())["nodes"])
            self.assertEqual(ids(docs=False, vendored=False), ["a"])
            self.assertEqual(ids(docs=True, vendored=False), ["a", "d"])
            self.assertEqual(ids(docs=False, vendored=True), ["a", "b"])
            self.assertEqual(ids(), ["a", "b", "d"])


if __name__ == "__main__":
    unittest.main()
