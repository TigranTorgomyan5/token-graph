"""Deterministic Explorer evidence, citation, classification, and read-only regressions."""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "token-graph" / "scripts"
sys.path.insert(0, str(SCRIPTS))
spec = importlib.util.spec_from_file_location("explorer_evidence", SCRIPTS / "explorer_evidence.py")
evidence = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = evidence
spec.loader.exec_module(evidence)


class ExplorerEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="explorer-evidence-")
        self.root = Path(self.temp.name)
        for directory in ("src", "tests", "benchmarks", "docs", "gen"):
            (self.root / directory).mkdir()
        (self.root / "src/auth.py").write_text(
            "def validate_token(token):\n    return token == 'ok'\n\n"
            "def auth_module():\n    return validate_token('ok')\n"
        )
        (self.root / "src/api.py").write_text(
            "from auth import validate_token\n"
            "def middleware(token):\n    return validate_token(token)\n"
        )
        (self.root / "tests/test_auth.py").write_text(
            "def test_validate_token():\n    assert validate_token('ok')\n"
        )
        (self.root / "benchmarks/eval_accuracy.py").write_text(
            "def validate_token_eval():\n    return 'fixture'\n"
        )
        (self.root / "docs/auth.md").write_text(
            "# Auth guide\nCall validate_token before accepting the request.\n"
        )
        (self.root / "gen/client.py").write_text("class GeneratedClient: pass\n")
        nodes = [
            {"id": "auth_validate_token", "label": "validate_token()", "source_file": "src/auth.py", "source_location": "L1"},
            {"id": "auth_auth_module", "label": "auth_module()", "source_file": "src/auth.py", "source_location": "L4"},
            {"id": "api_middleware", "label": "middleware()", "source_file": "src/api.py", "source_location": "L2"},
            {"id": "api_module", "label": "api", "source_file": "src/api.py", "source_location": "L1"},
            {"id": "test_validate_token", "label": "test_validate_token()", "source_file": "tests/test_auth.py", "source_location": "L1"},
            {"id": "bench_validate_token", "label": "validate_token_eval()", "source_file": "benchmarks/eval_accuracy.py", "source_location": "L1"},
            {"id": "docs_auth", "label": "Auth guide", "source_file": "docs/auth.md", "source_location": "L1"},
            {"id": "generated_client", "label": "GeneratedClient", "source_file": "gen/client.py", "source_location": "L1"},
        ]
        edges = [
            {"source": "api_middleware", "target": "auth_validate_token", "relation": "calls", "source_file": "src/api.py", "source_location": "L3"},
            {"source": "api_middleware", "target": "auth_validate_token", "relation": "references", "source_file": "src/api.py", "source_location": "L3"},
            {"source": "api_module", "target": "auth_validate_token", "relation": "imports", "source_file": "src/api.py", "source_location": "L1"},
            {"source": "docs_auth", "target": "auth_validate_token", "relation": "references", "source_file": "docs/auth.md", "source_location": "L2"},
            {"source": "api_module", "target": "auth_validate_token", "relation": "references"},
        ]
        output = self.root / "graphify-out"
        output.mkdir()
        self.graph_file = output / "graph.json"
        self.graph_file.write_text(json.dumps({"nodes": nodes, "links": edges}, indent=2))
        # Ensure source mtimes do not appear newer than the just-written index.
        now = self.graph_file.stat().st_mtime
        for source in self.root.rglob("*"):
            if source.is_file() and source != self.graph_file:
                os.utime(source, (now - 1, now - 1))

    def tearDown(self):
        self.temp.cleanup()

    def run_search(self, query, operation, **kwargs):
        return evidence.search_evidence(self.root, query, operation=operation, **kwargs)

    def test_graph_query_and_path_reuse_existing_graphify_engine(self):
        query = self.run_search("validate_token", "query")
        self.assertEqual(query["status"], "complete")
        self.assertTrue(any(item["symbol"] == "validate_token()" for item in query["results"]))
        path = self.run_search("middleware", "path", target="validate_token")
        self.assertEqual(path["status"], "complete")
        self.assertTrue(any(item["symbol"] == "middleware()" for item in path["results"]))
        self.assertTrue(any(item["symbol"] == "validate_token()" for item in path["results"]))

    def test_file_discovery_returns_relative_cited_paths(self):
        result = self.run_search("auth.py", "find_files")
        self.assertEqual(result["status"], "complete")
        self.assertEqual({item["file"] for item in result["results"]}, {"src/auth.py"})
        self.assertEqual(result["results"][0]["verification"], "verified")

    def test_symbol_lookup_excludes_tests_and_eval_by_default(self):
        result = self.run_search("validate_token", "find_symbol")
        self.assertEqual(result["status"], "complete")
        self.assertTrue(result["results"])
        self.assertTrue(all(item["source_type"] == "production" for item in result["results"]))
        self.assertTrue(all(item["verification"] == "verified" for item in result["results"]))

    def test_symbol_lookup_ignores_pathless_duplicate_nodes(self):
        graph = json.loads(self.graph_file.read_text())
        graph["nodes"].insert(0, {"id": "duplicate_auth", "label": "validate_token()", "source_file": "", "source_location": ""})
        self.graph_file.write_text(json.dumps(graph))
        result = self.run_search("validate_token", "find_symbol")
        self.assertEqual(result["status"], "complete")
        self.assertTrue(result["results"])
        self.assertTrue(all(item["file"] for item in result["results"]))
        self.assertFalse(any(item["id"] == "duplicate_auth" for item in result["results"]))

    def test_callers_and_callees_follow_graph_direction(self):
        callers = self.run_search("validate_token", "find_callers")
        callees = self.run_search("middleware", "find_callees")
        self.assertEqual(callers["results"][0]["source"]["symbol"], "middleware()")
        self.assertEqual(callers["results"][0]["target"]["symbol"], "validate_token()")
        self.assertEqual(callees["results"][0]["target"]["symbol"], "validate_token()")
        self.assertEqual(callers["results"][0]["verification"], "verified")

    def test_reference_lookup_returns_source_evidence_and_graph_only_marker(self):
        result = self.run_search("validate_token", "find_references", include_evaluation=True)
        self.assertEqual(result["status"], "complete")
        self.assertTrue(any(item["verification"] == "verified" for item in result["results"]))
        self.assertTrue(any(item["verification"] == "graph_only" for item in result["results"]))

    def test_dependency_lookup_uses_import_edges(self):
        result = self.run_search("api", "find_dependencies")
        self.assertTrue(any(item["relation"] == "imports" for item in result["results"]))
        self.assertEqual(result["results"][0]["source"]["symbol"], "api")

    def test_document_search_finds_content_with_exact_line_citation(self):
        result = self.run_search("validate_token before accepting", "find_docs")
        doc = next(item for item in result["results"] if item["file"] == "docs/auth.md")
        self.assertEqual(doc["source_type"], "documentation")
        self.assertEqual(doc["matched_line"], 2)
        self.assertEqual((doc["start_line"], doc["end_line"]), (1, 2))
        self.assertEqual(doc["verification"], "verified")
        self.assertIn("2: Call validate_token", doc["evidence"])

    def test_source_citations_use_exact_current_source_lines(self):
        result = self.run_search("src/auth.py", "find_source", line=2)
        item = result["results"][0]
        self.assertEqual(item["file"], "src/auth.py")
        self.assertEqual(item["matched_line"], 2)
        self.assertEqual((item["start_line"], item["end_line"]), (1, 4))
        self.assertEqual(item["verification"], "verified")
        self.assertIn("return token", item["evidence"])

    def test_missing_and_stale_indexes_are_reported(self):
        missing = self.root / "empty"
        missing.mkdir()
        self.assertEqual(evidence.search_evidence(missing, "x")["status"], "missing_graph")
        source = self.root / "src/auth.py"
        future = self.graph_file.stat().st_mtime + 30
        os.utime(source, (future, future))
        result = self.run_search("validate_token()", "find_symbol")
        self.assertEqual(result["results"][0]["verification"], "stale_index")

    def test_source_classification_distinguishes_all_required_kinds(self):
        result = self.run_search("validate_token", "find_symbol", include_tests=True, include_evaluation=True, include_generated=True)
        types = {item["source_type"] for item in result["results"]}
        self.assertTrue({"production", "test", "evaluation_fixture"}.issubset(types))
        self.assertEqual(evidence.classify_source("docs/auth.md"), "documentation")
        self.assertEqual(evidence.classify_source("examples/demo.py"), "example")
        self.assertEqual(evidence.classify_source("gen/client.py"), "generated")

    def test_empty_results_and_headroom_style_query_excludes_evaluation_evidence(self):
        self.assertEqual(self.run_search("NoSuchSymbol", "find_symbol")["status"], "insufficient_evidence")
        graph_result = {
            "text": "NODE validate_token_eval [src=benchmarks/eval_accuracy.py loc=L1]",
            "nodes": {"bench_validate_token": 0},
            "seeds": ["bench_validate_token"],
        }
        with patch.object(evidence, "_query_graph", return_value=graph_result):
            result = self.run_search("What is this project's purpose?", "query")
        self.assertNotIn("bench_validate_token", {item["id"] for item in result["results"]})
        self.assertNotIn("purpose", result)
        self.assertEqual(result["status"], "insufficient_evidence")

    def test_evidence_lookup_does_not_modify_project_state(self):
        before = {path.relative_to(self.root): path.read_bytes() for path in self.root.rglob("*") if path.is_file()}
        for operation, query in (("find_files", "auth.py"), ("find_symbol", "validate_token"),
                                 ("find_callers", "validate_token"), ("query", "validate_token")):
            self.run_search(query, operation)
        after = {path.relative_to(self.root): path.read_bytes() for path in self.root.rglob("*") if path.is_file()}
        self.assertEqual(after, before)


if __name__ == "__main__":
    unittest.main()
