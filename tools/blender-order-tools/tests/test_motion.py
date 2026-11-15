import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest

MODULE = Path(__file__).parents[1] / 'orderflow' / 'motion.py'


class MotionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.job = self.root / 'job'
        self.job.mkdir()
        (self.job / 'job.json').write_text('{}')
        self.marker = self.root / 'executed'
        self.gate = self.root / 'gate.py'
        self.skill = self.root / 'SKILL.md'
        self.skill.write_text('test skill')
        self.gate.write_text('import json\nprint(json.dumps(' + repr(dict(status='current', rerun_required=False, skill_file=str(self.skill))) + '))')
        self.cli = self.root / 'cli.py'
        self.config = dict(media_python=sys.executable, motion_cli=str(self.cli), motion_update_script=str(self.gate), motion_job=str(self.job), source_start_seconds=0)

    def tearDown(self):
        self.tmp.cleanup()

    def module(self):
        self.assertTrue(MODULE.is_file(), 'motion bridge is not implemented')
        spec = importlib.util.spec_from_file_location('motion_test', MODULE)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def status(self, command, **extra):
        payload = dict(ok=True, code='OK', state='ready', artifacts={}, next_action=dict(command=command, reason='test'))
        payload.update(extra)
        self.cli.write_text('import json,sys,os\nfrom pathlib import Path\nassert os.environ.get("PYTHONDONTWRITEBYTECODE")=="1"\nif sys.argv[1]=="status": print(json.dumps(' + repr(payload) + '))\nelse:\n Path(' + repr(str(self.marker)) + ').write_text("run")\n print(json.dumps({"ok":True,"code":"OK","state":"candidate","artifacts":{},"next_action":None}))\n')

    def test_allowed_next_and_gate(self):
        self.status([sys.executable, str(self.cli), 'extract', '--job', str(self.job)])
        result = self.module().run_action('next', self.config)
        self.assertTrue(result['ok'], result)
        self.assertTrue(self.marker.exists())
        self.assertIn('gate', result)

    def test_rejects_injected_and_wrong_commands(self):
        for command in ([sys.executable, str(self.cli), 'review', '--job', str(self.job)],
                        ['cmd.exe', str(self.cli), 'extract', '--job', str(self.job)],
                        [sys.executable, str(self.cli), 'extract', '--job', str(self.root)],
                        'echo injected',
                        [sys.executable, str(self.cli), 'extract', '--job', str(self.job), ';']):
            with self.subTest(command=command):
                self.status(command)
                result = self.module().run_action('next', self.config)
                self.assertFalse(result['ok'], result)
                self.assertFalse(self.marker.exists())

    def test_gate_failure_blocks_even_status(self):
        self.cli.write_text('from pathlib import Path\nPath(' + repr(str(self.marker)) + ').touch()')
        self.gate.write_text('print("not json")')
        result = self.module().run_action('status', self.config)
        self.assertFalse(result['ok'])
        self.assertFalse(self.marker.exists())

    def test_non_json_and_null_do_not_execute(self):
        self.cli.write_text('print("bad json")')
        self.assertFalse(self.module().run_action('next', self.config)['ok'])
        self.status(None)
        result = self.module().run_action('next', self.config)
        self.assertEqual(result['state'], 'ready')
        self.assertFalse(self.marker.exists())

    def test_failed_review_with_null_next_keeps_actual_status(self):
        self.status(None, state='needs_revision', code='NEEDS_REVISION', review={'verdict': 'failed'})
        result = self.module().run_action('next', self.config)
        self.assertEqual(result['state'], 'needs_revision')
        self.assertEqual(result['code'], 'NEEDS_REVISION')
        self.assertEqual(result['review'], {'verdict': 'failed'})
        self.assertFalse(self.marker.exists())

    def test_queue_invalid_paths_never_creates_directories(self):
        module = self.module()
        self.status(None)
        settings = SimpleNamespace(motion_job=str(self.job), source_start_seconds=0)
        for key in ('media_python', 'motion_cli', 'motion_update_script', 'motion_job'):
            for invalid in ('', str(self.root / 'missing')):
                config = dict(self.config, **{key: invalid})
                settings.motion_job = config['motion_job']
                prefs = SimpleNamespace(**{k: config[k] for k in ('media_python', 'motion_cli', 'motion_update_script')})
                with self.subTest(key=key, invalid=invalid):
                    with self.assertRaises(ValueError):
                        module.motion_status(settings, prefs)
                    self.assertFalse((self.job / 'ui_tasks').exists())
        settings.motion_job = str(self.cli)
        prefs = SimpleNamespace(**{k: self.config[k] for k in ('media_python', 'motion_cli', 'motion_update_script')})
        with self.assertRaises(ValueError):
            module.motion_status(settings, prefs)

    def test_gate_reruns_once(self):
        counter = self.root / 'counter'
        self.gate.write_text('import json\nfrom pathlib import Path\np=Path(' + repr(str(counter)) + ')\nn=int(p.read_text()) if p.exists() else 0\np.write_text(str(n+1))\nprint(json.dumps({"status":"updated","rerun_required":n==0,"skill_file":' + repr(str(self.skill)) + '}))')
        self.status(None)
        self.module().run_action('status', self.config)
        self.assertEqual(counter.read_text(), '2')

    def test_queued_helper_writes_atomic_failure_and_success(self):
        module = self.module()
        self.status(None)
        settings = SimpleNamespace(motion_job=str(self.job), source_start_seconds=0)
        prefs = SimpleNamespace(**{key: self.config[key] for key in ('media_python', 'motion_cli', 'motion_update_script')})
        for fails in (False, True):
            if fails:
                self.gate.write_text('raise SystemExit(2)')
            queued = module.motion_status(settings, prefs)
            self.assertEqual(queued['state'], 'queued')
            deadline = time.monotonic() + 15
            while not Path(queued['result_path']).exists() and time.monotonic() < deadline:
                time.sleep(.05)
            result = module._read(queued['result_path'])
            for process in module._HELPERS:
                process.wait(timeout=5)
            self.assertEqual(result['ok'], not fails)
            self.assertTrue({'ok', 'state', 'code', 'message', 'artifacts', 'next_action'} <= result.keys())
            if not fails:
                proof = result['import_proof']
                self.assertEqual(proof['job_sha256'], module._sha(self.job / 'job.json'))
                self.assertEqual(proof['helper_sha256'], module._sha(MODULE))


if __name__ == '__main__':
    unittest.main()
