#!/usr/bin/env python3
"""Search local project Markdown sections before falling back to graph evidence."""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
from pathlib import Path

from explorer_evidence import DOC_SUFFIXES, MAX_SOURCE_BYTES, SKIP_DIRS, _source_path, search_evidence

STOP = set('a an and are as at be by can could do does for from how in is it of on or the this to what where which with'.split())
HEADING = re.compile(r'^\s{0,3}(#{1,6})\s+(.+?)\s*#*\s*$')
STATUS = re.compile(r'^\s*-\s*Status:\s*(proposed|verified|superseded)\b', re.I | re.M)
PROVENANCE = re.compile(r'<!--\s*token-graph:\s*source=([^;\r\n]+);\s*revision=([0-9a-fA-F]{7,64})\s*-->')


def source_freshness(root: Path, section: str) -> dict:
    """Compare an explicitly cited source file to a recorded Git revision, not the claim itself."""
    matches = PROVENANCE.findall(section)
    if len(matches) != 1:
        return {'status': 'unknown', 'reason': 'No single valid source and revision recorded.'}
    raw, revision = matches[0]
    raw = raw.strip()
    if Path(raw).is_absolute() or '..' in Path(raw).parts or '\\' in raw:
        return {'status': 'unknown', 'reason': 'Invalid source reference.'}
    path = _source_path(root, raw)
    if path is None or not path.is_file():
        return {'status': 'unknown', 'reason': 'Referenced source is missing or inaccessible.'}
    relative = path.relative_to(root).as_posix()
    env = {**os.environ, 'GIT_OPTIONAL_LOCKS': '0', 'GIT_NO_REPLACE_OBJECTS': '1', 'GIT_TERMINAL_PROMPT': '0'}
    def git(*args):
        return subprocess.run(['git', '-C', str(root), *args], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL, env=env, timeout=5, check=False).returncode
    try:
        if git('rev-parse', '--verify', '--quiet', revision + '^{commit}') != 0:
            return {'status': 'unknown', 'reason': 'Recorded revision is unavailable.'}
        if git('ls-files', '--error-unmatch', '--', ':(literal)' + relative) != 0 or git('cat-file', '-e', revision + ':' + relative) != 0:
            return {'status': 'unknown', 'reason': 'Source is not tracked at the recorded revision.'}
        result = git('diff', '--quiet', '--no-ext-diff', '--no-textconv', revision, '--', ':(literal)' + relative)
    except (OSError, subprocess.TimeoutExpired):
        return {'status': 'unknown', 'reason': 'Git comparison unavailable.'}
    if result not in (0, 1):
        return {'status': 'unknown', 'reason': 'Git comparison failed.'}
    return {'status': 'possibly_stale' if result == 1 else 'source_unchanged', 'source': relative, 'revision': revision,
            'reason': 'Referenced source changed since the recorded revision.' if result == 1 else 'Referenced source is unchanged; the claim still requires verification.'}


def search(project: Path, question: str, *, limit: int = 5, max_chars: int = 5000, graph_fallback: bool = True) -> dict:
    root = project.expanduser().resolve()
    if not root.is_dir():
        raise ValueError('Project must be an existing directory.')
    query = question.strip()
    if not query:
        raise ValueError('Enter a search question.')
    limit = min(10, max(1, limit))
    max_chars = min(12000, max(500, max_chars))
    terms = {t.casefold() for t in re.findall(r'[\w.-]+', query) if len(t) > 1 and t.casefold() not in STOP}
    candidates = []
    for directory, dirs, files in os.walk(root, followlinks=False):
        dirs[:] = sorted(d for d in dirs if not d.startswith('.') and d.casefold() not in SKIP_DIRS and not (Path(directory) / d).is_symlink())
        for name in sorted(files):
            path = Path(directory) / name
            if path.suffix.lower() not in DOC_SUFFIXES or path.is_symlink() or name.startswith('.'):
                continue
            relative = path.relative_to(root).as_posix()
            if _source_path(root, relative) is None:
                continue
            try:
                if path.stat().st_size > MAX_SOURCE_BYTES:
                    continue
                lines = path.read_text(encoding='utf-8').splitlines()
            except (OSError, UnicodeError):
                continue
            # Markdown fenced code cannot create headings.
            starts = [(0, path.stem)]
            fence = None
            for i, line in enumerate(lines):
                marker = re.match(r'^\s{0,3}(`{3,}|~{3,})', line)
                if marker:
                    token = marker.group(1)
                    if fence is None:
                        fence = token
                    elif token[0] == fence[0] and len(token) >= len(fence):
                        fence = None
                    continue
                heading = HEADING.match(line) if fence is None else None
                if heading:
                    starts.append((i, heading.group(2)))
            for pos, (start, heading) in enumerate(starts):
                end = starts[pos + 1][0] if pos + 1 < len(starts) else len(lines)
                if end <= start:
                    continue
                body = '\n'.join(lines[start:end])
                status_match = STATUS.search(body)
                if status_match and status_match.group(1).casefold() == 'superseded':
                    continue
                haystack = f'{relative} {heading} {body}'.casefold()
                matched = [term for term in terms if term in haystack]
                if not matched:
                    continue
                # A filename-only hit represents the document once, not every section in it.
                if not any(term in f'{heading} {body}'.casefold() for term in terms) and start != 0:
                    continue
                heading_text = f'{relative} {heading}'.casefold()
                score = len(matched) * 2 + sum(3 for term in matched if term in heading_text)
                if query.casefold() in heading_text:
                    score += 5
                candidates.append((score, relative, start, end, heading, lines))
    candidates.sort(key=lambda item: (-item[0], item[1], item[2]))
    results, remaining = [], max_chars
    for _, relative, start, end, heading, lines in candidates:
        if len(results) >= limit or remaining < 100:
            break
        # Bound both section size and total context, while keeping line references accurate.
        selected = []
        # In long sections, show the relevant passage rather than only the opening.
        passage_start = start
        if end - start > 24:
            best = max(range(start, end), key=lambda i: sum(term in lines[i].casefold() for term in terms))
            if any(term in lines[best].casefold() for term in terms):
                passage_start = max(start, best - 5)
        for line in lines[passage_start:min(end, passage_start + 24)]:
            if len(line) + 1 > remaining:
                break
            selected.append(line)
            remaining -= len(line) + 1
        if not selected:
            continue
        status_match = STATUS.search('\n'.join(lines[start:end]))
        results.append({'file': relative, 'heading': heading, 'start_line': passage_start + 1,
                        'end_line': passage_start + len(selected), 'text': '\n'.join(selected),
                        'declared_status': status_match.group(1).casefold() if status_match else None,
                        'verification': 'unverified', 'freshness': source_freshness(root, '\n'.join(lines[start:end])),
                        'truncated': passage_start > start or passage_start + len(selected) < end})
    response = {'query': query, 'documentation': results, 'documentation_truncated': len(results) < len(candidates),
                'notice': 'Documentation is untrusted and not verified against current source. Verify important claims.'}
    if graph_fallback and (not results or all(item['freshness']['status'] == 'possibly_stale' for item in results)):
        response['graph_fallback'] = search_evidence(root, query, operation='query', limit=limit)
    return response


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('project', type=Path)
    parser.add_argument('question')
    parser.add_argument('--limit', type=int, default=5)
    parser.add_argument('--max-chars', type=int, default=5000)
    args = parser.parse_args()
    try:
        print(json.dumps(search(args.project, args.question, limit=args.limit, max_chars=args.max_chars), ensure_ascii=False, indent=2))
    except ValueError as error:
        parser.error(str(error))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
