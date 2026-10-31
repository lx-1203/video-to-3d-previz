"""Standard-library integration tests; fake GEM only, never Torch."""
import copy
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'scripts/motion/multi_actor_pose.py'


class PoseRunnerTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(SCRIPT.exists(), 'runner implementation missing')
        spec = importlib.util.spec_from_file_location('runner', SCRIPT)
        self.m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.m)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.repo = self.root / 'repo'
        (self.repo / 'gem/inference').mkdir(parents=True)
        (self.repo / 'gem/__init__.py').write_text('')
        (self.repo / 'gem/inference/__init__.py').write_text('')
        (self.repo / 'gem/inference/__main__.py').write_text(
            'import sys,pathlib\n'
            'p=pathlib.Path("calls");p.write_text(p.read_text()+"x" if p.exists() else "x")\n'
            'print("fake pose log")\n'
            'if pathlib.Path("fail").exists(): sys.exit(7)\n'
            'pathlib.Path(sys.argv[sys.argv.index("--output")+1]).write_bytes(b"opaque fake result")\n')
        self.source = self.root / 'source.mp4'
        self.source.write_bytes(b'fake source')
        self.ckpt = self.root / 'weights.ckpt'
        self.ckpt.write_bytes(b'fake checkpoint')
        self.assets = self.root / 'assets'
        self.assets.mkdir()
        (self.assets / 'asset').write_bytes(b'asset')
        self.candidate = {'valid': True, 'errors': [], 'frames': [
            {'shot_id': 's', 'frame': 568, 'expected_actor_ids': ['A'],
             'input': {'shot_id': 's', 'frame': 568, 'pts': 290816,
                       'time_base': '1/15360', 'detections': [
                           {'track_id': 4, 'source': 'observed', 'box': [10, 20, 40, 80]}]},
             'actors': [{'actor_id': 'A', 'track_id': 4, 'source': 'observed', 'detection_index': 0}]}]}
        self.k = [[1280, 0, 640], [0, 1280, 360], [0, 0, 1]]

    def plan(self):
        return self.m.prepare(self.candidate, source=self.source,
            source_sha256=self.m.sha256(self.source), intrinsics=self.k,
            python=sys.executable, repo=self.repo, checkpoint=self.ckpt,
            soma_assets=self.assets, device='cpu', work=self.root / 'job')

    def test_boxes_and_exact_timeline(self):
        p = self.plan()
        boxes = json.loads(Path(p['boxes']['path']).read_text())
        row = boxes['frames']['568']['instances'][0]
        self.assertEqual(row['bbox'], [25.0, 50.0, 72.0])
        self.assertEqual(row['track_id'], 'A')
        self.assertEqual(p['bindings'][0]['pts'], 290816)
        self.assertIn('pose-video', p['argv'])
        self.assertFalse(p['pose_visual_accepted'])

    def test_unknown_and_filled_are_not_truth(self):
        for source in ('unknown', 'filled'):
            self.candidate['frames'][0]['actors'][0]['source'] = source
            self.candidate['frames'][0]['input']['detections'][0]['source'] = source
            with self.assertRaises(ValueError): self.plan()

    def test_invalid_or_forged_candidate_rejected(self):
        for change in ('valid', 'pts', 'source', 'box'):
            saved = copy.deepcopy(self.candidate)
            if change == 'valid': self.candidate['valid'] = False
            elif change == 'pts': del self.candidate['frames'][0]['input']['pts']
            elif change == 'source': self.candidate['frames'][0]['input']['detections'][0]['source'] = 'filled'
            else: self.candidate['frames'][0]['input']['detections'][0]['box'] = [2, 0, 1, 5]
            with self.assertRaises(ValueError): self.plan()
            self.candidate = saved

    def test_actual_process_and_receipt_reuse(self):
        p = self.plan()
        r = self.m.execute(p)
        self.assertEqual(r['exit_code'], 0)
        self.assertIn('fake pose log', Path(r['log']['path']).read_text())
        self.assertTrue(self.m.execute(p)['reused'])
        self.assertEqual((self.repo / 'calls').read_text(), 'x')
        Path(p['output']).write_bytes(b'changed')
        with self.assertRaises(ValueError): self.m.execute(p)

    def test_changed_inputs_cannot_reuse(self):
        p = self.plan()
        self.m.execute(p)
        self.ckpt.write_bytes(b'changed')
        with self.assertRaises(ValueError): self.m.execute(p)

    def test_failure_exit_and_no_reuse(self):
        p = self.plan()
        (self.repo / 'fail').touch()
        r = self.m.execute(p)
        self.assertEqual(r['exit_code'], 7)
        self.assertFalse(r['reused'])
        self.assertIsNone(r['result'])

    def test_import_is_binding_only_and_needs_hash(self):
        p = self.plan()
        result = self.root / 'old.pt'
        result.write_bytes(b'not loaded')
        manifest = {'fingerprint': p['fingerprint'], 'result': {
            'path': str(result), 'sha256': self.m.sha256(result)},
            'bindings': p['bindings'], 'data_scope': 'formal_arm_points_candidate_2d'}
        imported = self.m.import_result(p, manifest)
        self.assertFalse(imported['temporal_multiactor_or_contact_solved'])
        self.assertEqual(imported['status'], 'imported_binding_unvalidated')
        manifest['fingerprint'] = 'wrong'
        with self.assertRaises(ValueError): self.m.import_result(p, manifest)

    def test_cli_execute_and_reuse(self):
        p = self.plan()
        for _ in range(2):
            r = subprocess.run([sys.executable, str(SCRIPT), 'execute',
                                '--plan', str(Path(p['work']) / 'plan.json')],
                               capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual((self.repo / 'calls').read_text(), 'x')

    def test_real_trial_box_xyxy(self):
        row = self.candidate['frames'][0]
        d = row['input'].pop('detections')[0]
        d['box_xyxy'] = d.pop('box')
        d['window_local_track_id'] = d.pop('track_id')
        row['input']['observed_tracks'] = [d]
        self.assertEqual(self.plan()['bindings'][0]['actor_id'], 'A')

    def test_official_bbox_and_inferred_preserved(self):
        row = self.candidate['frames'][0]
        d = row['input']['detections'][0]
        d['bbox'] = [25, 50, 72]
        del d['box']
        d['source'] = row['actors'][0]['source'] = 'unknown'
        p = self.m.prepare(self.candidate, source=self.source,
            source_sha256=self.m.sha256(self.source), intrinsics=self.k,
            python=sys.executable, repo=self.repo, checkpoint=self.ckpt,
            soma_assets=self.assets, device='cpu', work=self.root / 'inferred', allow_inferred=True)
        instance = json.loads(Path(p['boxes']['path']).read_text())['frames']['568']['instances'][0]
        self.assertFalse(instance['bbox_observed'])
        self.assertEqual(p['bindings'][0]['source'], 'unknown')

    def test_imported_result_reused_without_launch(self):
        p = self.plan()
        result = self.root / 'existing.pt'
        result.write_bytes(b'opaque old result')
        self.m.import_result(p, {'fingerprint': p['fingerprint'], 'bindings': p['bindings'],
            'result': self.m._file(result), 'data_scope': 'candidate_2d'})
        self.assertTrue(self.m.execute(p)['reused'])
        self.assertFalse((self.repo / 'calls').exists())

    def test_reuse_requires_success_status(self):
        p = self.plan()
        self.m.execute(p)
        receipt_path = Path(p['work']) / 'receipt.json'
        r = json.loads(receipt_path.read_text())
        r['inputs_unchanged'] = False
        r['status'] = 'failed'
        receipt_path.write_text(json.dumps(r))
        with self.assertRaises(ValueError): self.m.execute(p)

    def test_official_boxes_preserve_bytes(self):
        boxes, _ = self.m._boxes(self.candidate, self.k, False)
        old = self.root / 'official.json'
        old.write_text(json.dumps(boxes, separators=(',', ':')))
        p = self.m.prepare(self.candidate, source=self.source,
            source_sha256=self.m.sha256(self.source), intrinsics=self.k,
            python=sys.executable, repo=self.repo, checkpoint=self.ckpt,
            soma_assets=self.assets, device='cpu', work=self.root / 'exact', official_boxes=old)
        self.assertEqual(p['boxes']['sha256'], self.m.sha256(old))

    def test_existing_anchor_manifest_evidence_chain(self):
        p = self.plan()
        result = self.root / 'cpu.pt'
        result.write_bytes(b'opaque actual result')
        proposal = self.root / 'GPU_POSE_3ANCHOR_PROPOSAL.json'
        proposal.write_text(json.dumps({key: p[key] for key in ('source', 'boxes', 'checkpoint')}))
        receipt = self.root / 'cpu_trial_result.json'
        receipt.write_text(json.dumps({'exit_code': 0, 'proposal_sha256': self.m.sha256(proposal),
            'source_sha256_after': p['source']['sha256'], 'boxes_sha256_after': p['boxes']['sha256'],
            'output': str(result), 'output_sha256': self.m.sha256(result)}))
        review = self.root / 'POSE_RESULT_REVIEW.json'
        review.write_text(json.dumps({'input_sha256': self.m.sha256(result),
            'source_sha256': p['source']['sha256']}))
        manifest = {'source': p['source'], 'bindings': [{
            'source_sha256': p['source']['sha256'], 'actor_id': 'A', 'source_zero_based': 568,
            'pts': 290816, 'time_base': '1/15360', 'formal_arm_points': {'LeftHand': {'xy': [5, 7]}}}],
            'evidence_sha256': {str(x): self.m.sha256(x) for x in (proposal, receipt, review, result, Path(p['boxes']['path']))}}
        imported = self.m.import_result(p, manifest)
        self.assertEqual(imported['data_scope'], 'formal_arm_points_candidate_2d_not_full_77')
        self.assertFalse(imported['content_deserialized'])
        self.assertTrue(self.m.execute(p)['reused'])
        review.write_text('{}')
        with self.assertRaises(ValueError): self.m.execute(p)

    def test_observability_is_independent_and_preserved(self):
        raw = self.candidate['frames'][0]['input']
        raw['actor_observability'] = {'D': 'arm_only_no_crop'}
        raw['partial_observation'] = {'A': 'feet_outside'}
        p = self.plan()
        self.assertEqual(p['bindings'][0]['frame_input'], raw)
        self.assertFalse(p['all_actors_covered'])

    def test_json_overflow_rejected(self):
        path = self.root / 'bad.json'
        path.write_text('{"x":1e999}')
        with self.assertRaises(ValueError): self.m._load(path)

    def test_configured_output_and_collision(self):
        kwargs = dict(source=self.source, source_sha256=self.m.sha256(self.source),
            intrinsics=self.k, python=sys.executable, repo=self.repo, checkpoint=self.ckpt,
            soma_assets=self.assets, device='cpu', work=self.root / 'custom')
        with self.assertRaises(ValueError):
            self.m.prepare(self.candidate, **kwargs, output=self.source)
        p = self.m.prepare(self.candidate, **kwargs, output=self.root / 'custom.pt')
        self.assertEqual(self.m.execute(p)['result']['path'], str(self.root / 'custom.pt'))

    def test_identity_hypothesis_unknown_require_opt_in(self):
        for status in ('hypothesis', 'unknown'):
            actor = self.candidate['frames'][0]['actors'][0]
            actor.update(identity_status=status, evidence=[{'anchor': 'ai-range'}])
            self.candidate['identity_verified'] = False
            with self.assertRaisesRegex(ValueError, 'identity'): self.plan()
            _, bindings = self.m._boxes(self.candidate, self.k, True)
            self.assertEqual(bindings[0]['identity_status'], status)
            self.assertEqual(bindings[0]['evidence'], actor['evidence'])
            self.assertFalse(bindings[0]['identity_verified'])
            actor['evidence'][0]['anchor'] = 'changed'
            self.assertEqual(bindings[0]['evidence'][0]['anchor'], 'ai-range')

    def test_identity_false_without_status_is_unknown(self):
        self.candidate['identity_verified'] = False
        with self.assertRaisesRegex(ValueError, 'identity'): self.plan()
        _, bindings = self.m._boxes(self.candidate, self.k, True)
        self.assertEqual(bindings[0]['identity_status'], 'unknown')

    def test_legacy_identity_not_promoted_from_scope_label(self):
        self.candidate['scope'] = 'confirmed_observed_crop_subset'
        p = self.plan()
        self.assertEqual(p['bindings'][0]['identity_status'], 'unreviewed')
        self.assertFalse(p['bindings'][0]['identity_verified'])

    def test_proposal_confirmed_subset_and_hypothesis_audit(self):
        spec = importlib.util.spec_from_file_location('identity', ROOT / 'scripts/motion/actor_identity_proposal.py')
        proposal = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(proposal)
        anchor = dict(shot_id='s', actor_id='A', track_id=4, start_frame=568,
                      end_frame=568, confirmation='confirmed', evidence={'image': 'anchor.png'})
        raw = copy.deepcopy(self.candidate['frames'][0]['input'])
        result = proposal.build_proposal({'frames': [raw]}, {'actor_ids': ['A'], 'anchors': [anchor]})
        _, bindings = self.m._boxes(result['crop_candidate'], self.k, False)
        self.assertEqual(bindings[0]['identity_status'], 'confirmed')
        self.assertEqual(bindings[0]['evidence'], [anchor])
        anchor['confirmation'] = 'hypothesis'
        result = proposal.build_proposal({'frames': [raw]}, {'actor_ids': ['A'], 'anchors': [anchor]})
        with self.assertRaisesRegex(ValueError, 'identity'):
            self.m._boxes(result['audit_candidate'], self.k, False)

    def test_python_environment_cannot_select_other_gem(self):
        other = self.root / 'other'
        (other / 'gem/inference').mkdir(parents=True)
        (other / 'gem/__init__.py').touch()
        (other / 'gem/inference/__init__.py').touch()
        marker = self.root / 'WRONG_GEM'
        (other / 'gem/inference/__main__.py').write_text(
            'from pathlib import Path\nPath('+repr(str(marker))+').touch()\n')
        entry = self.repo / 'gem/inference/__main__.py'
        with entry.open('a') as stream:
            stream.write('import os,json,gem\nprint("CONTROLLED:"+json.dumps({'
                '"cwd":str(pathlib.Path.cwd()),"gem":gem.__file__,'
                '"variables":{k:os.environ.get(k) for k in '
                '("PYTHONPATH","PYTHONSAFEPATH","PYTHONHOME")}}))\n')
        p = self.plan()
        with patch.dict(os.environ, {'PYTHONPATH': str(other), 'PYTHONSAFEPATH': '1',
                                     'PYTHONHOME': sys.prefix}):
            r = self.m.execute(p)
        self.assertEqual(r['exit_code'], 0)
        self.assertFalse(marker.exists())
        self.assertEqual((self.repo / 'calls').read_text(), 'x')
        self.assertEqual(r['execution_environment']['cwd'], str(self.repo))
        for name in ('PYTHONPATH', 'PYTHONSAFEPATH', 'PYTHONHOME'):
            self.assertIsNone(r['execution_environment']['variables'][name])
        lines = Path(r['log']['path']).read_text().splitlines()
        actual = json.loads(next(line.removeprefix('CONTROLLED:') for line in lines
                                 if line.startswith('CONTROLLED:')))
        self.assertEqual(actual['cwd'], str(self.repo))
        self.assertEqual(Path(actual['gem']).resolve(), self.repo / 'gem/__init__.py')
        self.assertTrue(all(value is None for value in actual['variables'].values()))
        self.assertTrue(self.m.execute(p)['reused'])

    def test_no_local_package_cannot_fall_through_to_installed_gem(self):
        (self.repo / 'gem/__init__.py').unlink()
        with self.assertRaisesRegex(ValueError, 'package'): self.plan()

    def test_old_uncontrolled_environment_receipt_cannot_reuse(self):
        p = self.plan()
        r = self.m.execute(p)
        r.pop('execution_environment', None)
        (Path(p['work']) / 'receipt.json').write_text(json.dumps(r))
        with self.assertRaises(ValueError): self.m.execute(p)

    def test_dropped_actor_identity_metadata_recovers_mapping_claim(self):
        row = self.candidate['frames'][0]
        self.candidate['mapping_input'] = {'frames': [{
            'shot_id': 's', 'frame': 568, 'mappings': [{
                'actor_id': 'A', 'track_id': 4, 'identity_status': 'hypothesis',
                'evidence': {'range': [568, 569]}}]}]}
        with self.assertRaisesRegex(ValueError, 'identity'): self.plan()
        _, bindings = self.m._boxes(self.candidate, self.k, True)
        self.assertEqual(bindings[0]['identity_status'], 'hypothesis')
        self.assertEqual(bindings[0]['evidence'], {'range': [568, 569]})
        row['actors'][0]['identity_status'] = 'confirmed'
        with self.assertRaisesRegex(ValueError, 'conflicting identity'):
            self.m._boxes(self.candidate, self.k, True)

    def test_invalid_pythonhome_is_removed_before_start(self):
        p = self.plan()
        with patch.dict(os.environ, {'PYTHONHOME': str(self.root / 'not-a-python-home')}):
            receipt = self.m.execute(p)
        self.assertEqual(receipt['exit_code'], 0)
        self.assertEqual((self.repo / 'calls').read_text(), 'x')


if __name__ == '__main__': unittest.main()
