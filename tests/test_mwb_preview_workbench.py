"""Controller contracts for sequence previews; workers are isolated test doubles."""
import copy
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'scripts/motion/motion_workbench.py'


class PreviewWorkbenchTests(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location('preview_controller_test', SCRIPT)
        self.m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.m)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.requests = []

    def file(self, name, data='test artifact'):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(data, encoding='utf-8')
        return path

    def job(self):
        config = self.file('config.json', json.dumps({
            'schema_version': 1, 'blender': sys.executable, 'ffmpeg': sys.executable,
            'media_python': sys.executable, 'backend': {'python': sys.executable}}))
        j = {'schema_version': 1, 'inputs': {}, 'outputs': {}, 'failure': None,
             'review': None, 'history': [],
             'video_metadata': {'fps_num': 60000, 'fps_den': 1001},
             'interval': {'start_seconds': 2.25, 'duration': .4},
             'preview_scope': {'kind': 'single_frame', 'blender_frames': [1]}}
        for key in ('video', 'template', 'profile'):
            j['inputs'][key] = self.m.artifact(self.file('input-' + key))
        j['inputs']['config'] = self.m.artifact(config)
        for key in ('motion', 'candidate', 'preview', 'verification', 'bridge_report'):
            j['outputs'][key] = self.m.artifact(self.file('output-' + key))
        j['motion_provenance'] = {**j['outputs']['motion'], 'route': 'imported_motion'}
        return j

    def preview(self, job):
        function = getattr(self.m, 'preview', None)
        self.assertTrue(callable(function), 'A shipped preview command must create the continuous evidence')
        return function(self.root, job)

    def worker(self, argv, log, **kwargs):
        argv = list(map(str, argv))
        request = self.m.read_json(argv[argv.index('--request') + 1])
        self.requests.append((argv, request))
        out = Path(request['output_dir'])
        artifacts = {}
        for key in ('comparison_video', 'inspection_blend', 'preview_manifest', 'contact_sheet_1'):
            file = out / (key + '.test')
            file.write_text('worker output ' + key, encoding='utf-8')
            artifacts[key] = self.m.artifact(file)
        report = {'ok': True,
                  'input_sha256': {key: request[key]['sha256'] if request[key] else None
                                   for key in ('candidate', 'video', 'profile', 'parameters')},
                  'artifacts': artifacts,
                  'scope': {'kind': 'sequence', 'frame_count': 24, 'inspection_only': True},
                  'tracking_quality': {'available': False, 'reason': 'Imported motion has no tracking cache'}}
        self.m.write_json(out / 'report.json', report)

    def test_candidate_next_action_generates_sequence_preview(self):
        j = self.job()
        command = self.m.next_action(j, self.root)['command']
        self.assertIsNotNone(command, 'A candidate needs a shipped continuous-preview next step')
        self.assertEqual(command[2], 'preview')

    def test_preview_passes_nonzero_time_rational_fps_and_registers_bound_outputs(self):
        j = self.job()
        with patch.object(self.m, 'run_logged', side_effect=self.worker):
            result = self.preview(j)
        argv, request = self.requests[0]
        self.assertEqual(argv[0], sys.executable)
        self.assertEqual(Path(argv[1]).name, 'motion_preview.py')
        self.assertEqual(request['interval'], j['interval'])
        self.assertEqual(request['source_fps'], {'num': 60000, 'den': 1001})
        self.assertIsNone(request['parameters'])
        self.assertEqual(request['candidate'], j['outputs']['candidate'])
        self.assertTrue(self.m.intact(j['outputs']['sequence_preview_video']))
        self.assertIn('sequence_preview_report', j['outputs'])
        self.assertEqual(j['preview_scope']['kind'], 'single_frame')
        self.assertEqual(result['sequence_preview_scope']['kind'], 'sequence')
        self.assertIsNone(result['next_action']['command'])
        self.assertIsNone(j['review'])

    def test_repeated_preview_cannot_erase_prior_upstream_failure(self):
        j = self.job()
        j['failure'] = {'stage': 'extract', 'code': 'PROCESS_FAILED', 'message': 'real failure'}
        original = copy.deepcopy(j['failure'])
        with patch.object(self.m, 'run_logged') as worker:
            for _ in range(2):
                with self.assertRaises(self.m.WorkbenchError):
                    self.preview(j)
            worker.assert_not_called()
        self.assertEqual(j['failure'], original)

    def test_stale_upstream_candidate_cannot_be_previewed(self):
        j = self.job()
        Path(j['outputs']['candidate']['path']).write_text('changed')
        with patch.object(self.m, 'run_logged') as worker:
            with self.assertRaises(self.m.WorkbenchError):
                self.preview(j)
            worker.assert_not_called()

    def test_existing_failed_single_frame_review_is_preserved(self):
        j = self.job()
        self.m.review(self.root, j, SimpleNamespace(
            evidence=j['outputs']['preview']['path'], verdict='failed',
            reviewer='actual image viewer', notes='Observed shoulder seam'))
        original = copy.deepcopy(j['review'])
        with patch.object(self.m, 'run_logged', side_effect=self.worker):
            result = self.preview(j)
        self.assertEqual(j['review'], original)
        self.assertEqual(result['state'], 'needs_revision')

    def test_preview_can_rebuild_its_own_stale_outputs(self):
        j = self.job()
        with patch.object(self.m, 'run_logged', side_effect=self.worker):
            self.preview(j)
            old = j['outputs']['sequence_preview_video']['path']
            Path(old).unlink()
            result = self.preview(j)
        self.assertNotEqual(j['outputs']['sequence_preview_video']['path'], old)
        self.assertEqual(result['state'], 'candidate_ready')

    def test_gem_parameters_are_selected_from_current_motion_history_and_hash_checked(self):
        j = self.job()
        j['motion_provenance']['route'] = 'gem_video_inference'
        motion = Path(j['outputs']['motion']['path'])
        sidecar = motion.with_suffix(motion.suffix + '.json')
        sidecar.write_text('{}')
        j['outputs']['motion_metadata'] = self.m.artifact(sidecar)
        parameters = self.m.artifact(self.file('current-parameters.pt'))
        j['history'].append({'stage': 'extract', 'route': 'gem_video_inference',
                             'motion_sha256': j['outputs']['motion']['sha256'],
                             'parameters': parameters})
        with patch.object(self.m, 'run_logged', side_effect=self.worker):
            self.preview(j)
        self.assertEqual(self.requests[0][1]['parameters'], parameters)
        Path(parameters['path']).write_text('tampered')
        with patch.object(self.m, 'run_logged') as worker:
            with self.assertRaises(self.m.WorkbenchError):
                self.preview(j)
            worker.assert_not_called()

    def test_wrong_motion_history_cannot_supply_gem_parameters(self):
        j = self.job()
        j['motion_provenance']['route'] = 'gem_video_inference'
        motion = Path(j['outputs']['motion']['path'])
        sidecar = motion.with_suffix(motion.suffix + '.json')
        sidecar.write_text('{}')
        j['outputs']['motion_metadata'] = self.m.artifact(sidecar)
        j['history'].append({'stage': 'extract', 'motion_sha256': 'wrong',
                             'parameters': self.m.artifact(self.file('old-parameters.pt'))})
        with patch.object(self.m, 'run_logged') as worker:
            with self.assertRaises(self.m.WorkbenchError):
                self.preview(j)
            worker.assert_not_called()

    def test_preview_rejects_report_input_sha_or_artifact_sha_tampering(self):
        for attack in ('input_sha', 'artifact_sha', 'outside_path'):
            with self.subTest(attack=attack):
                j = self.job()
                def malicious_worker(argv, log, **kwargs):
                    self.worker(argv, log, **kwargs)
                    out = Path(self.requests[-1][1]['output_dir'])
                    report = self.m.read_json(out / 'report.json')
                    if attack == 'input_sha':
                        report['input_sha256']['candidate'] = 'old'
                    elif attack == 'artifact_sha':
                        report['artifacts']['comparison_video']['sha256'] = 'wrong'
                    else:
                        report['artifacts']['comparison_video'] = self.m.artifact(self.file('outside.mp4'))
                    self.m.write_json(out / 'report.json', report)
                with patch.object(self.m, 'run_logged', side_effect=malicious_worker):
                    with self.assertRaises(self.m.WorkbenchError):
                        self.preview(j)
                self.assertNotIn('sequence_preview_video', j['outputs'])

    def test_retarget_or_new_motion_invalidates_all_sequence_registrations(self):
        j = self.job()
        with patch.object(self.m, 'run_logged', side_effect=self.worker):
            self.preview(j)
        self.m.invalidate(j, ('candidate', 'preview', 'verification', 'bridge_report'))
        self.assertFalse(any(key.startswith('sequence_preview_') for key in j['outputs']))
        self.assertIsNone(j['sequence_preview_scope'])

    def test_sequence_review_is_bound_to_current_video_and_records_its_evidence_kind(self):
        j = self.job()
        with patch.object(self.m, 'run_logged', side_effect=self.worker):
            self.preview(j)
        result = self.m.review(self.root, j, SimpleNamespace(
            evidence=j['outputs']['sequence_preview_video']['path'], verdict='passed',
            reviewer='actual sequence viewer', notes='Observed this sequence only'))
        self.assertEqual(result['state'], 'motion_candidate_reviewed')
        self.assertEqual(j['review']['evidence_kind'], 'sequence')
        self.assertEqual(j['review']['evidence_artifact_key'], 'sequence_preview_video')

    def test_preview_worker_failure_can_be_retried_without_losing_candidate_or_review(self):
        j = self.job()
        with patch.object(self.m, 'run_logged', side_effect=self.m.WorkbenchError('PROCESS_FAILED', 'preview failed')):
            with self.assertRaises(self.m.WorkbenchError):
                self.preview(j)
        self.assertEqual(j['failure']['stage'], 'preview')
        self.assertIn('candidate', j['outputs'])
        with patch.object(self.m, 'run_logged', side_effect=self.worker):
            result = self.preview(j)
        self.assertEqual(result['state'], 'candidate_ready')


if __name__ == '__main__':
    unittest.main()
