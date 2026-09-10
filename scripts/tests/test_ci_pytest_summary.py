import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / 'summarize-pytest.py'


class SummaryTests(unittest.TestCase):
    def run_summary(self, xml, outcome='failure'):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            if xml is not None:
                (root / 'junit.xml').write_text(xml, encoding='utf-8')
            result = subprocess.run(
                [sys.executable, str(SCRIPT), '--junit', str(root / 'junit.xml'),
                 '--output', str(root / 'summary.json'), '--outcome', outcome],
                env={**os.environ, 'GITHUB_STEP_SUMMARY': str(root / 'github.md')},
                capture_output=True, text=True, encoding='utf-8',
            )
            self.assertTrue((root / 'summary.json').exists(), result.stderr)
            self.assertEqual((root / 'summary.md').read_text(encoding='utf-8'), (root / 'github.md').read_text(encoding='utf-8'))
            return result, json.loads((root / 'summary.json').read_text(encoding='utf-8')), (root / 'github.md').read_text(encoding='utf-8')

    def test_success_counts_without_double_counting(self):
        result, report, _ = self.run_summary('<testsuites><testsuite tests="2"><testcase name="a"/><testcase name="b"><skipped/></testcase></testsuite></testsuites>', 'success')
        self.assertEqual(result.returncode, 0)
        self.assertEqual(report['counts'], {'tests': 2, 'failures': 0, 'errors': 0, 'skipped': 1})

    def test_failure_and_collection_error_are_retained_and_escaped(self):
        result, report, markdown = self.run_summary('<testsuites><testsuite><testcase classname="x" name="&lt;script&gt;"><failure message="bad&#10;::error::injected%">detail</failure></testcase><testcase name="collect"><error message="oops"/></testcase></testsuite></testsuites>')
        self.assertEqual(result.returncode, 0)
        self.assertEqual(report['counts']['failures'], 1)
        self.assertEqual(report['counts']['errors'], 1)
        self.assertNotIn('<script>', markdown)
        self.assertNotIn('\n::error::injected', result.stdout)
        self.assertIn('%25', result.stdout)

    def test_missing_and_malformed_are_unavailable(self):
        for xml in (None, '<broken', '<unrelated/>'):
            with self.subTest(xml=xml):
                result, report, _ = self.run_summary(xml)
                self.assertEqual(result.returncode, 1)
                self.assertEqual(report['status'], 'unavailable')
                self.assertIsNone(report['counts'])

    def test_output_is_bounded(self):
        cases = ''.join('<testcase name="' + 'x' * 1000 + '"><failure message="' + 'y' * 10000 + '"/></testcase>' for _ in range(30))
        result, report, markdown = self.run_summary('<testsuite>' + cases + '</testsuite>')
        self.assertEqual(result.returncode, 0)
        self.assertEqual(len(report['failures']), 20)
        self.assertEqual(report['omitted_failures'], 10)
        self.assertLess(len(markdown), 25000)
        self.assertEqual(result.stdout.count('::error title='), 20)

    def test_empty_report_does_not_imply_success(self):
        _, report, markdown = self.run_summary('<testsuite/>')
        self.assertEqual(report['outcome'], 'failure')
        self.assertIn('failure', markdown)

    def test_unicode_failure_on_windows(self):
        result, report, _ = self.run_summary('<testsuite><testcase name="地图"><failure message="失败 🗺"/></testcase></testsuite>')
        self.assertEqual(result.returncode, 0)
        self.assertIn('地图', result.stdout)
        self.assertEqual(report['failures'][0]['message'], '失败 🗺')


if __name__ == '__main__':
    unittest.main()
