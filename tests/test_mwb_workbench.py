"""State/provenance tests; these do not simulate or certify video inference."""
import importlib.util
import json
import math
from pathlib import Path
import tempfile
import unittest
import subprocess
import sys
from unittest.mock import patch
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'scripts/motion/motion_workbench.py'


class WorkbenchTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(SCRIPT.is_file(), 'The workflow CLI has not been implemented')
        spec = importlib.util.spec_from_file_location('mwb', SCRIPT)
        self.m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.m)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def write(self, name, data='original'):
        p = self.root / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(data, encoding='utf-8')
        return p

    def job(self):
        return {'schema_version': 1, 'inputs': {
            name: self.m.artifact(self.write(name))
            for name in ('video', 'template', 'profile', 'config')
        }, 'outputs': {}, 'review': None, 'failure': None}

    def test_old_bvh_cannot_override_failed_extraction(self):
        j = self.job()
        j['outputs']['motion'] = self.m.artifact(self.write('old.bvh'))
        j['failure'] = {'stage': 'extract', 'code': 'BACKEND_FAILED'}
        self.assertEqual(self.m.derive_state(j), 'blocked')

    def test_changed_source_invalidates_downstream(self):
        j = self.job()
        j['outputs']['motion'] = self.m.artifact(self.write('a.bvh'))
        Path(j['inputs']['video']['path']).write_text('changed')
        self.assertEqual(self.m.derive_state(j), 'stale_inputs')

    def test_candidate_is_not_visual_acceptance(self):
        j = self.job()
        j['outputs']['motion'] = self.m.artifact(self.write('a.bvh'))
        j['outputs']['candidate'] = self.m.artifact(self.write('candidate.blend'))
        self.assertEqual(self.m.derive_state(j), 'candidate_ready')

    def test_review_is_bound_to_current_artifact(self):
        j = self.job()
        j['outputs']['motion'] = self.m.artifact(self.write('a.bvh'))
        j['outputs']['candidate'] = self.m.artifact(self.write('candidate.blend'))
        j['outputs']['preview'] = self.m.artifact(self.write('preview.png'))
        j['review'] = {'verdict': 'passed', 'candidate_sha256': 'old',
                       'preview_sha256': j['outputs']['preview']['sha256']}
        self.assertEqual(self.m.derive_state(j), 'candidate_ready')

    def test_mutated_output_invalidates_review(self):
        j = self.job()
        j['outputs']['motion'] = self.m.artifact(self.write('a.bvh'))
        Path(j['outputs']['motion']['path']).write_text('tampered')
        self.assertEqual(self.m.derive_state(j), 'stale_artifacts')

    def bvh(self, rows='0 0 0 0 0 0\n0 0 0 0 0 0', frames=2, dt='0.04'):
        return self.write('motion.bvh',
            'HIERARCHY\nROOT Hips\n{\nOFFSET 0 0 0\n'
            'CHANNELS 6 Xposition Yposition Zposition Zrotation Xrotation Yrotation\n'
            'End Site\n{\nOFFSET 0 1 0\n}\n}\nMOTION\n'
            f'Frames: {frames}\nFrame Time: {dt}\n{rows}\n')

    def test_bvh_fps_is_measured_not_assumed(self):
        r = self.m.inspect_bvh(self.bvh())
        self.assertAlmostEqual(r['fps'], 25.0)
        self.assertEqual(r['frames'], 2)

    def test_truncated_or_nonfinite_motion_is_rejected(self):
        for rows in ('0 0 0 0 0 0', '0 0 0 0 0 0\n0 nan 0 0 0 0'):
            with self.subTest(rows=rows), self.assertRaises(self.m.WorkbenchError):
                self.m.inspect_bvh(self.bvh(rows))

    def test_zero_bvh_frame_time_is_rejected(self):
        with self.assertRaises(self.m.WorkbenchError):
            self.m.inspect_bvh(self.bvh(dt='0'))

    def test_import_preserves_and_hashes_coordinate_sidecar(self):
        j = self.job()
        j.update(video_metadata={'fps_num':25,'fps_den':1},
                 interval={'duration':0.08}, history=[])
        src = self.bvh()
        sidecar = src.with_suffix('.bvh.json')
        sidecar.write_text(json.dumps({'format':'motion_workbench_soma_bvh_v1',
                                       'bvh_sha256':self.m.digest(src),
                                       'forward_axis':'Z'}), encoding='utf-8')
        self.m.attach_motion(self.root, j, src)
        copied = Path(j['outputs']['motion']['path']).with_suffix('.bvh.json')
        self.assertTrue(copied.is_file(), 'SOMA coordinate sidecar was lost')
        self.assertEqual(j['outputs']['motion_metadata']['sha256'], self.m.digest(sidecar))
        copied.write_text('{}')
        self.assertEqual(self.m.derive_state(j), 'stale_artifacts')

    def test_invalid_config_returns_json_instead_of_traceback(self):
        config = self.write('bad-config.json', '{"schema_version":1,"backend":[]}')
        p = subprocess.run([sys.executable, str(SCRIPT), 'doctor', '--config', str(config)],
                           capture_output=True, text=True, encoding='utf-8')
        self.assertTrue(p.stdout.strip(), 'A malformed local config must still produce structured JSON')
        self.assertEqual(p.returncode, 2)
        self.assertEqual(json.loads(p.stdout)['code'], 'INVALID_CONFIG')

    def test_repeated_retarget_cannot_erase_missing_producer_success(self):
        j = self.job()
        j['inputs']['config'] = self.m.artifact(self.write('config.json', json.dumps({
            'schema_version':1, 'blender':sys.executable, 'backend':{}})))
        j.update(video_metadata={'fps_num':25,'fps_den':1},
                 interval={'duration':0.08,'start_seconds':0}, history=[])
        j['outputs']['motion'] = self.m.artifact(self.write('partial.bvh'))
        j['failure'] = {'stage':'extract','code':'MISSING_METADATA'}
        with patch.object(self.m, 'run_logged', side_effect=self.m.WorkbenchError('WORKER_REACHED','test sentinel')) as worker:
            for _ in range(2):
                with self.assertRaises(self.m.WorkbenchError):
                    self.m.retarget(self.root, j)
            worker.assert_not_called()
        self.assertEqual(j['failure']['code'], 'MOTION_PROVENANCE_REQUIRED')

    def test_new_unregistered_sidecar_cannot_change_existing_motion(self):
        j = self.job()
        j['inputs']['config'] = self.m.artifact(self.write('config.json', json.dumps({
            'schema_version':1,'blender':sys.executable,'backend':{}})))
        j.update(video_metadata={'fps_num':25,'fps_den':1},
                 interval={'duration':0.08,'start_seconds':0},history=[])
        self.m.attach_motion(self.root, j, self.bvh())
        Path(j['outputs']['motion']['path']).with_suffix('.bvh.json').write_text('{}')
        with patch.object(self.m, 'run_logged', side_effect=self.m.WorkbenchError('WORKER_REACHED','test sentinel')):
            with self.assertRaises(self.m.WorkbenchError) as caught:
                self.m.retarget(self.root,j)
        self.assertEqual(caught.exception.code,'UNREGISTERED_MOTION_METADATA')

    def test_selected_external_motion_next_action_never_starts_video_inference(self):
        j = self.job()
        j['inputs']['selected_motion'] = self.m.artifact(self.bvh())
        command = self.m.next_action(j,self.root)['command']
        self.assertEqual(command[2],'attach-motion')
        self.assertEqual(command[-2:],["--motion",j['inputs']['selected_motion']['path']])

    def test_gpu_inference_and_cpu_contact_processing_are_separate(self):
        j = self.job()
        template = self.bvh().read_text(encoding='utf-8')
        backend = {'python':str(self.write('python.exe')), 'root':str(self.root),
                   'motion_config':str(self.write('motion.yaml')), 'soma_assets':str(self.root),
                   'revision':'0'*40, 'device':'cuda'}
        j['inputs']['config'] = self.m.artifact(self.write('config.json',json.dumps({
            'schema_version':1,'ffmpeg':sys.executable,'backend':backend})))
        j.update(video_metadata={'fps_num':25,'fps_den':1},interval={'start_seconds':0,'duration':.08},history=[])
        commands = []
        def fake_external(argv, *args, **kwargs):
            argv = list(map(str,argv)); commands.append(argv)
            out = Path(argv[argv.index('--output')+1])
            if out.suffix == '.bvh':
                out.write_text(template)
                metadata = self.m.inspect_bvh(out)
                metadata.update(bvh_sha256=self.m.digest(out),
                    input_sha256=self.m.digest(Path(argv[argv.index('--input')+1])), postprocessed=True)
                out.with_suffix('.bvh.json').write_text(json.dumps(metadata))
            else:
                out.write_bytes(b'test-only result, not real inference')
        with patch.object(self.m,'run_logged',side_effect=fake_external), patch.object(
                self.m.subprocess,'run',return_value=SimpleNamespace(returncode=0,stdout='0'*40)):
            self.m.extract(self.root,j)
        self.assertNotIn('--postprocess',commands[0], 'CUDA contact fails on the tested Windows runtime')
        self.assertIn('motion-cache',commands[1])
        self.assertEqual(commands[1][commands[1].index('--device')+1],'cpu')


if __name__ == '__main__':
    unittest.main()
