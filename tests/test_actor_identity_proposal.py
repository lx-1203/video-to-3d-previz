import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'scripts/motion' / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def frame(n, tracks=(1,), shot='s'):
    return dict(shot_id=shot, frame=n, pts=n, time_base='1/25',
                detections=[dict(track_id=t, source='observed', box=[0, 0, 20, 40]) for t in tracks])


def anchor(track=1, actor='A', start=0, end=3, confirmation='confirmed', shot='s'):
    return dict(shot_id=shot, track_id=track, actor_id=actor, start_frame=start,
                end_frame=end, confirmation=confirmation, evidence={'image': 'review.png'})


class IdentityTests(unittest.TestCase):
    def build(self, rows, anchors, **extra):
        return load('actor_identity_proposal').build_proposal(
            {'frames': rows}, dict(actor_ids=['A', 'B'], anchors=anchors, **extra))

    def test_range_expansion_no_guess_on_switch_cut_or_range_end(self):
        r = self.build([frame(0), frame(1), frame(2, (9,)), frame(3, shot='cut'), frame(4)], [anchor()])
        self.assertEqual([f['identities'][0]['identity_status'] for f in r['frames']],
                         ['confirmed', 'confirmed', 'unknown', 'unknown', 'unknown'])
        self.assertEqual(len(r['crop_candidate']['frames']), 2)
        self.assertTrue(r['crop_candidate']['valid'])
        self.assertFalse(r['all_actors_covered'])
        for cropped in r['crop_candidate']['frames']:
            self.assertEqual(cropped['actors'][0]['identity_status'], 'confirmed')
            self.assertEqual(cropped['actors'][0]['evidence'], [anchor()])
        # Crop metadata is independently copied from the audit and anchor input.
        r['crop_candidate']['frames'][0]['actors'][0]['evidence'][0]['evidence']['image'] = 'changed'
        self.assertEqual(r['audit_candidate']['frames'][0]['actors'][0]['evidence'], [anchor()])
        self.assertEqual(r['hypotheses_input']['anchors'], [anchor()])

    def test_hypothesis_never_promoted_and_evidence_retained(self):
        a = anchor(confirmation='hypothesis')
        r = self.build([frame(0)], [a])
        self.assertEqual(r['frames'][0]['identities'][0]['identity_status'], 'hypothesis')
        self.assertEqual(r['frames'][0]['identities'][0]['evidence'], [a])
        self.assertEqual(r['crop_candidate']['frames'], [])
        self.assertEqual(len(r['unresolved_detections']), 1)
        self.assertEqual(r['audit_candidate']['frames'][0]['actors'][0]['identity_status'], 'hypothesis')

    def test_conflicting_track_and_actor_claims_block_all_involved(self):
        for anchors in ([anchor(), anchor(actor='B')],
                        [anchor(), anchor(track=2)],
                        [anchor(), anchor(actor='B', confirmation='hypothesis')]):
            r = self.build([frame(0, (1, 2))], anchors)
            self.assertTrue(r['conflicts'])
            self.assertFalse(r['crop_candidate']['frames'])

    def test_partial_visibility_missing_actor_and_raw_not_cropped_away(self):
        row = frame(0, (1, 9))
        row['raw_detections'] = [{'untracked': True}]
        row['source_visibility'] = {'A': {'arm': 'visible', 'legs': 'unknown'}, 'B': 'partial'}
        original = copy.deepcopy(row)
        r = self.build([row], [anchor()])
        self.assertEqual(r['input']['frames'][0], original)
        self.assertEqual(r['frames'][0]['source_visibility'], row['source_visibility'])
        self.assertEqual(r['frames'][0]['missing_actor_ids'], ['B'])
        self.assertEqual(r['mapping']['frames'][0]['mappings'][1],
                         {'actor_id': 'B', 'track_id': None, 'source': 'missing', 'identity_status': 'unknown'})
        self.assertEqual(len(r['unresolved_detections']), 1)
        candidate = r['crop_candidate']
        self.assertEqual(candidate['frames'][0]['expected_actor_ids'], ['A'])
        self.assertEqual(candidate['frames'][0]['input']['raw_detections'], row['raw_detections'])
        boxes, bindings = load('multi_actor_pose')._boxes(candidate, [[10, 0, 5], [0, 10, 5], [0, 0, 1]], False)
        self.assertEqual(len(bindings), 1)
        self.assertFalse(bindings[0]['visible_joint_truth'])

    def test_trial_schema_and_source_not_upgraded(self):
        row = dict(window='s', source_zero_based=0, pts=0, time_base='1/25',
                   observed_tracks=[dict(window_local_track_id=1, box=[0, 0, 2, 3])], lost_buffer=[99])
        r = self.build([row], [anchor()])
        self.assertTrue(r['crop_candidate']['valid'])
        self.assertEqual(r['input']['frames'][0]['lost_buffer'], [99])
        self.assertEqual(r['crop_candidate']['frames'][0]['actors'][0]['identity_status'], 'confirmed')
        self.assertEqual(r['crop_candidate']['frames'][0]['actors'][0]['evidence'], [anchor()])
        row = frame(0)
        row['detections'][0]['source'] = 'filled'
        self.assertFalse(self.build([row], [anchor()])['crop_candidate']['frames'])

    def test_invalid_contract_rejected(self):
        for a in (dict(anchor(), evidence=None), dict(anchor(), start_frame=4),
                  dict(anchor(), confirmation='automatic'), dict(anchor(), actor_id='X')):
            with self.assertRaises(ValueError):
                self.build([frame(0)], [a])

    def test_overlap_only_blocks_overlap_and_point_does_not_expand(self):
        r = self.build([frame(i) for i in range(4)], [anchor(end=2), anchor(actor='B', start=2, end=2)])
        self.assertEqual([f['identities'][0]['identity_status'] for f in r['frames']],
                         ['confirmed', 'confirmed', 'unknown', 'unknown'])
        r = self.build([frame(0), frame(1)], [anchor(end=0)])
        self.assertEqual(r['frames'][1]['identities'][0]['identity_status'], 'unknown')

    def test_partial_without_crop_keeps_identity_and_visibility(self):
        row = frame(0)
        del row['detections'][0]['box']
        row['source_visibility'] = {'A': {'hand': 'visible', 'body': 'unknown'}}
        r = self.build([row], [anchor()])
        self.assertEqual(r['frames'][0]['identities'][0]['identity_status'], 'confirmed')
        self.assertFalse(r['crop_candidate']['frames'])
        self.assertNotIn('box', r['input']['frames'][0]['detections'][0])
        self.assertEqual(r['frames'][0]['required_actor_ids'], ['A', 'B'])

    def test_jsonl_cli_hashes_inputs_and_refuses_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            tracking, anchors, output = root/'frames.jsonl', root/'anchors.json', root/'out.json'
            tracking.write_text(json.dumps(frame(0)) + '\n', encoding='utf-8')
            anchors.write_text(json.dumps(dict(actor_ids=['A'], anchors=[anchor()])), encoding='utf-8')
            argv = [sys.executable, str(ROOT/'scripts/motion/actor_identity_proposal.py'),
                    '--detections', str(tracking), '--anchors', str(anchors), '--output', str(output)]
            run = subprocess.run(argv, capture_output=True, text=True)
            self.assertEqual(run.returncode, 0, run.stderr + run.stdout)
            payload = json.loads(output.read_text(encoding='utf-8'))
            self.assertEqual(len(payload['input_sha256']['detections']), 64)
            before = output.read_bytes()
            self.assertEqual(subprocess.run(argv, capture_output=True).returncode, 2)
            self.assertEqual(output.read_bytes(), before)


if __name__ == '__main__':
    unittest.main()
