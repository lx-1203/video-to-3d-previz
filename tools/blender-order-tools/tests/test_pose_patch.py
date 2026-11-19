import importlib.util
from pathlib import Path
import sys
import unittest


SCRIPTS = Path(__file__).resolve().parents[1] / 'orderflow/auto_box/scripts'
sys.path.insert(0, str(SCRIPTS))
spec = importlib.util.spec_from_file_location('orderflow_pose_patch_test', SCRIPTS / 'pose_patch.py')
patch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(patch)


def mapping(frame, *entries):
    return {'shot_id': 's', 'frame': frame, 'expected_actor_ids': [actor for actor, _ in entries],
            'mappings': [{'actor_id': actor, 'track_id': track} for actor, track in entries]}


def pose(frame, *actors):
    return {'frame_id': frame, 'instances': [{'track_id': actor} for actor in actors]}


class PosePatchTests(unittest.TestCase):
    def test_reinference_only_includes_actors_changed_on_each_frame(self):
        full_map = {'actor_ids': ['a', 'b', 'c'], 'frames': [mapping(3, ('c', 1), ('b', 2)),
                     mapping(4, ('a', 1), ('c', 2))]}
        deltas = [{'shot_id': 's', 'frame': 3, 'removed': {'a': 1}, 'added': {'c': 1}},
                  {'shot_id': 's', 'frame': 4, 'removed': {'b': 2}, 'added': {'c': 2}}]
        changed, selected = patch.select_patch(full_map, deltas)
        self.assertEqual(selected, {'actor_ids': ['c'], 'frames': [mapping(3, ('c', 1)), mapping(4, ('c', 2))]})
        merged, _ = patch.merge_patch([pose(3, 'a', 'b'), pose(4, 'a', 'b')],
                                       [pose(3, 'c'), pose(4, 'c')], changed, selected)
        self.assertEqual(merged, [pose(3, 'b', 'c'), pose(4, 'a', 'c')])

    def test_removed_only_frames_do_not_need_inference(self):
        changed, selected = patch.select_patch({'frames': [mapping(3, ('b', 2))]},
            [{'shot_id': 's', 'frame': 3, 'removed': {'a': 1}, 'added': {}}])
        self.assertEqual(selected['frames'], [])
        merged, _ = patch.merge_patch([pose(3, 'a', 'b')], [], changed, selected)
        self.assertEqual(merged, [pose(3, 'b')])

    def test_missing_reinference_cannot_replace_baseline(self):
        deltas = [{'shot_id': 's', 'frame': 3, 'removed': {'a': 1}, 'added': {'c': 1}}]
        changed, selected = patch.select_patch({'frames': [mapping(3, ('c', 1))]}, deltas)
        with self.assertRaisesRegex(ValueError, 'coverage'):
            patch.merge_patch([pose(3, 'a')], [], changed, selected)
