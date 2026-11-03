#!/usr/bin/env python3
"""Build the single-file project explorer from viewer-src/.

Edit the small files in viewer-src/, then run:  python viewer-src/build.py
It writes token-graph/assets/project-explorer.html and the root index.html
(identical copies). The output is one self-contained HTML file, so it still
works opened from disk (file://) and when served by explorer_server.py.

  --check   exit 1 if the built files are out of date (used by the tests)

Files in js/ are fragments of one script (01-core.js opens the IIFE,
11-dashboard.js closes it); they are concatenated in template order.
"""
from pathlib import Path
import re
import sys

SRC = Path(__file__).resolve().parent
ROOT = SRC.parent
OUTPUTS = (ROOT / 'token-graph' / 'assets' / 'project-explorer.html', ROOT / 'index.html')
INCLUDE = re.compile(rb'^\s*<!-- @include (\S+) -->\s*$')


def build() -> bytes:
    out = []
    for line in (SRC / 'template.html').read_bytes().splitlines(keepends=True):
        match = INCLUDE.match(line)
        out.append((SRC / match.group(1).decode()).read_bytes() if match else line)
    return b''.join(out)


def main(argv: list[str]) -> int:
    html = build()
    stale = [p for p in OUTPUTS if not p.exists() or p.read_bytes() != html]
    if '--check' in argv:
        for p in stale:
            print(f'out of date: {p.relative_to(ROOT)} (run python viewer-src/build.py)')
        return 1 if stale else 0
    for p in stale:
        p.write_bytes(html)
        print(f'wrote {p.relative_to(ROOT)} ({len(html):,} bytes)')
    if not stale:
        print('viewer already up to date')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
