#!/usr/bin/env python3
"""Run Token Graph's bundled engine without an installed Graphify executable."""
from __future__ import annotations
import json
import os
import re
from pathlib import Path
import sys

PACKAGE = Path(__file__).resolve().parents[1]
RUNTIME = PACKAGE / '.runtime' / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
os.environ.setdefault('PYTHONHASHSEED', '0')
if __name__ == '__main__' and RUNTIME.is_file() and Path(sys.prefix).resolve() != RUNTIME.parent.parent.resolve():
    os.execv(str(RUNTIME), [str(RUNTIME), str(Path(__file__).resolve()), *sys.argv[1:]])
sys.path.insert(0, str(PACKAGE / 'vendor'))


class UserOutput:
    """Point missing optional dependencies at this skill's setup, never upstream installation."""
    def __init__(self, stream):
        self.stream = stream

    def write(self, text):
        extra_map = {'svg': 'plot', 'office': 'office', 'pdf': 'pdf', 'sql': 'sql', 'video': 'media', 'ollama': 'ollama'}
        def suggestion(match):
            extra = extra_map.get(match.group(1), '')
            command = f'python3 "{PACKAGE / "scripts" / "setup_runtime.py"}" --download-deps'
            return command + (f' --extras {extra}' if extra else '')
        text = re.sub(r"pip install [\"']?graphifyy\[([a-z]+)\][\"']?", suggestion, text)
        return self.stream.write(text)

    def flush(self):
        return self.stream.flush()

    def __getattr__(self, name):
        return getattr(self.stream, name)


def main() -> None:
    args = sys.argv[1:]
    allowed = {'extract', 'update', 'cluster-only', 'query', 'path', 'explain', 'affected', 'god-nodes', 'export', 'merge-graphs', 'diagnose', 'reflect', 'watch', 'tree', 'benchmark-json', 'benchmark', 'save-result', 'cache-check', 'merge-chunks', 'merge-semantic', 'label'}
    if not args or args[0] not in allowed:
        raise SystemExit('Token Graph supports local analysis commands only; cloud, URLs, remote databases and hosted backends are disabled.')
    if any('://' in arg for arg in args):
        raise SystemExit('Token Graph accepts local paths only, not URLs.')
    backend = None
    for i, arg in enumerate(args):
        if arg == '--backend' and i + 1 < len(args):
            backend = args[i + 1]
        elif arg.startswith('--backend='):
            backend = arg.split('=', 1)[1]
    if backend not in {None, 'ollama'}:
        raise SystemExit('Only explicit local Ollama processing is supported; hosted model backends are disabled.')
    from offline_policy import install
    install(backend == 'ollama')
    try:
        import networkx, tree_sitter, numpy, rapidfuzz
    except ImportError as error:
        raise SystemExit(f'Missing Token Graph dependency: {error}. Run scripts/setup_runtime.py once.') from error
    if args[0] == 'benchmark-json':
        from graphify.detect import detect
        from graphify.benchmark import run_benchmark
        root = Path(args[1]).resolve()
        questions = json.loads(args[2]) if len(args) > 2 else []
        detected = detect(root, google_workspace=False)
        result = run_benchmark(str(root / 'graphify-out' / 'graph.json'), corpus_words=detected['total_words'], questions=questions or None)
        if 'error' in result and not questions:
            graph = json.loads((root / 'graphify-out' / 'graph.json').read_text(encoding='utf-8'))
            samples = [str(node.get('label', '')) for node in graph.get('nodes', []) if node.get('label')][:8]
            result = run_benchmark(str(root / 'graphify-out' / 'graph.json'), corpus_words=detected['total_words'], questions=samples)
        if 'error' in result:
            raise SystemExit(result['error'])
        baseline, average = result['corpus_tokens'], result['avg_query_tokens']
        print(json.dumps({'corpus_words': detected['total_words'], 'corpus_files': detected['total_files'], 'corpus_tokens_estimate': baseline, 'average_query_tokens_estimate': average, 'saved_tokens_per_query_estimate': baseline - average, 'reduction_ratio_estimate': result['reduction_ratio'], 'benchmark_questions': len(result['per_question']), 'method': 'Local heuristic: ~133 tokens per 100 corpus words; ~1 token per 4 context characters'}))
        return
    # Pin deterministic hashing before entry to avoid upstream re-exec escaping the guard.
    os.environ['PYTHONHASHSEED'] = '0'
    sys.stdout = UserOutput(sys.stdout)
    sys.stderr = UserOutput(sys.stderr)
    from graphify.cli import dispatch_command
    sys.argv = ['token-graph-engine', *args]
    dispatch_command(args[0])


if __name__ == '__main__':
    main()
