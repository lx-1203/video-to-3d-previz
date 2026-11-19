"""Single-shot timing stays 4-8s; multi-shot packages obey verified platform limits."""
import unittest
import json
import pathlib
import subprocess
import sys
import tempfile

from validate_duration_limits import validate


def single(seconds, **extra):
    return dict(segment_id='SG01', logical_shot_id='SH01', duration_seconds=seconds, **extra)


def package(seconds, maximum=30, **limits):
    return dict(segment_id='PK01', generation_mode='multi_shot', shot_ids=['SH01', 'SH02'],
                duration_seconds=seconds,
                platform_limits=dict(verified=True, max_seconds=maximum, **limits))


def report(*segments):
    return validate({'generation_segments': list(segments)}, 4, 8)


class GenerationTimingTests(unittest.TestCase):
    def test_legacy_and_explicit_single_shots_keep_four_to_eight_seconds(self):
        for seconds in [4, 6, 8]:
            self.assertEqual(report(single(seconds))['errors'], [])
            self.assertEqual(report(single(seconds, generation_mode='single_shot'))['errors'], [])
        for seconds in [3, 9, 15, 30]:
            self.assertTrue(report(single(seconds))['errors'])

    def test_multi_shot_15_and_30_are_platform_specific(self):
        self.assertEqual(report(package(15, 15))['errors'], [])
        self.assertEqual(report(package(30, 30))['errors'], [])
        self.assertTrue(report(package(16, 15))['errors'])
        self.assertTrue(report(package(31, 30))['errors'])

    def test_old_plan_overrides_cannot_expand_single_shot_policy(self):
        self.assertTrue(validate({'generation_segments': [single(15)]}, 0, 30)['errors'])
        self.assertTrue(validate({'generation_segments': [single(3)]}, 0, 30)['errors'])
        self.assertEqual(validate({'generation_segments': [package(30)]}, 0, 30)['errors'], [])

    def test_multi_shot_has_no_invented_four_second_minimum(self):
        self.assertEqual(report(package(3, 15))['errors'], [])
        self.assertTrue(report(package(3, 15, min_seconds=5))['errors'])

    def test_platform_allowed_values_are_respected(self):
        self.assertEqual(report(package(15, 30, allowed_seconds=[15, 30]))['errors'], [])
        self.assertTrue(report(package(18, 30, allowed_seconds=[15, 30]))['errors'])

    def test_single_shot_also_obeys_narrower_verified_platform_limit(self):
        self.assertTrue(report(single(6, platform_limits={'verified': True, 'max_seconds': 5}))['errors'])

    def test_multi_shot_does_not_fallback_to_guessed_limits(self):
        for limits in [None, {}, {'verified': False, 'max_seconds': 30},
                       {'verified': True}, {'verified': True, 'max_seconds': float('nan')}]:
            item = package(15)
            item['platform_limits'] = limits
            self.assertTrue(report(item)['errors'])

    def test_declaring_multi_shot_requires_distinct_shot_ids(self):
        for ids in [None, [], ['SH01'], ['SH01', 'SH01'], ['SH01', '']]:
            item = package(15)
            item['shot_ids'] = ids
            self.assertTrue(report(item)['errors'])

    def test_unknown_modes_and_invalid_numbers_fail(self):
        self.assertTrue(report(single(6, generation_mode='long'))['errors'])
        for seconds in [float('nan'), float('inf'), True, 0, -1]:
            self.assertTrue(report(single(seconds))['errors'])
            self.assertTrue(report(package(seconds))['errors'])

    def test_mixed_plan_keeps_per_job_limits(self):
        result = report(single(6), package(15, 15), dict(package(30), segment_id='PK02'))
        self.assertEqual(result['errors'], [])
        self.assertEqual(result['segment_count'], 3)

    def test_cli_single_shot_flags_do_not_override_multi_shot_platform_limits(self):
        script = pathlib.Path(__file__).with_name('validate_duration_limits.py')
        with tempfile.TemporaryDirectory() as td:
            plan = pathlib.Path(td) / 'plan.json'
            plan.write_text(json.dumps({'generation_segments': [package(30)]}), encoding='utf-8')
            result = subprocess.run([sys.executable, '-B', str(script), str(plan), '--max', '8'],
                                    capture_output=True, text=True, encoding='utf-8')
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(json.loads(result.stdout)['status'], 'passed')


if __name__ == '__main__':
    unittest.main()
