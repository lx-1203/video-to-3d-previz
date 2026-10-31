import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/motion/multi_actor.py'


class MultiActorTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(SCRIPT.exists(), 'multi_actor implementation is missing')
        spec = importlib.util.spec_from_file_location('multi_actor', SCRIPT)
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)
        self.data = {'frames': [{'shot_id': 's1', 'frame': 0, 'detections': [
            {'track_id': 1, 'source': 'observed', 'box': [0, 0, 1, 1]},
            {'track_id': 2, 'source': 'filled', 'box': [2, 0, 3, 1]}]}]}
        self.manual = {'actor_ids': ['alice', 'bob'], 'frames': [{
            'shot_id': 's1', 'frame': 0, 'expected_actor_ids': ['alice', 'bob'],
            'mappings': [{'actor_id': 'alice', 'track_id': 1},
                         {'actor_id': 'bob', 'track_id': 2}]}]}

    def build(self):
        return self.module.build_candidate(self.data, self.manual)

    def test_preserves_every_detection_and_provenance(self):
        original = copy.deepcopy(self.data)
        result = self.build()
        self.assertTrue(result['valid'])
        self.assertFalse(result['delivery_ready'])
        self.assertEqual(result['frames'][0]['input'], original['frames'][0])
        self.assertEqual([a['source'] for a in result['frames'][0]['actors']], ['observed', 'filled'])
        self.assertEqual(self.data, original)

    def test_track_is_not_identity_and_is_shot_scoped(self):
        self.data['frames'].append({'shot_id': 's2', 'frame': 0,
                                   'detections': [{'track_id': 1}]})
        self.manual['frames'].append({'shot_id': 's2', 'frame': 0,
            'expected_actor_ids': ['bob'], 'mappings': [{'actor_id': 'bob', 'track_id': 1}]})
        result = self.build()
        self.assertTrue(result['valid'])
        self.assertEqual(result['frames'][1]['actors'][0]['actor_id'], 'bob')
        self.assertEqual(result['frames'][1]['actors'][0]['source'], 'unknown')

    def test_conflicts_duplicates_and_omissions_fail_closed(self):
        for kind in ('duplicate_actor', 'duplicate_track', 'unmapped', 'absent_track',
                     'duplicate_frame', 'missing_frame', 'undeclared_actor', 'missing_expected',
                     'duplicate_detection', 'bad_source', 'empty_expected'):
            with self.subTest(kind=kind):
                data, manual = copy.deepcopy(self.data), copy.deepcopy(self.manual)
                frame = manual['frames'][0]
                if kind == 'duplicate_actor': frame['mappings'].append(frame['mappings'][0])
                if kind == 'duplicate_track': frame['mappings'][1]['track_id'] = 1
                if kind == 'unmapped': frame['mappings'].pop()
                if kind == 'absent_track': frame['mappings'][1]['track_id'] = 99
                if kind == 'duplicate_frame': data['frames'].append(data['frames'][0])
                if kind == 'missing_frame': manual['frames'] = []
                if kind == 'undeclared_actor': frame['mappings'][0]['actor_id'] = 'stranger'
                if kind == 'missing_expected': del frame['expected_actor_ids']
                if kind == 'duplicate_detection': data['frames'][0]['detections'].append(data['frames'][0]['detections'][0])
                if kind == 'bad_source': data['frames'][0]['detections'][0]['source'] = 'guessed'
                if kind == 'empty_expected': frame['expected_actor_ids'] = []
                result = self.module.build_candidate(data, manual)
                self.assertFalse(result['valid'])
                self.assertTrue(result['errors'])
                self.assertEqual(result['frames'][0]['input'], data['frames'][0])

    def test_missing_actor_is_explicit_and_blocks(self):
        self.manual['frames'][0]['mappings'][1] = {'actor_id': 'bob', 'track_id': None, 'source': 'missing'}
        result = self.build()
        self.assertFalse(result['valid'])
        self.assertEqual(result['frames'][0]['actors'][1]['source'], 'missing')

    def test_trial_format_preserved_without_promoting_lost_tracks(self):
        self.data = {'frames': [{'window': 's1', 'source_zero_based': 0, 'pts': 123,
            'raw_detections': [{'score': .2}, {'score': .9}],
            'observed_tracks': [{'window_local_track_id': 1}],
            'lost_buffer': [{'window_local_track_id': 2, 'observed': False}]}]}
        result = self.build()
        self.assertFalse(result['valid'])
        self.assertEqual(len(result['frames'][0]['input']['raw_detections']), 2)
        self.assertEqual(result['frames'][0]['actors'][1]['source'], 'unknown')

    def test_malformed_source_retains_input_and_fails_closed(self):
        self.data['frames'][0]['detections'][0]['source'] = ['observed']
        result = self.build()
        self.assertFalse(result['valid'])
        self.assertEqual(result['frames'][0]['input'], self.data['frames'][0])

    def test_jsonl_cli_and_unknown_retention(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            row = {'window': 's1', 'source_zero_based': 0, 'blender_one_based': 1,
                   'pts': 0, 'time_base': '1/30', 'raw_detections': [{'score': .8}],
                   'observed_tracks': [{'window_local_track_id': 1}], 'lost_buffer': []}
            (base/'d.jsonl').write_text(json.dumps(row)+'\n', encoding='utf-8')
            (base/'m.json').write_text(json.dumps({'actor_ids': [], 'frames': []}), encoding='utf-8')
            run = subprocess.run([sys.executable, '-B', str(SCRIPT), '--detections', str(base/'d.jsonl'),
                '--mapping', str(base/'m.json'), '--output', str(base/'out.json')], capture_output=True)
            self.assertEqual(run.returncode, 2)
            audit = json.loads((base/'out.json').read_text())
            self.assertEqual(audit['frames'][0]['input'], row)
            self.assertEqual(audit['frames'][0]['unmapped_detection_indices'], [0])
            self.assertEqual(audit['frames'][0]['actors'], [])
            self.assertEqual(len(audit['input_sha256']['detections']), 64)

    def test_unknown_cannot_be_upgraded(self):
        self.data['frames'][0]['detections'][0]['source'] = 'unknown'
        self.assertEqual(self.build()['frames'][0]['actors'][0]['source'], 'unknown')
        self.manual['frames'][0]['mappings'][0]['source'] = 'observed'
        result = self.build()
        self.assertFalse(result['valid'])
        self.assertEqual(result['frames'][0]['actors'][0]['source'], 'unknown')

    def test_cli_rejects_duplicate_keys_nonfinite_and_input_aliases(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            d, m, out = base/'d.json', base/'m.json', base/'out.json'
            m.write_text(json.dumps(self.manual), encoding='utf-8')
            def run(target):
                return subprocess.run([sys.executable, '-B', str(SCRIPT), '--detections', str(d),
                    '--mapping', str(m), '--output', str(target)], capture_output=True)
            for text in ('{"frames":[],"frames":[]}', '{"frames":NaN}', '{"frames":Infinity}'):
                d.write_text(text, encoding='utf-8')
                self.assertEqual(run(out).returncode, 2)
                self.assertFalse(json.loads(out.read_text())['valid'])
            d.write_text(json.dumps(self.data), encoding='utf-8')
            original = d.read_bytes()
            self.assertEqual(run(d).returncode, 2)
            self.assertEqual(d.read_bytes(), original)
            alias = base/'alias.json'
            alias.hardlink_to(d)
            self.assertEqual(run(alias).returncode, 2)
            self.assertEqual(d.read_bytes(), original)
            alias.unlink()
            alias.hardlink_to(m)
            original_mapping = m.read_bytes()
            self.assertEqual(run(alias).returncode, 2)
            self.assertEqual(m.read_bytes(), original_mapping)

    def test_cli_writes_audit_on_failure_and_nonzero_exit(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            (base / 'd.json').write_text(json.dumps(self.data), encoding='utf-8')
            (base / 'm.json').write_text(json.dumps(self.manual), encoding='utf-8')
            argv = [sys.executable, '-B', str(SCRIPT), '--detections', str(base/'d.json'),
                    '--mapping', str(base/'m.json'), '--output', str(base/'out.json')]
            self.assertEqual(subprocess.run(argv, capture_output=True).returncode, 0)
            valid_input = (base / 'd.json').read_text()
            for overflow in ('1e999', '-1e999'):
                (base / 'd.json').write_text(valid_input.replace('0, 0, 1, 1', overflow + ', 0, 1, 1'), encoding='utf-8')
                self.assertEqual(subprocess.run(argv, capture_output=True).returncode, 2)
                failed = json.loads((base / 'out.json').read_text())
                self.assertFalse(failed['valid'])
                self.assertIn('nonfinite', failed['errors'][0])
            (base / 'd.json').write_text(valid_input, encoding='utf-8')
            self.manual['frames'][0]['mappings'] = []
            (base / 'm.json').write_text(json.dumps(self.manual), encoding='utf-8')
            self.assertEqual(subprocess.run(argv, capture_output=True).returncode, 2)
            self.assertFalse(json.loads((base/'out.json').read_text())['valid'])


if __name__ == '__main__':
    unittest.main()
