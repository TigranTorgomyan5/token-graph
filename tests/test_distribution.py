"""Recipient-facing packaging and setup checks."""
from pathlib import Path
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[1]
SKILL = Path(os.environ.get('TOKEN_GRAPH_TEST_SKILL_PATH', str(ROOT / 'token-graph')))


class DistributionTests(unittest.TestCase):
    def test_archive_is_portable_and_contains_engine_and_viewers(self):
        with zipfile.ZipFile(ROOT / 'token-graph.zip') as archive:
            names = archive.namelist()
            for expected in ('SKILL.md', 'INSTALL.md', 'scripts/setup_runtime.py', 'scripts/offline_engine.py', 'scripts/offline_policy.py', 'scripts/sitecustomize.py', 'vendor/graphify/cli.py', 'vendor/LICENSE', 'assets/graph-viewer.html', 'assets/project-explorer.html', 'requirements.txt'):
                self.assertIn('token-graph/' + expected, names)
            for name in names:
                self.assertNotIn('/.runtime/', name)
                self.assertNotIn('/__pycache__/', name)
                self.assertFalse(name.endswith('.pyc'))
                self.assertNotIn(str(Path.home()).encode(), archive.read(name))
            for line in archive.read('token-graph/requirements.txt').decode().splitlines():
                self.assertFalse(line.lower().startswith(('graphify', 'graphifyy')))

    def test_fresh_offline_setup_fails_clearly_without_uv_or_cached_wheels(self):
        with tempfile.TemporaryDirectory(prefix='token-graph-offline-setup-') as directory:
            root = Path(directory)
            (root / 'scripts').mkdir()
            shutil.copy2(SKILL / 'scripts/setup_runtime.py', root / 'scripts/setup_runtime.py')
            shutil.copy2(SKILL / 'requirements.txt', root / 'requirements.txt')
            env = dict(os.environ, PATH='/usr/bin:/bin', PIP_CACHE_DIR=str(root / 'empty-cache'))
            result = subprocess.run([sys.executable, str(root / 'scripts/setup_runtime.py'), '--installer', 'pip'], env=env, text=True, capture_output=True, timeout=60)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('No network download was attempted', result.stderr)
            self.assertIn('--download-deps', result.stderr)
            self.assertIn('Graphify is not required', result.stderr)


if __name__ == '__main__':
    unittest.main()
