#!/usr/bin/env python3
"""Ground-truth checks for the local explore agent (and the log analyzer) on the nh Go services.

Not a unit test (not collected by `unittest discover`): it needs a running Ollama, the model, and the
service checkouts with built graphs next to each other under --root. Every line range and string below
was read from the services' source, never from model output (verified 2026-11-04). Each case runs the
model once, so treat a difference of a fact or two between runs or models as noise.

    python3 tests/eval_agents.py                          # every case, qwen3:14b-q4_K_M
    python3 tests/eval_agents.py --only identity wallet   # cases whose name contains a word
    python3 tests/eval_agents.py --models qwen3:14b-q4_K_M qwen3:8b --logs

Per case it reports:
  model   facts the model's own steps cite (a cited range overlaps the fact's lines)
  answer  facts anywhere in the answer, the code-built sections included: what the reader gets
  code    strings the code-built sections must contain (routes, bodies, statuses, related endpoints)
  wrong   phrases known to be false for that code
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
LOG = ROOT / 'tests' / 'fixtures' / 'logs' / 'casino-core-dashtz.log'
OVERVIEW = 'Give an architecture overview: what does this service do and what are its main entry points?'
CITE = re.compile(r'([\w./-]+\.\w+):(\d+)(?:-(\d+))?')

# name, project folder, question, facts [(label, file, first, last)], code strings, known-wrong phrases
CASES = [
    ('sport overview', 'nh-sport-go', OVERVIEW, [
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
    ], [], ('config file', 'configuration file')),

    ('identity login', 'nh_identity', 'How does the login endpoint verify the password and issue tokens?', [
        ('session created', 'internal/modules/auth/auth.go', 158, 162),
        ('authorization code', 'internal/modules/auth/auth.go', 163, 167),
        ('dummy hash for unknown player', 'internal/modules/auth/auth.go', 193, 196),
        ('real hash check', 'internal/modules/auth/auth.go', 206, 217),
        ('deleted account refused', 'internal/modules/auth/auth.go', 220, 221),
        ('code and verifier required', 'internal/modules/auth/oauth.go', 47, 50),
        ('token pair minted', 'internal/modules/auth/oauth.go', 93, 98),
        ('refresh token created', 'internal/modules/auth/oauth.go', 100, 104),
    ], ['handler login of POST /v1/authentication/login', 'handler exchangeToken of POST /v1/oauth/token',
        '`phoneNumber` string · `password` string · `codeChallenge` string'], ()),

    ('wallet balance', 'nh-wallet-adapter-go', 'How is a balance request handled?', [
        ('handler calls the service', 'internal/walletapi/endpoints.go', 170, 170),
        ('wallet resolved', 'internal/wallet/service.go', 240, 240),
        ('player unavailable -> technical failure', 'internal/wallet/service.go', 241, 245),
        ('result built', 'internal/wallet/service.go', 250, 250),
        ('non-numeric id -> WALLET_NOT_FOUND', 'internal/wallet/service.go', 595, 607),
        ('backend wallet lookup', 'internal/wallet/service.go', 613, 615),
    ], ['GetBalanceByWalletID, called by handler balance', 'resolveByWalletID, called by GetBalanceByWalletID'], ()),

    ('player self exclusion', 'nh-player-service-go', 'How does self exclusion work?', [
        ('allowed durations', 'internal/modules/security/security.go', 88, 91),
        ('overlap check', 'internal/modules/security/security.go', 96, 104),
        ('exclusion inserted', 'internal/modules/security/security.go', 110, 114),
        ('sessions revoked', 'internal/modules/security/security.go', 117, 120),
        ('refresh tokens revoked', 'internal/modules/security/security.go', 183, 185),
        ('devices deleted', 'internal/modules/security/security.go', 186, 193),
    ], ['revokeAllSessions, called by handler selfExclude', '409 when `overlap`'],
     ("invalidates the user's session", 'invalidate the session', "invalidates the session")),

    ('player favourites', 'nh-player-service-go', 'How are favourites added?', [
        ('existing favourite looked up', 'internal/modules/user/writes.go', 155, 159),
        ('soft-deleted favourite restored', 'internal/modules/user/writes.go', 160, 167),
        ('new favourite inserted', 'internal/modules/user/writes.go', 175, 181),
        ('cache invalidated', 'internal/modules/user/writes.go', 183, 183),
    ], ['InsertFavourite, called by handler addFavourite', '`gameId` int · `siteSection` string'], ()),

    ('player deposit', 'nh-player-service-go', 'How does a deposit work?', [
        ('amount and fields validated', 'internal/modules/wallet/paybridge_handlers.go', 142, 146),
        ('deposit limits', 'internal/modules/wallet/paybridge_handlers.go', 148, 154),
        ('Pay Bridge deposit started', 'internal/modules/wallet/paybridge_handlers.go', 156, 160),
        ('Pay Bridge service info checked', 'internal/paybridge/paybridge.go', 143, 146),
    ], ['InitiateDeposit, called by handler deposit'], ()),

    ('player registration bodies', 'nh-player-service-go',
     'How is the user registration done? Show the request and response body of the endpoints involved.', [
        ('existing phone looked up', 'internal/modules/auth/register_otp.go', 50, 54),
        ('registered phone refused', 'internal/modules/auth/register_otp.go', 55, 59),
        ('operator codes checked', 'internal/modules/auth/register_otp.go', 61, 64),
        ('session created', 'internal/modules/auth/register_otp.go', 66, 70),
    ], ['**Code path**', '`phoneNumber` string · `password` string · `codeChallenge` string',
        '422 (set in `apierror.WriteValidation`)', '400 when `phone == ""`',
        '200 — `httpx.WriteJSON(w, http.StatusOK, map[string]any{"sessionId": sessionID})`',
        'POST /v1/otp/send takes `authSessionId`', 'POST /v1/otp/verify takes `authSessionId`',
        'POST /v1/oauth/token takes `code` (from `authorizationCode`)', '`accessToken`, `idToken`, `refreshToken`'], ()),

    ('gateway forwarding', 'nh-gateway-go-sync', 'How are /v1/user requests forwarded to the player service?', [
        ('body size cap', 'internal/forwarding/forwarder.go', 168, 173),
        ('idle activity timeout', 'internal/forwarding/forwarder.go', 175, 180),
        ('reverse proxy', 'internal/forwarding/forwarder.go', 193, 193),
    ], ['registration of POST /v1/user (and /v1/user/* via catchAll)', 'handler Handler of POST /v1/user'], ()),

    ('gateway InitGame', 'nh-gateway-go-sync', 'How does InitGame work?', [
        ('unauthenticated refused unless demo', 'internal/modules/gaming/gaming.go', 225, 229),
        ('casino engine gameInit', 'internal/modules/gaming/gaming.go', 243, 247),
        ('history recorded in background', 'internal/modules/gaming/gaming.go', 255, 257),
        ('gRPC GameInit', 'internal/modules/gaming/engine.go', 227, 231),
    ], ['gameInit, called by handler initGame', '401 when `!signedIn && !req.Demo`'], ()),

    ('gateway getConfig', 'nh-gateway-go-sync', 'How does getConfig work?', [
        ('config document fetched', 'internal/modules/configmod/configmod.go', 100, 100),
        ('document rendered', 'internal/modules/configmod/configmod.go', 105, 105),
        ('cached document reused', 'internal/platformconfig/platformconfig.go', 95, 97),
        ('GET to config-service', 'internal/platformconfig/platformconfig.go', 106, 111),
    ], ['Document, called by handler getConfig'], ()),

    ('gateway top winners', 'nh-gateway-go-sync', 'How are top winners fetched?', [
        ('request bound', 'internal/modules/gaming/gaming.go', 367, 370),
        ('engine topWinners called', 'internal/modules/gaming/gaming.go', 379, 386),
    ], ['handler topWinners of POST /v1/gaming/stats/GetTopWinners — internal/modules/gaming/gaming.go:366'], ()),

    ('config update', 'nh-config-service-go', 'How is the config updated?', [
        ('document validated', 'internal/api/api.go', 210, 216),
        ('service Update or Create', 'internal/api/api.go', 241, 245),
    ], ['handler updateConfig of PUT /config/api/v1/config', 'upsert, called by handler updateConfig'], ()),

    ('config history', 'nh-config-service-go', 'How does the config history work?', [
        ('history read', 'internal/api/api.go', 270, 274),
        ('history depth', 'internal/api/api.go', 277, 280),
    ], ['writeHistory, called by handler getHistory'], ()),

    ('data banners', 'nh-data-adapter-go', 'How are banners fetched?', [
        ('content type checked', 'internal/content/handler.go', 110, 113),
        ('service Banners called', 'internal/content/handler.go', 129, 132),
        ('L1 cache hit', 'internal/cache/cache.go', 142, 145),
        ('L2 cache lookup', 'internal/cache/cache.go', 149, 159),
    ], ['GetOrSet, called by Banners'], ()),

    ('data legacy user create', 'nh-data-adapter-go', 'How is a legacy user created?', [
        ('user inserted', 'internal/legacyuser/repository.go', 104, 104),
        ('new id read', 'internal/legacyuser/repository.go', 108, 108),
    ], ['Create, called by handler create', '400 (set in `h.invalid`)'], ()),

    ('data betting allowed bulk', 'nh-data-adapter-go', 'How does betting allowed bulk work?', [
        ('ids de-duplicated', 'internal/legacyuser/handler.go', 337, 344),
        ('missing or disallowed players warned', 'internal/legacyuser/handler.go', 352, 358),
        ('one SELECT ... IN', 'internal/legacyuser/repository.go', 224, 225),
    ], ['BettingAllowedBulk, called by handler bettingAllowedBulk'], ()),
]


TIMEOUT = 1500


STDERR_DIR: Path | None = None      # --stderr-dir: where each case's stderr (EXPLORE_DEBUG_DROPS notes) is saved


def run(cmd: list[str], label: str = '') -> tuple[str | None, float]:
    """The command's output and seconds taken; None as output when it timed out (one slow answer must not
    end the whole eval and lose the remaining cases)."""
    started = time.monotonic()
    try:
        done = subprocess.run(cmd, capture_output=True, text=True, timeout=TIMEOUT)
    except subprocess.TimeoutExpired:
        return None, time.monotonic() - started
    if STDERR_DIR and label:
        STDERR_DIR.mkdir(parents=True, exist_ok=True)
        (STDERR_DIR / (re.sub(r'\W+', '-', label).strip('-') + '.txt')).write_text(done.stderr, encoding='utf-8')
    return done.stdout, time.monotonic() - started


def overlaps(cites, facts):
    return [label for label, file, lo, hi in facts if any(f == file and a <= hi and lo <= b for f, a, b in cites)]


def score(output: str, facts, code, wrong) -> dict:
    # Sections found by code say so in their header ("found by code", "quoted by code", "by code"); the
    # rest of the bullets are the model's. Model facts count only the model's bullets.
    model_lines, in_code = [], False
    for line in output.splitlines():
        if re.match(r'^\*\*[^*]+\*\*', line):
            in_code = 'by code' in line
        if not in_code and line.startswith('- ') and 'quoted by code' not in line:
            model_lines.append(line)
    model_cites = [(f, int(a), int(b or a)) for line in model_lines for f, a, b in CITE.findall(line)]
    all_cites = [(f, int(a), int(b or a)) for line in output.splitlines() if line.lstrip().startswith('- ') for f, a, b in CITE.findall(line)]
    model_found, answer_found = overlaps(model_cites, facts), overlaps(all_cites, facts)
    text = '\n'.join(model_lines).casefold()
    return {'model': len(model_found), 'answer': len(answer_found), 'of': len(facts),
            'missing': [label for label, *_ in facts if label not in answer_found],
            'code_ok': [needle for needle in code if needle in output], 'code_missing': [needle for needle in code if needle not in output],
            'wrong': [phrase for phrase in wrong if phrase.casefold() in text]}


def score_logs(output: str) -> dict:
    findings = [line for line in output.splitlines() if line.startswith(('- OBSERVATION', '- HYPOTHESIS', '- SUGGESTED'))]
    text = ' '.join(findings)
    return {'findings': len(findings), 'observation': any(l.startswith('- OBSERVATION') for l in findings),
            'names_target': 'wallet-service-service-grpc' in text, 'rate_in_report': 'every ~3.0s' in output}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--models', nargs='+', default=['qwen3:14b-q4_K_M'])
    parser.add_argument('--root', type=Path, default=Path('~/Documents/Projects'), help='folder holding the service checkouts')
    parser.add_argument('--only', nargs='*', default=[], help='run cases whose name contains one of these words')
    parser.add_argument('--logs', action='store_true', help='also run the log analyzer check')
    parser.add_argument('--stderr-dir', type=Path, help='save each case\'s stderr here (with EXPLORE_DEBUG_DROPS=1: why claims were dropped or moved)')
    args = parser.parse_args()
    global STDERR_DIR
    STDERR_DIR = args.stderr_dir
    root = args.root.expanduser()
    cases = [case for case in CASES if not args.only or any(word.casefold() in case[0].casefold() for word in args.only)]
    failures = 0
    for model in args.models:
        print(f'\n== {model}')
        rows = []
        for name, project, question, facts, code, wrong in cases:
            if not (root / project / 'graphify-out' / 'graph.json').exists():
                # A failure, not a skip: a wrong --root must not report success while checking nothing.
                print(f'  {name}: FAILED, no graph at {root / project}')
                failures += 1
                continue
            out, secs = run([sys.executable, str(SCRIPTS / 'explore_agent.py'), str(root / project), question, '--model', model], name)
            if out is None:
                print(f'  {name}: FAILED, no answer within {TIMEOUT}s', flush=True)
                failures += 1
                continue
            result = score(out, facts, code, wrong)
            rows.append((name, result, secs))
            bad = result['code_missing'] or result['wrong']
            failures += bool(bad)
            print(f'  {name}: model {result["model"]}/{result["of"]}, answer {result["answer"]}/{result["of"]}, '
                  f'code {len(result["code_ok"])}/{len(code)}, wrong {len(result["wrong"])}, {secs:.0f}s'
                  + (f'\n    not in the answer: {", ".join(result["missing"])}' if result['missing'] else '')
                  + ''.join(f'\n    CODE MISSING: {needle}' for needle in result['code_missing'])
                  + ''.join(f'\n    KNOWN WRONG: {phrase}' for phrase in result['wrong']), flush=True)
        print('\n| case | model facts | in answer | code checks | wrong | s |\n|---|---|---|---|---|---|')
        for name, r, secs in rows:
            print(f'| {name} | {r["model"]}/{r["of"]} | {r["answer"]}/{r["of"]} | {len(r["code_ok"])}/{len(r["code_ok"]) + len(r["code_missing"])} | {len(r["wrong"])} | {secs:.0f} |')
        if args.logs:
            out, secs = run([sys.executable, str(SCRIPTS / 'log_analyzer.py'), str(LOG), 'Why is this service failing and what should I check?', '--model', model])
            logs = score_logs(out or '')
            print(f'\n  logs: {logs["findings"]} findings, observation={logs["observation"]}, names target={logs["names_target"]}, '
                  f'rate in report={logs["rate_in_report"]}, {secs:.0f}s')
    # Code checks and known-wrong phrases are hard failures; fact counts depend on the model and are reported only.
    if not cases:
        print(f'\nno case matched --only {" ".join(args.only)}')
        return 1
    print(f'\n{failures} case(s) failed: no graph, no answer in time, a missing code check or a known-wrong statement.')
    return 1 if failures else 0


if __name__ == '__main__':
    raise SystemExit(main())
