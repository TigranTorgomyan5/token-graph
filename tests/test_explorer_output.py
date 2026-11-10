"""Returned context stays bounded without dropping uncertainty warnings."""
import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'token-graph/scripts'))
from explore_agent import compact_report

class ExplorerOutputTests(unittest.TestCase):
    def test_compact_preserves_complete_claims_and_warnings(self):
        claim = '- Handler reads the request. src/handler.go:10-14'
        report = '\n'.join(['ANSWER:', *[claim for _ in range(100)], 'UNCERTAINTIES: Middleware was not inspected.'])
        result = compact_report(report, 1000)
        self.assertLessEqual(len(result), 1000)
        self.assertIn('UNCERTAINTIES: Middleware was not inspected.', result)
        self.assertIn('OUTPUT LIMITED:', result)
        self.assertTrue(all(line == claim for line in result.splitlines() if line.startswith('- ')))

    def test_short_report_unchanged(self):
        text = 'UNCERTAINTIES: No evidence found.'
        self.assertEqual(compact_report(text), text)

    def test_huge_warning_is_explicitly_shortened(self):
        result = compact_report('UNCERTAINTIES: ' + 'unknown ' * 1000, 1000)
        self.assertLessEqual(len(result), 1000)
        self.assertIn('uncertainties shortened', result)
