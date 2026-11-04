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
        self.assertIn('Callers:   POST /v1/user/security/selfExclude route, registered at internal/security/security.go:', block)
        self.assertIn('SelfExclude_InvalidDuration (table in todaysRows), SelfExclude_NoToken (table in todaysRows) (e2e/rail_test.go:4-5)', block)
        self.assertIn('TestSelfExclude_OverlapReturns409', block)
        self.assertNotIn('TestMain', block)                                          # shared setup is not a test of the route
        self.assertIn('helper `refuseWrite` (not a test) (e2e/guard_test.go:6)', block)
        self.assertNotIn('TestUnrelated', block)                                     # the test above the helper is not credited
        self.assertIn('docs/rules/contracts.md § Gateway-facing routes (docs/rules/contracts.md:5)', block)
        self.assertIn('run without a model', block)

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
