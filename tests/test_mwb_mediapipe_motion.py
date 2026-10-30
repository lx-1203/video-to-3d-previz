import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / 'scripts/motion'


def sample_landmarks():
    # MediaPipe camera frame: x right, y down, z away. Subject faces camera.
    p = np.zeros((33, 3))
    for left, right, height, width in [(23,24,0,.15), (11,12,-.5,.25),
                                      (7,8,-.75,.08), (25,26,.45,.13), (27,28,.88,.13)]:
        p[left] = (width, height, 0)
        p[right] = (-width, height, 0)
    p[0] = (0,-.78,-.12)
    for a,b,c,side in [(13,15,19,1),(14,16,20,-1)]:
        p[a] = (.5*side,-.5,0)
        p[b] = (.75*side,-.5,0)
        p[c] = (.82*side,-.5,0)
    p[31], p[32] = (.13,.9,-.2),(-.13,.9,-.2)
    return p


class MediaPipeMotionTests(unittest.TestCase):
    def setUp(self):
        path = SCRIPTS / 'mediapipe_motion.py'
        self.assertTrue(path.exists(), 'MediaPipe optional exporter is missing')
        sys.path.insert(0, str(SCRIPTS))
        spec = importlib.util.spec_from_file_location('mp_motion_under_test', path)
        self.mp = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.mp)

    def test_detected_arm_changes_rotation_with_fixed_in_place_root(self):
        poses = np.tile(sample_landmarks(), (10,1,1))
        poses[5:,13] = (.25,-.75,0)
        poses[5:,15] = (.25,-1,0)
        poses[5:,19] = (.25,-1.07,0)
        world, rest = self.mp.build_motion(poses)
        arm = self.mp.NAMES.index('LeftArm')
        self.assertGreater(np.linalg.norm(world[8,arm,:3,:3]-world[0,arm,:3,:3]), 1)
        np.testing.assert_allclose(world[:,0,:3,3], np.tile(world[0,0,:3,3],(10,1)))
        for rotations in (world[...,:3,:3],rest[...,:3,:3]):
            np.testing.assert_allclose(np.swapaxes(rotations,-1,-2)@rotations,np.broadcast_to(np.eye(3),rotations.shape),atol=1e-10)
            np.testing.assert_allclose(np.linalg.det(rotations),1,atol=1e-10)

    def test_quality_rejects_missing_or_invisible_majority(self):
        raw = np.tile(sample_landmarks(), (60,1,1))
        visibility = np.ones((60,33))
        raw[:30] = np.nan
        with self.assertRaisesRegex(ValueError, 'coverage'):
            self.mp.prepare_landmarks(raw,visibility,30)
        raw[:] = sample_landmarks()
        visibility[:,27:33] = 0
        with self.assertRaisesRegex(ValueError, 'coverage'):
            self.mp.prepare_landmarks(raw,visibility,30)

    def test_upper_body_leaves_unobserved_legs_in_local_bind_pose(self):
        poses = np.tile(sample_landmarks(),(3,1,1))
        poses[:,23] = (.1,0,.1)
        poses[:,24] = (-.1,0,-.1)
        world,rest = self.mp.build_motion(poses,coverage='upper_body')
        delta = world[:,12:,:3,:3]@np.swapaxes(rest[12:,:3,:3],-1,-2)
        np.testing.assert_allclose(delta,np.broadcast_to(world[:,0:1,:3,:3],(3,8,3,3)),atol=1e-10)

    def test_head_ear_axis_changes_head_yaw(self):
        poses = np.tile(sample_landmarks(),(3,1,1))
        poses[2,7] = (.05,-.75,.05)
        poses[2,8] = (-.05,-.75,-.05)
        world,rest = self.mp.build_motion(poses)
        head = self.mp.NAMES.index('Head')
        self.assertGreater(np.linalg.norm(world[2,head,:3,:3]-world[0,head,:3,:3]),.5)

    def test_terminal_hand_end_site_aims_along_unsolved_forearm(self):
        world,rest = self.mp.build_motion(np.tile(sample_landmarks(),(3,1,1)))
        for hand,arm,sign in [('LeftHand','LeftForeArm',1),('RightHand','RightForeArm',-1)]:
            h,a = self.mp.NAMES.index(hand),self.mp.NAMES.index(arm)
            np.testing.assert_allclose(world[0,h,:3,:3]@np.array([0,1,0]),world[0,a,:3,:3]@np.array([sign,0,0]),atol=1e-10)
            np.testing.assert_allclose(rest[h,:3,:3]@np.array([0,1,0]),[sign,0,0],atol=1e-10)

    def test_only_short_internal_gaps_are_interpolated_and_reported(self):
        raw = np.tile(sample_landmarks(), (60,1,1))
        raw[10:12] = np.nan
        filled, info = self.mp.prepare_landmarks(raw,np.ones((60,33)),30)
        self.assertEqual(info['interpolated_frame_indices'],[10,11])
        self.assertEqual(info['detected_frame_count'],58)
        self.assertTrue(np.isfinite(filled).all())
        raw[20:30] = np.nan
        with self.assertRaisesRegex(ValueError,'gap'):
            self.mp.prepare_landmarks(raw,np.ones((60,33)),30)

    def test_bvh_sidecar_is_bridge_compatible_and_labels_estimator(self):
        spec = importlib.util.spec_from_file_location('bridge_contract',SCRIPTS/'blender_bridge.py')
        bridge = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(bridge)
        world, rest = self.mp.build_motion(np.tile(sample_landmarks(),(3,1,1)))
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'draft.bvh'
            info = self.mp.write_bvh(path,world,rest,30,{'coverage':'full_body'})
            self.assertEqual(info['estimator'],'mediapipe_pose')
            self.assertEqual(info['root_motion_mode'],'in_place')
            self.assertFalse(info['world_translation_estimated'])
            self.assertEqual(info['unit'],'centimeters')
            self.assertLess(info['max_bvh_fk_position_error_m'],1e-4)
            self.assertEqual(len(bridge.parse_bvh(path.read_text())['joints']),20)
            convention = bridge.motion_convention(path,{})
            self.assertEqual(convention['axis_up'],'Y')
            self.assertEqual(convention['axis_forward'],'Z')
            self.assertEqual(len(convention['source_rest_world_rotations']),20)


if __name__ == '__main__':
    unittest.main()
