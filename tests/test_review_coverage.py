"""Exercise the public CLI against omitted, skipped, stale and changed evidence."""
import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/validate_review_coverage.py'


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class ReviewCoverageTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / 'source.mp4'
        self.candidate = self.root / 'candidate.blend'
        self.evidence = self.root / 'review.txt'
        for path in [self.source, self.candidate, self.evidence]:
            path.write_text(path.name, encoding='utf-8')
        self.protocol = self.root / 'protocol.json'
        self.protocol.write_text(json.dumps({
            'source_video_sha256': digest(self.source),
            'checks': {'lead_facing_visual': {'source_frames': [0, 1, 2]},
                       'server_arm_native': {'source_frames': [8, 9]}}}), encoding='utf-8')
        self.report = self.root / 'report.json'
        self.good = {'protocol_sha256': digest(self.protocol),
                     'source_video_sha256': digest(self.source),
                     'candidate_sha256': digest(self.candidate), 'checks': {}}
        for name, frames in [('lead_facing_visual', [0, 1, 2]), ('server_arm_native', [8, 9])]:
            self.good['checks'][name] = {'executed': True, 'status': 'passed', 'source_frames': frames,
                                        'evidence': [{'path': self.evidence.name, 'sha256': digest(self.evidence)}]}

    def run_cli(self, report):
        self.report.write_text(json.dumps(report), encoding='utf-8')
        return self.run_saved_cli()

    def run_saved_cli(self):
        return subprocess.run([sys.executable, str(SCRIPT), '--protocol', str(self.protocol),
                               '--report', str(self.report), '--candidate', str(self.candidate),
                               '--source-video', str(self.source)], capture_output=True, text=True,
                              encoding='utf-8')

    def test_complete_ledger_is_accepted_without_claiming_visual_truth(self):
        result = self.run_cli(self.good)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        output = json.loads(result.stdout)
        self.assertEqual(output['status'], 'coverage_complete')
        self.assertIn('does not inspect', output['limitation'])

    def test_omitted_role_and_truncated_continuous_frames_are_rejected(self):
        omitted = copy.deepcopy(self.good)
        del omitted['checks']['server_arm_native']
        short = copy.deepcopy(self.good)
        short['checks']['lead_facing_visual']['source_frames'] = [0, 2]
        for report in [omitted, short]:
            with self.subTest(report=report):
                self.assertEqual(self.run_cli(report).returncode, 1)

    def test_unexecuted_or_nonpassing_claims_are_rejected(self):
        for executed, status in [(False, 'passed'), ('true', 'passed'),
                                 (True, 'unverified'), (True, 'not_applicable'), (True, 'failed')]:
            report = copy.deepcopy(self.good)
            report['checks']['server_arm_native'].update(executed=executed, status=status)
            with self.subTest(executed=executed, status=status):
                self.assertEqual(self.run_cli(report).returncode, 1)

    def test_stale_protocol_source_candidate_and_evidence_are_rejected(self):
        for path in [self.protocol, self.source, self.candidate, self.evidence]:
            original = path.read_bytes()
            path.write_bytes(original + b' ')
            with self.subTest(path=path.name):
                self.assertEqual(self.run_cli(self.good).returncode, 1)
            path.write_bytes(original)

    def test_missing_evidence_and_unknown_check_ids_are_rejected(self):
        empty = copy.deepcopy(self.good)
        empty['checks']['server_arm_native']['evidence'] = []
        wrong = copy.deepcopy(self.good)
        wrong['checks']['server_arms_native'] = wrong['checks'].pop('server_arm_native')
        for report in [empty, wrong]:
            self.assertEqual(self.run_cli(report).returncode, 1)

    def test_malformed_ledger_fails_with_json_diagnostic(self):
        for value in [None, [], [True], [-1]]:
            report = copy.deepcopy(self.good)
            report['checks']['server_arm_native']['source_frames'] = value
            result = self.run_cli(report)
            self.assertEqual(result.returncode, 1)
            self.assertEqual(json.loads(result.stdout)['status'], 'incomplete')

    def test_matching_id_with_no_result_cannot_silently_pass(self):
        for value in [None, [], True, 'passed']:
            report = copy.deepcopy(self.good)
            report['checks']['server_arm_native'] = value
            result = self.run_cli(report)
            self.assertEqual(result.returncode, 1)
            self.assertEqual(json.loads(result.stdout)['status'], 'incomplete')

    def test_duplicate_json_result_is_rejected_instead_of_overwritten(self):
        self.report.write_text('{"checks":{},"checks":{}}', encoding='utf-8')
        result = self.run_saved_cli()
        self.assertEqual(result.returncode, 1)
        self.assertIn('Duplicate JSON key', json.loads(result.stdout)['errors'][0])

    def test_empty_protocol_and_missing_evidence_file_are_rejected(self):
        self.evidence.unlink()
        self.assertEqual(self.run_cli(self.good).returncode, 1)
        self.protocol.write_text('{"checks":{}}', encoding='utf-8')
        report = copy.deepcopy(self.good)
        report.update(protocol_sha256=digest(self.protocol), checks={})
        result = self.run_cli(report)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(json.loads(result.stdout)['status'], 'incomplete')


if __name__ == '__main__':
    unittest.main()
