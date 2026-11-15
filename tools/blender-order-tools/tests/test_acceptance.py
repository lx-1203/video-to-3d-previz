"""Interface tests use deliberately fake checker/updater fixtures, never production review."""
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest import mock
import test_core
from orderflow import acceptance, core


class AcceptanceTests(unittest.TestCase):
    setUp = test_core.CoreTests.setUp
    tearDown = test_core.CoreTests.tearDown

    def fixture(self, status='passed', delivery=False):
        self.spec = dict(self.spec, media_python=sys.executable)
        if delivery:
            test_core.CoreTests.make_delivery(self)
        else:
            core.write_job(self.job, dict(self.spec, mode='final'))
            (self.job/'scene.blend').write_bytes(b'candidate')
        skill = self.root/'installed_test_skill'
        (skill/'scripts').mkdir(parents=True)
        (skill/'SKILL.md').write_text('FAKE TEST SKILL')
        gate = skill/'scripts'/'check_update.py'
        gate.write_text('import json\nprint(json.dumps('+repr({'status':'current','rerun_required':False,'skill_file':str(skill/'SKILL.md')})+'))')
        checker = skill/'scripts'/'validate_skeleton_overlay.py'
        self.argv_path = self.root/'fake_checker_argv.json'
        checker.write_text('import json,sys\nfrom pathlib import Path\nPath('+repr(str(self.argv_path))+').write_text(json.dumps(sys.argv[1:]))\nprint(json.dumps('+repr({'status':status,'errors':[] if status=='passed' else ['test incomplete']})+'))\n')
        for name in ('protocol.json','report.json'):
            (self.root/name).write_text('{}')
        return {'protocol':str(self.root/'protocol.json'), 'report':str(self.root/'report.json'), 'checker':str(checker), 'motion_update_script':str(gate), 'required_actors':['actor_a']}

    def test_fresh_gate_exact_frozen_arguments_and_stale_inputs_rejected(self):
        config = self.fixture()
        result = acceptance.validate_for_package(self.job,config)
        args = json.loads(self.argv_path.read_text())
        for key,value in (('--blender-start','2'),('--source-start','7'),('--frame-count','3'),('--fps-num','30'),('--fps-den','1'),('--required-actor','actor_a')):
            self.assertEqual(args[args.index(key)+1], value)
        self.assertEqual(result['status'],'passed')
        acceptance.assert_unchanged(result)
        (self.root/'report.json').write_text('{"changed":true}')
        with self.assertRaises(ValueError): acceptance.assert_unchanged(result)

    def test_checker_cannot_change_frozen_report_or_override_expected_map(self):
        config = self.fixture()
        config['source_start'] = 999
        checker = Path(config['checker'])
        checker.write_text(checker.read_text()+ '\nPath('+repr(config['report'])+').write_text("changed by fake checker")\n')
        with self.assertRaisesRegex(ValueError, '已变化'):
            acceptance.validate_for_package(self.job, config)
        result = core.read_json(self.job/'acceptance_gate_result.json')
        self.assertEqual(result['status'],'failed')
        self.assertEqual(result['expected']['source_start'],7)

    def test_missing_unknown_checker_and_fractional_mapping_are_rejected(self):
        with self.assertRaises(ValueError): acceptance.validate_for_package(self.job,None)
        config = self.fixture('unknown')
        with self.assertRaises(ValueError): acceptance.validate_for_package(self.job,config)
        config['checker'] = str(self.root/'developer_checker.py')
        Path(config['checker']).write_text('print("passed")')
        with self.assertRaises(ValueError): acceptance.validate_for_package(self.job,config)
        path = self.job/'job.json'
        job = core.read_json(path)
        job['source_start_seconds'] = 0.21
        core.atomic_json(path,job)
        with self.assertRaises(ValueError): acceptance.validate_for_package(self.job,config)

    def test_actor_ids_and_checker_timeout_fail_closed(self):
        config = self.fixture()
        config['required_actors'] = ['actor_a','actor_a']
        with self.assertRaises(ValueError): acceptance.validate_for_package(self.job,config)
        config['required_actors'] = ['actor_a']
        with mock.patch('orderflow.motion._gate', return_value={'skill_file':str(Path(config['checker']).parents[1]/'SKILL.md')}), mock.patch('subprocess.run', side_effect=subprocess.TimeoutExpired('checker',1800)):
            with self.assertRaisesRegex(ValueError, '超时'):
                acceptance.validate_for_package(self.job,config)

    def test_reference_changed_since_frozen_job_cannot_pass_a_new_overlay(self):
        config = self.fixture()
        Path(self.spec['reference_video']).write_bytes(b'new source after old render')
        with self.assertRaisesRegex(ValueError, 'Frozen input changed'):
            acceptance.validate_for_package(self.job,config)

    def test_updater_cannot_replace_reference_baseline_mid_gate(self):
        config = self.fixture(delivery=True)
        frozen = core.load_job(self.job/'job.json')['input_sha256']['reference_video']
        updater = Path(config['motion_update_script'])
        updater.write_text(updater.read_text()+ '\nfrom pathlib import Path\nPath('+repr(self.spec['reference_video'])+').write_bytes(b"changed during gate")\n')
        with self.assertRaises(ValueError): core.package_submission(self.job, acceptance=config)
        result = core.read_json(self.job/'acceptance_gate_result.json')
        self.assertEqual(result['status'],'failed')
        self.assertEqual(result['frozen_files']['reference_video']['sha256'], frozen)
        self.assertEqual(list(self.job.glob('*.zip')), [])

    def test_checker_cannot_change_snapshot_or_optional_model_standard(self):
        standard = self.root/'model_standard.txt'
        standard.write_bytes(b'private standard fixture')
        self.spec['model_standard'] = str(standard)
        config = self.fixture(delivery=True)
        original_checker = Path(config['checker']).read_text()
        for key in ('snapshot_blend','source_blend','model_standard'):
            with self.subTest(key=key):
                target = Path(self.spec[key])
                original = target.read_bytes()
                Path(config['checker']).write_text(original_checker+'\nPath('+repr(str(target))+').write_bytes(b"changed during checker")\n')
                with self.assertRaises(ValueError): core.package_submission(self.job, acceptance=config)
                result = core.read_json(self.job/'acceptance_gate_result.json')
                self.assertEqual(result['status'],'failed')
                self.assertEqual(result['frozen_files'][key]['sha256'], core.load_job(self.job/'job.json')['input_sha256'][key])
                self.assertEqual(list(self.job.glob('*.zip')), [])
                target.write_bytes(original)

    def test_movie_changed_during_checker_leaves_no_openable_archive(self):
        config = self.fixture(delivery=True)
        checker = Path(config['checker'])
        checker.write_text(checker.read_text()+'\nPath('+repr(str(self.job/'white.mp4'))+').write_bytes(b"changed during checker")\n')
        with self.assertRaisesRegex(ValueError, 'SHA mismatch'):
            core.package_submission(self.job, acceptance=config)
        self.assertEqual(list(self.job.glob('*.zip')), [])
