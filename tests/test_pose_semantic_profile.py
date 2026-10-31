import copy
import importlib.util
from pathlib import Path
import unittest

MODULE = Path(__file__).resolve().parents[1] / 'scripts/motion/pose_semantic_profile.py'


class SemanticTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(MODULE.exists(), 'semantic profile module missing')
        spec = importlib.util.spec_from_file_location('semantic', MODULE)
        self.mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.mod)
        self.data = {'source_sha256': 'a' * 64,
                     'joint_names': list(self.mod.OFFICIAL_JOINT_NAMES), 'rows': []}
        self.row = {'actor_id': 'A', 'source_frame': 12, 'pts': 1024,
                    'time_base': '1/15360', 'joints_camera': [[0, 0, 1]] * 77,
                    'keypoints_2d': [[10, 20]] * 77, 'projection_valid': [True] * 77,
                    'source_joint_visibility': 'unknown'}
        self.row['joints_camera'][13] = [1, 2, 3]
        self.data['rows'] = [self.row]
        self.chain = {'source_start': 'LeftArm', 'source_end': 'LeftForeArm',
                      'target_component': 'upper', 'target_control': 'control',
                      'side_confirmation': {'status': 'confirmed', 'evidence': 'side audit'},
                      'pivot_calibration': {'status': 'calibrated', 'evidence': 'pivot audit'}}
        self.profile = {'source_sha256': 'a' * 64, 'target_template_sha256': 'b' * 64,
                        'actors': {'A': {'mesh': 'body', 'rig': 'rig', 'chains': [self.chain]}}}

    def run_profile(self):
        return self.mod.build_semantic_profile(self.data, self.profile)

    def test_geometry_and_visibility_are_separate(self):
        before = copy.deepcopy(self.data)
        out = self.run_profile()
        chain = out['rows'][0]['chains'][0]
        self.assertEqual(chain['direction_camera'], [1, 2, 2])
        self.assertEqual(chain['start']['visibility'], 'unknown')
        self.assertTrue(chain['start']['projection_valid'])
        self.assertEqual(chain['status'], 'ready_for_semantic_adaptation')
        self.assertFalse(out['executable_animation'])
        self.assertEqual(self.data, before)
        self.assertEqual(out['rows'][0]['pts'], 1024)

    def test_unproven_side_or_pivot_blocks_without_dropping_geometry(self):
        for field in ('side_confirmation', 'pivot_calibration'):
            with self.subTest(field=field):
                original = self.chain[field]
                self.chain[field] = {'status': original['status']}
                out = self.run_profile()
                chain = out['rows'][0]['chains'][0]
                self.assertEqual(chain['status'], 'blocked')
                self.assertTrue(chain['reasons'])
                self.assertEqual(chain['direction_camera'], [1, 2, 2])
                self.chain[field] = original

    def test_hash_mismatch_blocks(self):
        self.profile['source_sha256'] = 'c' * 64
        self.assertEqual(self.run_profile()['status'], 'blocked')

    def test_unknown_actor_is_not_inferred(self):
        self.row['actor_id'] = 'B'
        result = self.run_profile()['rows'][0]
        self.assertEqual(result['status'], 'blocked')
        self.assertEqual(result['chains'], [])

    def test_joint_semantics_and_no_aliases(self):
        for name, meaning in [('LeftShoulder', 'clavicle'), ('LeftArm', 'upper_arm_root'),
                              ('LeftForeArm', 'elbow'), ('LeftHand', 'wrist')]:
            self.chain['source_end'] = name
            self.assertEqual(self.run_profile()['rows'][0]['chains'][0]['end']['semantic'], meaning)
        self.chain['source_end'] = 'left_wrist'
        self.assertEqual(self.run_profile()['status'], 'blocked')

    def test_bad_numbers_and_projection_do_not_create_valid_direction(self):
        self.row['joints_camera'][13] = [float('nan'), 0, 1]
        chain = self.run_profile()['rows'][0]['chains'][0]
        self.assertEqual(chain['status'], 'blocked')
        self.assertIsNone(chain['direction_camera'])

    def test_invalid_projection_keeps_unknown_visibility(self):
        self.row['projection_valid'][13] = False
        chain = self.run_profile()['rows'][0]['chains'][0]
        self.assertFalse(chain['end']['projection_valid'])
        self.assertEqual(chain['end']['visibility'], 'unknown')
        self.assertIsNone(chain['direction_2d'])

    def test_rows_preserve_actor_pts_and_no_cross_actor_chain(self):
        second = copy.deepcopy(self.row)
        second.update(actor_id='B', pts=2048)
        self.data['rows'].append(second)
        self.assertEqual([(r['actor_id'], r['pts']) for r in self.run_profile()['rows']],
                         [('A', 1024), ('B', 2048)])

    def test_bad_structure_rejected(self):
        self.data['joint_names'][0] = 'invented'
        with self.assertRaises(ValueError):
            self.run_profile()

    def test_status_must_be_exact_not_truthy(self):
        for value in (True, 'confirmed', {'status': 'unknown', 'evidence': 'audit'}):
            self.chain['side_confirmation'] = value
            self.assertEqual(self.run_profile()['status'], 'blocked')

    def test_degenerate_chain_is_blocked(self):
        self.row['joints_camera'][13] = [0, 0, 1]
        self.assertEqual(self.run_profile()['status'], 'blocked')

    def test_target_hash_and_mapping_are_required(self):
        self.profile['target_template_sha256'] = ''
        self.profile['actors']['A']['rig'] = ''
        out = self.run_profile()
        self.assertEqual(out['status'], 'blocked')
        self.assertIn('mesh_rig_mapping_missing', out['rows'][0]['reasons'])

    def test_visibility_annotations_survive(self):
        self.row['source_joint_visibility'] = {'LeftArm': {'state': 'inferred', 'interval': [1, 2]}}
        chain = self.run_profile()['rows'][0]['chains'][0]
        self.assertEqual(chain['start']['visibility'], {'state': 'inferred', 'interval': [1, 2]})
        self.assertEqual(chain['end']['visibility'], 'unknown')


if __name__ == '__main__':
    unittest.main()
