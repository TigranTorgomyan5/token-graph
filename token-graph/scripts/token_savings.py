#!/usr/bin/env python3
"""Estimate Token Graph context savings using only local project files."""
import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("project", nargs="?", default=".", help="project root containing graphify-out/graph.json")
    parser.add_argument("--question", action="append", dest="questions", help="benchmark question; repeat for multiple questions")
    parser.add_argument("--queries", type=int, default=1, help="projected number of queries (default: 1)")
    parser.add_argument("--json", action="store_true", help="print machine-readable JSON")
    args = parser.parse_args()
    root = Path(args.project).expanduser().resolve()
    if args.queries < 1:
        parser.error("--queries must be at least 1")
    graph = root / "graphify-out" / "graph.json"
    if not graph.is_file():
        parser.error(f"Graph export not found: {graph}")
    from token_graph import engine_command, clean_env
    try:
        completed = subprocess.run(
            [*engine_command(), "benchmark-json", str(root), json.dumps(args.questions or [])],
            cwd=root, env=clean_env(), check=True, capture_output=True, text=True, timeout=90,
        )
        result = json.loads(completed.stdout)
    except subprocess.CalledProcessError as error:
        print(f"Local benchmark failed: {error.stderr.strip() or error}", file=sys.stderr)
        return 2
    except (OSError, subprocess.SubprocessError, ValueError, IndexError) as error:
        print(f"Could not calculate local graph estimate: {error}", file=sys.stderr)
        return 2
    saved = result["saved_tokens_per_query_estimate"]
    result["queries"] = args.queries
    result["total_saved_tokens_estimate"] = saved * args.queries
    result["projection_note"] = "Projected by multiplying the per-query estimate; actual query context can vary."
    data = json.loads(graph.read_text(encoding="utf-8"))
    data["benchmark"] = result
    graph.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    (graph.parent / "benchmark.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    if args.json:
        print(json.dumps(result, indent=2))
        return 0
    outcome = f"~{saved:,} estimated tokens saved/query" if saved >= 0 else f"~{abs(saved):,} estimated extra tokens/query"
    print(f"Corpus: {result['corpus_files']:,} files, {result['corpus_words']:,} words")
    print(f"Baseline estimate: ~{result['corpus_tokens_estimate']:,} tokens")
    print(f"Graph context estimate: ~{result['average_query_tokens_estimate']:,} tokens/query")
    print(f"Savings: {outcome}")
    if args.queries > 1:
        print(f"Projected for {args.queries:,} queries: ~{result['total_saved_tokens_estimate']:,} tokens")
    print(f"Reduction estimate: {result['reduction_ratio_estimate']}×")
    print(f"Method: {result['method']}")
    print("All scanning and calculations ran locally; no project data was uploaded.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
