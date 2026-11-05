"""Local repo-scout: the nh-agent-kit scout block, filled by code (no model in these tests)."""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'token-graph' / 'scripts'))
import local_scout as scout  # noqa: E402

FILES = {
    'internal/security/security.go': (
        'package security\nfunc (m *Module) Mount(r chi.Router) {\n\tr.Post("/v1/user/security/selfExclude", m.selfExclude)\n}\n'
        'func (m *Module) selfExclude(w http.ResponseWriter, r *http.Request) {\n\tvar req struct {\n'
        '\t\tDurationDays int `json:"durationDays"`\n\t}\n\tif err := httpx.DecodeJSON(r, &req); err != nil {\n'
        '\t\tapierror.Write(w, r, http.StatusUnprocessableEntity, apierror.ValidationFailed, nil)\n\t\treturn\n\t}\n'
        '\tif overlap {\n\t\tapierror.Write(w, r, http.StatusConflict, apierror.SelfExclusionOverlap, nil)\n\t\treturn\n\t}\n'
        '\thttpx.WriteJSON(w, http.StatusOK, map[string]any{"success": true})\n}\n'),
    'e2e/rail_test.go': ('package e2e\nfunc todaysRows(tok string) []railCase {\n\treturn []railCase{\n'
                         '\t\t{"SelfExclude_InvalidDuration", "/v1/user/security/selfExclude", tok, nil},\n'
                         '\t\t{"SelfExclude_NoToken", "/v1/user/security/selfExclude", "", nil},\n\t}\n}\n'),
    'internal/security/security_test.go': ('package security\nfunc TestMain(m *testing.M) { setup("/v1/user/security/selfExclude") }\n'
                                           'func TestSelfExclude_OverlapReturns409(t *testing.T) {\n\tpost(t, "/v1/user/security/selfExclude")\n}\n'),
    'e2e/guard_test.go': ('package e2e\nfunc TestUnrelated(t *testing.T) {\n\tt.Log("x")\n}\n'
                          'func refuseWrite(path string) error {\n\tif has("/v1/user/security/selfExclude") {\n\t\treturn errNo\n\t}\n\treturn nil\n}\n'),
    'docs/rules/contracts.md': '# Contracts\n\n## Gateway-facing routes\n\n- POST /v1/user/security/selfExclude (caller: nh-gateway)\n',
    # Near misses: a longer path and a longer word are not the route, and a helper in a file whose tests name
    # the route is not listed.
    'e2e/other_test.go': ('package e2e\nfunc TestSelfExcludeAll(t *testing.T) {\n\tpost(t, "/v1/user/security/selfExcludeAll")\n}\n'
                          'func TestSelfExcludedFlag(t *testing.T) {\n\tcheck(t, selfExcluded)\n}\n'),
    'internal/security/const_test.go': ('package security\nvar selfExcludePath = "/v1/user/security/selfExclude"\n'
                                        'func TestThroughTheConstant(t *testing.T) {\n\tpostJSON(selfExcludePath, `{}`)\n}\n'),
    'e2e/suite_test.go': ('package e2e\nfunc newSuiteGateway() *G {\n\treturn start("/v1/user/security/selfExclude")\n}\n'
                          'func (s *SecuritySuite) TestSelfExcludeIs200() {\n\ts.post("/v1/user/security/selfExclude")\n}\n'),
}


def make_project(directory: Path) -> Path:
    for name, text in FILES.items():
        (directory / name).parent.mkdir(parents=True, exist_ok=True)
        (directory / name).write_text(text)
    (directory / 'graphify-out').mkdir()
    nodes = [{'source_file': name} for name in FILES if name.endswith('.go')] + [
        {'id': 'h', 'label': '.selfExclude()', 'source_file': 'internal/security/security.go', 'source_location': 'L5'}]
    (directory / 'graphify-out' / 'graph.json').write_text(json.dumps({'nodes': nodes}))
    return directory


class LocalScoutTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = make_project(Path(self.directory.name))

    def test_block_has_every_kit_field_in_order(self):
        block = scout.scout(self.root, 'nh-player-service (origin/V5.0 @ abc)', 'How does self exclusion work?', None)
        labels = [line.split(':')[0] for line in block.splitlines() if line[:1].isalpha()]
        self.assertEqual(['Repo', 'Question', 'Behaviour', 'Callers', 'Pinned by', 'Rules', 'Unsure', 'Graph'], labels)
        self.assertTrue(block.startswith('Repo:      nh-player-service (origin/V5.0 @ abc)'))

    def test_code_found_fields(self):
        block = scout.scout(self.root, 'repo', 'How does self exclusion work?', None)
        self.assertIn('POST /v1/user/security/selfExclude is handled by `selfExclude` (internal/security/security.go:5)', block)
        self.assertIn('`durationDays` int', block)                                   # request body
        self.assertIn('409 when `overlap`', block)                                   # response with its condition
        # The registration line itself, not the first line of the window read around it.
        self.assertIn('Callers:   POST /v1/user/security/selfExclude route, registered at internal/security/security.go:3\n', block)
        self.assertNotIn('TestSelfExcludeAll', block)                              # /…/selfExcludeAll is another route
        self.assertNotIn('TestSelfExcludedFlag', block)                            # selfExcluded is another word
        self.assertIn('TestSelfExcludeIs200 (e2e/suite_test.go:3-6)', block)        # a testify suite method is a test
        self.assertNotIn('newSuiteGateway', block)                                 # its file has a test naming the route
        self.assertIn('TestThroughTheConstant (internal/security/const_test.go:2-4)', block)   # via selfExcludePath
        self.assertIn('SelfExclude_InvalidDuration (table in todaysRows), SelfExclude_NoToken (table in todaysRows) (e2e/rail_test.go:4-5)', block)
        self.assertIn('TestSelfExclude_OverlapReturns409', block)
        self.assertNotIn('TestMain', block)                                          # shared setup is not a test of the route
        self.assertIn('helper `refuseWrite` (not a test) (e2e/guard_test.go:6)', block)
        self.assertNotIn('TestUnrelated', block)                                     # the test above the helper is not credited
        self.assertIn('docs/rules/contracts.md § Gateway-facing routes (docs/rules/contracts.md:5)', block)
        self.assertIn('run without a model', block)

    def test_registration_line_and_contracts_per_handler(self):
        # Live nh-sport-go registers "/"+TokenRoute: the path is not in the source, the route list has the line.
        hints = ('POST /api/v1/auth/player/token — internal/sport/controller.go:59\n'
                 'POST /v1/user/security/selfExclude — internal/security/security.go:3\n')
        sources = [('internal/sport/controller.go', 57, 8, 'registration of POST /api/v1/auth/player/token'),
                   ('internal/sport/controller.go', 77, 10, 'handler issuePlayerToken of POST /api/v1/auth/player/token'),
                   ('internal/security/security.go', 1, 8, 'registration of POST /v1/user/security/selfExclude'),
                   ('internal/security/security.go', 5, 12, 'handler selfExclude of POST /v1/user/security/selfExclude')]
        contracts = ['**Request and responses: POST /api/v1/auth/player/token** (found by code)', '- Request body: none read by the handler',
                     '**Request and responses: POST /v1/user/security/selfExclude** (found by code)', '- Request body (`req`): `durationDays` int']
        with patch.object(scout.explorer, 'endpoint_route_hints', return_value=hints), \
                patch.object(scout.explorer, 'question_route_sources', return_value=sources), \
                patch.object(scout.explorer, 'endpoint_contracts', return_value=contracts):
            block = scout.scout(self.root, 'repo', 'How is a player token issued and self exclusion applied?', None)
        self.assertIn('registered at internal/sport/controller.go:59', block)
        behaviour = block.split('Callers:')[0]
        token, exclusion = behaviour.index('handled by `issuePlayerToken`'), behaviour.index('handled by `selfExclude`')
        self.assertLess(token, behaviour.index('none read by the handler'))
        self.assertLess(behaviour.index('none read by the handler'), exclusion)     # under its own handler
        self.assertLess(exclusion, behaviour.index('`durationDays` int'))

    def test_callers_in_other_services_by_config_key_and_forwarded_prefix(self):
        # Live: nh-gateway-go-sync reaches nh-player-service through NH_PLAYER_SERVICE_URI and forwards /v1/user/* to it.
        parent = Path(self.directory.name) / 'services'
        service = make_project(parent / 'nh-player-service') if (parent / 'nh-player-service').mkdir(parents=True) is None else None
        gateway, unrelated = parent / 'nh-gateway', parent / 'nh-billing'
        gateway.mkdir()
        (gateway / '.env.example').write_text('NH_PLAYER_SERVICE_URI=http://player:9008\n')
        (gateway / 'docker-compose.yml').write_text('services:\n  gateway:\n    environment:\n      NH_PLAYER_SERVICE_URI: http://host.docker.internal:9008\n')
        unrelated.mkdir()
        (unrelated / 'README.md').write_text('nothing here\n')
        real_hints = scout.explorer.endpoint_route_hints
        hints = lambda path: ('POST /v1/user (and /v1/user/* via catchAll) — internal/forwarding/routes.go:39\n' if Path(path) == gateway
                              else real_hints(path))
        with patch.object(scout.explorer, 'endpoint_route_hints', side_effect=hints):
            block = scout.scout(service, 'repo', 'How does self exclusion work?', None, [('nh-gateway', gateway), ('nh-billing', unrelated)])
        self.assertIn('nh-gateway calls this service (http, NH_PLAYER_SERVICE_URI: ', block)       # the key, not the host name
        self.assertIn('nh-gateway forwards /v1/user/* here via `catchAll` (internal/forwarding/routes.go:39)', block)
        self.assertNotIn('nh-billing', block)
        self.assertIn('other services: found by a config key or route match', block)
        # Without the option, nothing about other services is claimed.
        self.assertNotIn('other services', scout.scout(service, 'repo', 'How does self exclusion work?', None))

    def test_callers_are_the_handlers_own_not_a_neighbours(self):
        graph = {'nodes': [{'id': 'h', 'label': '.selfExclude()', 'source_file': 'internal/security/security.go', 'source_location': 'L5'},
                           {'id': 'n', 'label': '.neighbour()', 'source_file': 'internal/security/security.go', 'source_location': 'L7'},
                           {'id': 'a', 'label': 'Wire()', 'source_file': 'cmd/main.go', 'source_location': 'L10'},
                           {'id': 'b', 'label': 'Other()', 'source_file': 'cmd/main.go', 'source_location': 'L20'}],
                 'links': [{'source': 'a', 'target': 'h', 'relation': 'calls'}, {'source': 'b', 'target': 'n', 'relation': 'calls'}]}
        self.assertEqual(['Wire() (cmd/main.go:10)'], scout.callers(self.root, graph, 'internal/security/security.go', 5, 'selfExclude'))
        # A stale graph line far from the file's: still the handler's node, found by its name.
        graph['nodes'][0]['source_location'] = 'L14'
        self.assertEqual(['Wire() (cmd/main.go:10)'], scout.callers(self.root, graph, 'internal/security/security.go', 5, 'selfExclude'))

    def test_no_route_and_no_graph_say_so(self):
        block = scout.scout(self.root, 'repo', 'Where is the cache warmed?', None)
        self.assertIn('no route matched the question', block)
        (self.root / 'graphify-out' / 'graph.json').unlink()
        self.assertIn('Graph:     no graph', scout.scout(self.root, 'repo', 'How does self exclusion work?', None))

    def test_ref_reads_the_commit_and_leaves_the_repo_untouched(self):
        # The kit's scouts read origin/V5.0, never the working tree, which other sessions use.
        repo = Path(self.directory.name) / 'repo'
        repo.mkdir()
        run = lambda *args: subprocess.run(['git', '-C', str(repo), *args], capture_output=True, text=True, check=True).stdout
        run('init', '-q')
        (repo / 'main.go').write_text('package main\n// committed\n')
        run('add', 'main.go')
        run('-c', 'user.name=t', '-c', 'user.email=t@example.com', 'commit', '-qm', 'base')
        (repo / 'main.go').write_text('package main\n// working-tree edit\n')
        (repo / '.env').write_text('SECRET=1\n')                                    # untracked: never in a snapshot
        before = run('status', '--porcelain')

        def fake_build(cmd, capture_output, text):
            target = Path(cmd[-1])
            (target / 'graphify-out').mkdir(parents=True, exist_ok=True)
            (target / 'graphify-out' / 'graph.json').write_text('{"nodes": []}')
            return subprocess.CompletedProcess(cmd, 0, '', '')
        real_run = subprocess.run          # the graph build is faked; git runs for real
        with patch.object(scout, 'CACHE', Path(self.directory.name) / 'cache'), patch.object(scout.subprocess, 'run', side_effect=lambda cmd, **kw:
                fake_build(cmd, **kw) if 'token_graph.py' in ' '.join(map(str, cmd)) else real_run(cmd, **kw)):
            folder, sha = scout.snapshot(repo, 'HEAD')
        self.assertEqual('package main\n// committed\n', (folder / 'main.go').read_text())
        self.assertFalse((folder / '.env').exists())
        self.assertEqual(before, run('status', '--porcelain'))
        self.assertTrue(folder.name.startswith('repo@' + sha[:12]))


if __name__ == '__main__':
    unittest.main()
