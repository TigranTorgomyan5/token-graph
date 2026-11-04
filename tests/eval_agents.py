#!/usr/bin/env python3
"""Compare local Ollama models on the explore agent and the log analyzer.

Not a unit test (not collected by `unittest discover`): it needs a running Ollama, the models
installed, and the nh-sport-go project with a built graph. Ground truth below was read from that
project's source, never from model output. Each model is run once, so treat differences of a
fact or two as noise.

    python3 tests/eval_agents.py --project ~/Documents/Projects/nh-sport-go qwen3:14b-q4_K_M qwen3:8b
"""
from __future__ import annotations
import argparse
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / 'token-graph' / 'scripts'
QUESTION = 'Give an architecture overview: what does this service do and what are its main entry points?'
LOG = ROOT / 'tests' / 'fixtures' / 'logs' / 'casino-core-dashtz.log'

# (label, file, first_line, last_line): a model bullet "finds" the fact when one of its citations
# in that file overlaps the range. Lines verified against nh-sport-go source.
FACTS = [
    ('purpose (header comment)', 'cmd/nh-sport/main.go', 1, 2),
    ('process entry main()', 'cmd/nh-sport/main.go', 33, 33),
    ('config loaded', 'cmd/nh-sport/main.go', 39, 39),
    ('observability set up', 'cmd/nh-sport/main.go', 54, 64),
    ('router built', 'cmd/nh-sport/main.go', 74, 77),
    ('signal handling', 'cmd/nh-sport/main.go', 80, 81),
    ('warm-up worker', 'cmd/nh-sport/main.go', 85, 91),
    ('listen/serve', 'cmd/nh-sport/main.go', 94, 115),
    ('shutdown', 'cmd/nh-sport/main.go', 133, 146),
    ('recover middleware', 'cmd/nh-sport/router.go', 52, 52),
    ('health mounted', 'cmd/nh-sport/router.go', 67, 67),
    ('metrics endpoint', 'cmd/nh-sport/router.go', 68, 68),
    ('controllers mounted', 'cmd/nh-sport/router.go', 70, 70),
    ('module wiring', 'cmd/nh-sport/wiring.go', 22, 31),
    ('token/refresh/revoke routes', 'internal/sport/controller.go', 58, 63),
    ('bonus contract route', 'internal/bonus/controller.go', 45, 47),
]
# Statements known to be false for this project (config comes from environment variables).
KNOWN_WRONG = ('config file', 'configuration file')
CITE = re.compile(r'([\w./-]+\.\w+):(\d+)(?:-(\d+))?')


def run(cmd: list[str]) -> tuple[str, float]:
    started = time.monotonic()
    done = subprocess.run(cmd, capture_output=True, text=True, timeout=1500)
    return done.stdout, time.monotonic() - started


def score_explore(output: str) -> dict:
    # Sections whose header says "by code" (purpose, entry points, routes) are not model output: drop just those sections.
    kept, in_code = [], False
    for line in output.splitlines():
        if re.match(r'^\*\*[^*]+\*\*', line):
            in_code = 'by code' in line
        if not in_code:
            kept.append(line)
    model_part = '\n'.join(kept)
    bullets = [line for line in model_part.splitlines() if line.startswith('- ')]
    cites = [(f, int(a), int(b or a)) for line in bullets for f, a, b in CITE.findall(line)]
    found = [label for label, f, lo, hi in FACTS if any(cf == f and a <= hi and lo <= b for cf, a, b in cites)]
    # The whole answer, including the code-built sections: what the reader actually gets.
    all_cites = [(f, int(a), int(b or a)) for line in output.splitlines() if line.startswith('- ') for f, a, b in CITE.findall(line)]
    full = [label for label, f, lo, hi in FACTS if any(cf == f and a <= hi and lo <= b for cf, a, b in all_cites)]
    wrong = [p for p in KNOWN_WRONG if p in model_part.lower()]
    return {'bullets': len(bullets), 'found': len(found), 'full': len(full), 'of': len(FACTS),
            'missing': [l for l, *_ in FACTS if l not in found], 'missing_full': [l for l, *_ in FACTS if l not in full], 'wrong': wrong}


def score_logs(output: str) -> dict:
    findings = [line for line in output.splitlines() if line.startswith(('- OBSERVATION', '- HYPOTHESIS', '- SUGGESTED'))]
    text = ' '.join(findings)
    return {'findings': len(findings), 'observation': any(l.startswith('- OBSERVATION') for l in findings),
            'names_target': 'wallet-service-service-grpc' in text, 'rate_in_report': 'every ~3.0s' in output,
            'mentions_rate_in_findings': bool(re.search(r'3(\.0)?\s*s|every 3', text))}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('models', nargs='+')
    parser.add_argument('--project', type=Path, required=True, help='nh-sport-go checkout with graphify-out/graph.json')
    args = parser.parse_args()
    rows = []
    for model in args.models:
        out, secs = run([sys.executable, str(SCRIPTS / 'explore_agent.py'), str(args.project.expanduser()), QUESTION, '--model', model])
        explore = score_explore(out)
        out2, secs2 = run([sys.executable, str(SCRIPTS / 'log_analyzer.py'), str(LOG), 'Why is this service failing and what should I check?', '--model', model])
        logs = score_logs(out2)
        rows.append((model, explore, secs, logs, secs2))
        print(f'\n== {model}\n  explore: model {explore["found"]}/{explore["of"]}, whole answer {explore["full"]}/{explore["of"]}, {explore["bullets"]} bullets, {secs:.0f}s, known-wrong: {explore["wrong"] or "none"}'
              f'\n    missing from model bullets: {", ".join(explore["missing"]) or "none"}'
              f'\n    missing from whole answer: {", ".join(explore["missing_full"]) or "none"}'
              f'\n  logs: {logs["findings"]} findings, observation={logs["observation"]}, names target={logs["names_target"]}, '
              f'rate in report={logs["rate_in_report"]}, rate in findings={logs["mentions_rate_in_findings"]}, {secs2:.0f}s', flush=True)
    print('\n| model | model facts | whole answer | bullets | explore s | wrong | log findings | log s |\n|---|---|---|---|---|---|---|---|')
    for model, e, s1, l, s2 in rows:
        print(f'| {model} | {e["found"]}/{e["of"]} | {e["full"]}/{e["of"]} | {e["bullets"]} | {s1:.0f} | {len(e["wrong"])} | {l["findings"]} | {s2:.0f} |')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
