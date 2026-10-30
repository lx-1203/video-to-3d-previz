"""Offline regressions for task ownership, CLI metadata, and setup/download recovery."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stdout
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
from types import SimpleNamespace, ModuleType
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def module(name):
    spec = importlib.util.spec_from_file_location('safety_' + name, ROOT / 'scripts/motion' / (name + '.py'))
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


class SafetyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.m = module('motion_workbench')

    def file(self, name, text='synthetic fixture'):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding='utf-8')
        return path

    def init_args(self, start=0):
        paths = {name: self.file(name) for name in ('video', 'template', 'profile')}
        paths['config'] = self.file('config.json', json.dumps({'schema_version': 1, 'backend': {}}))
        return SimpleNamespace(job=str(self.root / 'job'), start=start, duration=1, motion=None, **paths)

    def test_stale_snapshot_is_rejected_without_overwriting_new_state(self):
        directory = self.root / 'job'
        directory.mkdir()
        self.m.write_json(directory / 'job.json', {'schema_version': 1, 'inputs': {}, 'outputs': {}, 'history': [], 'failure': None})
        _, first = self.m.load_job(directory)
        _, stale = self.m.load_job(directory)
        with self.m.operation(directory, first, 'preview'):
            first['outputs']['candidate'] = {'path': 'new candidate'}
            first['history'].append({'stage': 'retarget'})
        current_bytes = (directory / 'job.json').read_bytes()
        with self.assertRaises(self.m.WorkbenchError) as caught:
            with self.m.operation(directory, stale, 'review'):
                stale['review'] = {'verdict': 'passed'}
        self.assertEqual(caught.exception.code, 'STALE_JOB')
        self.assertEqual((directory / 'job.json').read_bytes(), current_bytes)
        self.assertFalse((directory / '.operation.lock').exists())

    def test_successful_snapshot_can_be_reused_without_serializing_internal_version(self):
        self.m.write_json(self.root / 'job.json', {'schema_version': 1, 'inputs': {}, 'outputs': {}, 'history': [], 'failure': None})
        _, job = self.m.load_job(self.root)
        for step in (1, 2):
            with self.m.operation(self.root, job, 'review'):
                job['history'].append(step)
        current = self.m.read_json(self.root / 'job.json')
        self.assertEqual(current['history'], [1, 2])
        self.assertEqual(set(current), {'schema_version', 'inputs', 'outputs', 'history', 'failure', 'updated_at'})

    def test_concurrent_init_cannot_claim_an_initializing_job(self):
        first_args = self.init_args()
        second_args = SimpleNamespace(**vars(first_args))
        second_args.start = 3
        entered, release = threading.Event(), threading.Event()
        calls = []
        def probe(*args):
            calls.append(True)
            if len(calls) == 1:
                entered.set()
                if not release.wait(10):
                    raise RuntimeError('test synchronization timed out')
            return {'fps_num': 30, 'fps_den': 1, 'width': 16, 'height': 16, 'duration': 5}
        with patch.object(self.m, 'probe_video', side_effect=probe), ThreadPoolExecutor(1) as pool:
            first = pool.submit(self.m.init_job, first_args)
            self.assertTrue(entered.wait(10))
            try:
                with self.assertRaises(self.m.WorkbenchError) as caught:
                    self.m.init_job(second_args)
                self.assertIn(caught.exception.code, ('BUSY', 'JOB_EXISTS'))
            finally:
                release.set()
            self.assertTrue(first.result()['ok'])
        self.assertEqual(self.m.read_json(self.root / 'job/job.json')['interval']['start_seconds'], 0)

    def test_unknown_average_fps_uses_valid_stream_rate(self):
        probe = self.file('ffprobe.exe')
        response = {'streams': [{'avg_frame_rate': '0/0', 'r_frame_rate': '30000/1001', 'width': 16, 'height': 16, 'duration': '5'}]}
        with patch.object(self.m.subprocess, 'run', return_value=SimpleNamespace(returncode=0, stdout=json.dumps(response))):
            try:
                result = self.m.probe_video('synthetic video', {'ffprobe': str(probe)})
            except Exception as exc:
                self.fail('Valid backup stream rate must be usable: ' + repr(exc))
        self.assertEqual((result['fps_num'], result['fps_den']), (30000, 1001))

    def test_all_invalid_fps_returns_structured_cli_failure(self):
        args = self.init_args()
        probe = self.file('ffprobe.exe')
        self.file('config.json', json.dumps({'schema_version': 1, 'ffprobe': str(probe), 'backend': {}}))
        response = {'streams': [{'avg_frame_rate': '0/0', 'r_frame_rate': '0/0', 'width': 16, 'height': 16, 'duration': '5'}]}
        stdout = io.StringIO()
        argv = ['init', '--job', args.job]
        for key in ('video', 'template', 'profile', 'config'):
            argv.extend(['--' + key, str(getattr(args, key))])
        with patch.object(self.m.subprocess, 'run', return_value=SimpleNamespace(returncode=0, stdout=json.dumps(response))), redirect_stdout(stdout):
            try:
                code = self.m.main(argv)
            except Exception as exc:
                self.fail('Metadata failure must return JSON: ' + repr(exc))
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(stdout.getvalue())['code'], 'INVALID_VIDEO_METADATA')


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_metadata_failure_records_current_failed_attempt(self):
        m = module('download_backend_assets')
        status_path = self.root / 'evidence/backend/asset_download_status.json'
        status_path.parent.mkdir(parents=True)
        status_path.write_text(json.dumps({'status': 'verified', 'files': ['old'], 'failed': []}))
        observed = []
        class FakeApi:
            def __init__(self, **kwargs):
                observed.append(json.loads(status_path.read_text())['status'])
            def model_info(self, *args, **kwargs):
                raise OSError('synthetic metadata outage')
        fake = ModuleType('huggingface_hub')
        fake.HfApi = FakeApi
        with patch.dict(sys.modules, {'huggingface_hub': fake}), patch.dict(os.environ), patch.object(m.time, 'sleep'), \
                patch.object(sys, 'argv', ['download', '--root', str(self.root)]), redirect_stdout(io.StringIO()):
            try:
                m.main()
            except (OSError, SystemExit):
                pass
        current = json.loads(status_path.read_text())
        self.assertEqual(observed, ['running'])
        self.assertEqual(current['status'], 'failed')
        self.assertTrue(any('synthetic metadata outage' in row.get('error', '') for row in current['failed']))

    def test_missing_hub_dependency_records_failure(self):
        m = module('download_backend_assets')
        with patch.dict(sys.modules, {'huggingface_hub': None}), patch.dict(os.environ), \
                patch.object(sys, 'argv', ['download', '--root', str(self.root)]), redirect_stdout(io.StringIO()):
            try:
                m.main()
            except (ImportError, SystemExit):
                pass
        status_path = self.root / 'evidence/backend/asset_download_status.json'
        self.assertTrue(status_path.is_file(), 'Early dependency failure must be persisted')
        self.assertEqual(json.loads(status_path.read_text())['status'], 'failed')

    def test_failed_whole_file_hash_invalidates_only_owned_segments(self):
        m = module('download_backend_assets')
        fake = ModuleType('huggingface_hub')
        fake.hf_hub_download = lambda *args, **kwargs: self.fail('No real Hub calls allowed')
        unrelated = self.root / 'user-original.range00'
        unrelated.write_bytes(b'keep user file')
        parts = []
        def segmented(url, target, size, segments):
            target.write_bytes(b'bad assembly')
            for index in range(segments):
                part = target.with_name(target.name + f'.range{index:02d}')
                part.write_bytes(b'bad chunk')
                parts.append(part)
            return parts
        info = SimpleNamespace(rfilename='model.bin', size=101 * 1024 * 1024,
                               lfs=SimpleNamespace(sha256='0' * 64))
        job = ('public/model', 'pinned', self.root, info, 'urllib', 'huggingface.co', 4)
        with patch.dict(sys.modules, {'huggingface_hub': fake}), patch.object(m, 'download_segmented', side_effect=segmented):
            with self.assertRaises(RuntimeError) as caught:
                m.download_model(job)
        self.assertIn('Integrity check failed', str(caught.exception))
        self.assertTrue(parts)
        self.assertFalse(any(part.exists() for part in parts), 'Bad tool-owned segments must not poison retries')
        self.assertEqual(unrelated.read_bytes(), b'keep user file')

    def test_setup_preserves_previous_generated_files_and_each_attempt_logs(self):
        m = module('setup_backend')
        (self.root / 'runtime/bin').mkdir(parents=True)
        originals = {'config.generated.json': b'{"custom":"keep"}',
                     'runtime/gem_body_video_8gb.yaml': b'custom: keep', 'runtime/bin/ffmpeg.exe': b'old ffmpeg'}
        for name, value in originals.items():
            (self.root / name).write_bytes(value)
        blender, ffmpeg = self.root / 'blender.exe', self.root / 'input_ffmpeg.exe'
        blender.write_bytes(b'placeholder'); ffmpeg.write_bytes(b'new ffmpeg')
        args = SimpleNamespace(workspace=str(self.root), blender=str(blender), ffmpeg=str(ffmpeg), plan_only=False)
        def run(argv, **kwargs):
            if '-c' in argv:
                Path(argv[-1]).write_text('generated: default')
            return SimpleNamespace(returncode=0, stdout='')
        with patch.object(m.shutil, 'which', return_value=sys.executable), patch.object(m.subprocess, 'run', side_effect=run):
            first = m.setup(args)
            second = m.setup(args)
        self.assertNotEqual(first['logs'], second['logs'], 'Recovery must retain the previous attempt logs')
        self.assertTrue(Path(first['logs']).is_dir())
        self.assertTrue(Path(second['logs']).is_dir())
        for name, value in originals.items():
            backup = Path(first['logs']) / 'backup' / name
            self.assertTrue(backup.is_file(), 'Missing backup: ' + name)
            self.assertEqual(backup.read_bytes(), value)


if __name__ == '__main__':
    unittest.main()
