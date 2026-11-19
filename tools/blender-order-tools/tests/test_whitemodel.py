from fractions import Fraction
import json
from pathlib import Path
import tempfile
import unittest

from orderflow import core, local_pipeline, multiactor, scene_tools


class WhiteModelTests(unittest.TestCase):
    def test_opening_builds_two_posts_and_a_header(self):
        parts = scene_tools.blockout_parts('OPENING', (4, 0.2, 3), (1, 2))
        self.assertEqual(len(parts), 3)
        self.assertEqual(parts[2], ((0, 0, 2.5), (1, 0.2, 1)))
        with self.assertRaises(ValueError):
            scene_tools.blockout_parts('OPENING', (4, 0.2, 3), (4, 2))

    def test_detect_input_and_anchor_provenance(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            detections = root / 'frames.jsonl'
            rows = [dict(window='s1', source_zero_based=i, observed_tracks=[
                dict(window_local_track_id=7, observed=True)]) for i in (4, 5)]
            detections.write_text('\n'.join(json.dumps(row) for row in rows), encoding='utf-8')
            multiactor.check_track(detections, 's1', 7, 4, 5)
            with self.assertRaisesRegex(ValueError, '轨迹'):
                multiactor.check_track(detections, 's1', 8, 4, 5)

    def test_reference_frames_use_exact_pts(self):
        detections = {7: Fraction(1, 10), 8: Fraction(7, 50), 9: Fraction(1, 5)}
        result = multiactor.blender_frames(detections, 10, Fraction(25), Fraction(1, 10))
        self.assertEqual(result, {7: 10, 8: 11, 9: None})

    def test_local_pipeline_loads_only_a_matching_candidate(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / 'sample.mp4'
            candidate = root / 'sample.blend'
            source.write_bytes(b'synthetic source')
            candidate.write_bytes(b'synthetic candidate')
            snapshot = root / 'inputs.json'
            core.atomic_json(snapshot, {'video': {'sha256': core.sha256(source)}})
            core.atomic_json(root / 'run_report.json', {
                'input_snapshot': {'path': str(snapshot), 'sha256': core.sha256(snapshot)},
                'outputs': {'blend': {'path': str(candidate), 'sha256': core.sha256(candidate)}}})
            self.assertEqual(local_pipeline.candidate(root, source), candidate.resolve())
            source.write_bytes(b'changed')
            with self.assertRaisesRegex(ValueError, '另一份原片'):
                local_pipeline.candidate(root, source)


if __name__ == '__main__':
    unittest.main()
