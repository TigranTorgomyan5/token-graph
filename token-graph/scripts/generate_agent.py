#!/usr/bin/env python3
"""Generate a project graph with an installed local Ollama model."""
from __future__ import annotations
import argparse
from pathlib import Path
import sys
import time
from explorer_server import Jobs, ollama_models


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('project', type=Path)
    parser.add_argument('--model', help='installed local Ollama model; defaults to qwen3:8b or the first available model')
    args = parser.parse_args()
    project = args.project.expanduser().resolve()
    if not project.is_dir():
        parser.error('project must be an existing directory')
    try:
        models = ollama_models()
        if not models:
            raise ValueError('No local Ollama models are installed. Install a model and retry.')
        model = args.model or ('qwen3:8b' if 'qwen3:8b' in models else models[0])
        if model not in models:
            raise ValueError(f'Local model {model!r} is not installed.')
        print(f'Generating graph for {project} with {model} locally.', flush=True)
        jobs = Jobs()
        jobs.start(project, model)
        last = None
        while True:
            job = jobs.status()
            if job['step'] != last:
                print(job['step'], flush=True)
                last = job['step']
            if job['state'] != 'running':
                if job['state'] == 'error':
                    print(job['message'], file=sys.stderr)
                    return 1
                print(f"Graph ready: {project / 'graphify-out' / 'graph.json'}")
                return 0
            time.sleep(1)
    except (OSError, ValueError) as error:
        print(str(error), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
