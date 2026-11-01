#!/usr/bin/env python3
"""Prepare Token Graph's own dependencies; never install the Graphify package."""
from __future__ import annotations
import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys
import venv

ROOT = Path(__file__).resolve().parents[1]
EXTRAS = {
    'sql': ['tree-sitter-sql'],
    'pdf': ['pypdf>=6.16.1', 'markdownify'],
    'office': ['python-docx', 'openpyxl'],
    'plot': ['matplotlib', 'pillow>=12.3.0'],
    'ollama': ['openai'],
    'media': ['faster-whisper'],
}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--download-deps', action='store_true', help='explicitly allow one-time package downloads from PyPI; no project files are sent')
    mode.add_argument('--wheel-dir', type=Path, help='install offline from a directory of local wheels')
    parser.add_argument('--locked', action='store_true', help='use the exact dependency versions tested with Python 3.14')
    parser.add_argument('--extras', nargs='+', choices=sorted(EXTRAS), default=[], help='optional local features to prepare')
    parser.add_argument('--installer', choices=['auto', 'pip', 'uv'], default='auto', help='pip needs no separate installer; uv can also use an existing local package cache')
    parser.add_argument('--agent-project', type=Path, help='register the Codex exploration agent for this project instead of globally')
    parser.add_argument('--no-agent', action='store_true', help='skip registering the optional Codex exploration agent')
    args = parser.parse_args(argv)
    if sys.version_info < (3, 11):
        parser.error('Python 3.11 or newer is required. Python 3.14 is the tested runtime.')
    if args.locked and sys.version_info[:2] != (3, 14):
        parser.error('--locked requires Python 3.14; omit it to resolve compatible parser versions for your Python')
    wheel_dir = args.wheel_dir.expanduser().resolve() if args.wheel_dir else None
    if wheel_dir and not wheel_dir.is_dir():
        parser.error('--wheel-dir must be an existing local directory')
    uv = shutil.which('uv') if args.installer != 'pip' else None
    if args.installer == 'uv' and not uv:
        parser.error('uv was requested but is not installed; use --installer pip instead')
    runtime = ROOT / '.runtime'
    python = runtime / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    # Setup installs dependencies only; analysis runs under a separate offline policy.
    env = dict(os.environ, UV_PYTHON_DOWNLOADS='never', PIP_CONFIG_FILE=os.devnull, PIP_DISABLE_PIP_VERSION_CHECK='1')
    for key in list(env):
        if key.startswith(('TOKEN_GRAPH_', 'PIP_INDEX_', 'PIP_EXTRA_', 'UV_INDEX', 'UV_DEFAULT_INDEX')) or key in {'PYTHONPATH', 'PYTHONHOME', 'PIP_NO_INDEX', 'PIP_FIND_LINKS'}:
            env.pop(key, None)
    requirements = ROOT / ('requirements.lock' if args.locked else 'requirements.txt')
    packages = [package for extra in args.extras for package in EXTRAS[extra]]
    try:
        if not python.is_file():
            if uv:
                subprocess.run([uv, 'venv', '--offline', '--python', sys.executable, str(runtime)], env=env, check=True)
            else:
                venv.EnvBuilder(with_pip=True).create(runtime)
        if uv:
            command = [uv, 'pip', 'install', '--python', str(python), '-r', str(requirements)]
            if args.download_deps:
                command += ['--default-index', 'https://pypi.org/simple']
            else:
                command += ['--offline']
            if wheel_dir:
                command += ['--no-index', '--find-links', str(wheel_dir)]
        else:
            check = subprocess.run([str(python), '-I', '-c', 'import pip'], env=env, capture_output=True)
            if check.returncode:
                subprocess.run([str(python), '-I', '-m', 'ensurepip', '--upgrade'], env=env, check=True)
            command = [str(python), '-I', '-m', 'pip', '--isolated', '--disable-pip-version-check', 'install', '-r', str(requirements)]
            if args.download_deps:
                command += ['--index-url', 'https://pypi.org/simple']
            else:
                command += ['--no-index']
            if wheel_dir:
                command += ['--find-links', str(wheel_dir)]
        subprocess.run([*command, *packages], env=env, check=True)
        # Verify all required modules, not merely the presence of a venv directory.
        modules = ['networkx', 'numpy', 'rapidfuzz', 'tree_sitter']
        modules += [line.split('<')[0].split('>')[0].split('=')[0].replace('-', '_') for line in (ROOT / 'requirements.txt').read_text().splitlines() if line.startswith('tree-sitter-')]
        probe = 'import importlib; ' + '; '.join(f'importlib.import_module({name!r})' for name in modules)
        subprocess.run([str(python), '-I', '-c', probe], env=env, check=True)
    except (OSError, subprocess.CalledProcessError) as error:
        print(f'Token Graph setup failed: {error}', file=sys.stderr)
        if not args.download_deps and not wheel_dir:
            print('No network download was attempted. On a fresh machine run setup_runtime.py --download-deps, or provide --wheel-dir /path/to/wheels for offline setup. Graphify is not required.', file=sys.stderr)
        return 1
    print('Token Graph runtime ready. Graphify was not installed. No model was downloaded.')
    codex_home = Path(os.environ.get('CODEX_HOME', Path.home() / '.codex')).expanduser().resolve()
    if not args.no_agent and ROOT.resolve() == (codex_home / 'skills' / 'token-graph').resolve():
        command = [sys.executable, str(ROOT / 'scripts' / 'install_explorer_agent.py')]
        if args.agent_project:
            command += ['--project', str(args.agent_project)]
        subprocess.run(command, check=False)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
