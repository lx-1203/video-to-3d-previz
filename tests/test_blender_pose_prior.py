"""Stdlib parsing/contract tests only; no substitute for real Blender checks."""
import copy
import importlib.util
from pathlib import Path
import unittest

PATH = Path(__file__).resolve().parents[1] / 'scripts/motion/blender_pose_prior.py'


class ContractTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(PATH.exists(), 'adapter module missing')
        spec = importlib.util.spec_from_file_location('prior', PATH)
        self.mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.mod)
        semantic_path = PATH.with_name('pose_semantic_profile.py')
        spec = importlib.util.spec_from_file_location('semantic', semantic_path)
        semantic = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(semantic)
        self.data = {'source_sha256': 'a' * 64,
                     'joint_names': list(semantic.OFFICIAL_JOINT_NAMES),
                     'rows': [{'actor_id': 'A', 'source_frame': 619, 'pts': 316928,
                               'time_base': '1/15360', 'joints_camera': [[1, 2, 3]] * 77}]}
        self.data['rows'][0]['joints_camera'][13] = [2, 3, 4]
        self.data['rows'][0]['joints_camera'][14] = [3, 4, 5]
        segment = {'control': 'upper_control', 'vertex_indices': [0, 1, 2, 3],
                   'proximal_cap_indices': [0, 1], 'distal_cap_indices': [2, 3],
                   'source_start': 'LeftArm', 'source_end': 'LeftForeArm'}
        lower = copy.deepcopy(segment)
        lower.update(control='lower_control', vertex_indices=[4, 5, 6, 7],
                     proximal_cap_indices=[4, 5], distal_cap_indices=[6, 7],
                     source_start='LeftForeArm', source_end='LeftHand')
        self.chain = {'upper': segment, 'lower': lower,
                      'side_confirmation': {'status': 'confirmed', 'evidence': 'audit'},
                      'pivot_calibration': {'status': 'calibrated', 'evidence': 'audit'}}
        self.profile = {'source_sha256': 'a' * 64, 'target_template_sha256': 'b' * 64,
                        'expected_blend_sha256': 'c' * 64, 'target_template_path': 'private.blend',
                        'frame_offset': 1,
                        'actors': {'A': {'mesh': 'mesh', 'rig': 'rig', 'chains': [self.chain]}}}

    def test_explicit_sample_offset(self):
        plan = self.mod.validate_contract(self.data, self.profile)
        self.assertEqual(plan[0]['blender_frame'], 620)
        self.assertEqual(plan[0]['row']['pts'], 316928)

    def test_offset_required_and_integer(self):
        for value in (None, True, 0.5):
            self.profile['frame_offset'] = value
            with self.assertRaises(ValueError):
                self.mod.validate_contract(self.data, self.profile)

    def test_side_and_pivot_evidence_required(self):
        for key in ('side_confirmation', 'pivot_calibration'):
            old = self.chain[key]
            self.chain[key] = {'status': old['status']}
            with self.assertRaises(ValueError):
                self.mod.validate_contract(self.data, self.profile)
            self.chain[key] = old

    def test_caps_must_belong_to_component(self):
        self.chain['upper']['proximal_cap_indices'] = [99]
        with self.assertRaises(ValueError):
            self.mod.validate_contract(self.data, self.profile)

    def test_no_shoulder_or_palm_alias(self):
        for name in ('LeftShoulder', 'LeftPalm', 'RightArm'):
            self.chain['upper']['source_start'] = name
            with self.assertRaises(ValueError):
                self.mod.validate_contract(self.data, self.profile)

    def test_duplicate_sample_rejected(self):
        self.data['rows'] *= 2
        with self.assertRaises(ValueError):
            self.mod.validate_contract(self.data, self.profile)

    def test_bad_hash_and_nonfinite_rejected(self):
        self.data['rows'][0]['joints_camera'][12] = [float('nan'), 1, 2]
        with self.assertRaises(ValueError):
            self.mod.validate_contract(self.data, self.profile)
        self.profile['source_sha256'] = 'd' * 64
        with self.assertRaises(ValueError):
            self.mod.validate_contract(self.data, self.profile)

    def test_output_must_be_new(self):
        with self.assertRaises(ValueError):
            self.mod.validate_output(PATH, PATH.with_suffix('.receipt.json'), PATH)

    def test_cli_requires_all_three_arguments(self):
        args = self.mod.parse_args(['--candidates', 'numeric.json', '--profile', 'private.json',
                                    '--output', 'new.blend', '--samples', '620', '699'])
        self.assertEqual(args.output, 'new.blend')
        self.assertEqual(args.samples, [620, 699])

    def test_samples_select_blender_frames_only(self):
        row = copy.deepcopy(self.data['rows'][0])
        row.update(source_frame=568, pts=290816)
        self.data['rows'].append(row)
        plan = self.mod.validate_contract(self.data, self.profile, samples=[620])
        self.assertEqual([s['blender_frame'] for s in plan], [620])

    def test_missing_requested_sample_is_error(self):
        with self.assertRaises(ValueError):
            self.mod.validate_contract(self.data, self.profile, samples=[620, 699])


if __name__ == '__main__':
    unittest.main()
