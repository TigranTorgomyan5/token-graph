"""Run the offline viewer's projection and interaction checks when Node is available."""
from pathlib import Path
import shutil
import subprocess
import unittest


class Viewer3DTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('node'), 'Node is needed only for frontend development tests')
    def test_projection_and_interactions(self):
        subprocess.run(['node', str(Path(__file__).with_suffix('.js'))], check=True, capture_output=True, text=True)
