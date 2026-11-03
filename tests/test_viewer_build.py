import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class ViewerBuildTest(unittest.TestCase):
    def test_built_viewer_matches_sources(self):
        """assets/project-explorer.html and index.html must be rebuilt from viewer-src/."""
        result = subprocess.run([sys.executable, str(ROOT / 'viewer-src' / 'build.py'), '--check'],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == '__main__':
    unittest.main()
