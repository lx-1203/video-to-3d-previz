"""Synthetic registration and interruption tests; no inference or SOMA execution."""
import copy
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import test_export_recovery as fixtures


class FixtureCase(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.ResumeGuards('test_valid_checkpoint_binds_stage_and_source')
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.setUp()
        self.mw = fixtures.mw

    def export_stage(self, argv, log, *, wrong_input=False, **kwargs):
        argv = list(map(str, argv))
        output = Path(argv[argv.index('--output') + 1])
        if argv[1] == '-m':
            value = fixtures.fixture_result('motion-cache' in argv)
            value['video']['source']['path'] = str(self.fixture.video)
            fixtures.torch.save(value, output)
        else:
            world = fixtures.np.tile(fixtures.np.eye(4), (3, 1, 1, 1))
            info = fixtures.exporter.write_motion_bvh(
                output, ['Hips'], [-1], world, fixtures.np.eye(4)[None], 30)
            info.update(input_sha256='0' * 64 if wrong_input else self.mw.digest(
                Path(argv[argv.index('--input') + 1])), postprocessed=True)
            self.mw.write_json(output.with_suffix('.bvh.json'), info)


class NormalRegistration(FixtureCase):
    def test_incomplete_resume_arguments_cannot_start_inference(self):
        mw = self.mw
        before = (self.fixture.jobdir / 'job.json').read_bytes()
        for options in ({'receipt_sha256': '0' * 64},
                        {'resume_checkpoint': self.fixture.root / 'receipt.json'},
                        {'resume_checkpoint': '', 'receipt_sha256': '0' * 64},
                        {'resume_checkpoint': self.fixture.root / 'receipt.json', 'receipt_sha256': ''}):
            with self.subTest(options=options):
                (self.fixture.jobdir / 'job.json').write_bytes(before)
                directory, job = mw.load_job(self.fixture.jobdir)
                with patch.object(mw.subprocess, 'run', return_value=SimpleNamespace(
                        returncode=0, stdout='fixture-commit')), \
                        patch.object(mw, 'run_logged', side_effect=mw.WorkbenchError(
                            'UNEXPECTED_INFERENCE', 'No backend stage may start')) as run, \
                        self.assertRaises(mw.WorkbenchError):
                    mw.extract(directory, job, **options)
                self.assertFalse(run.called, 'Incomplete resume arguments started a backend stage')
                self.assertEqual((directory / 'job.json').read_bytes(), before)
                self.assertFalse((directory / '.operation.lock').exists())

    def test_normal_extract_uses_registration_once_and_binds_pt(self):
        mw = self.mw
        directory, job = mw.load_job(self.fixture.jobdir)
        with patch.object(mw.subprocess, 'run', return_value=SimpleNamespace(
                returncode=0, stdout='fixture-commit')), \
                patch.object(mw, 'run_logged', self.export_stage), \
                patch.object(mw, 'register_extracted_motion',
                             wraps=mw.register_extracted_motion) as register:
            result = mw.extract(directory, job)
        self.assertEqual(result['state'], 'motion_ready')
        self.assertEqual(register.call_count, 1)
        self.assertEqual(job['motion_provenance']['route'], 'gem_video_inference')
        metadata = mw.read_json(job['outputs']['motion_metadata']['path'])
        self.assertEqual(job['history'][-1]['parameters']['sha256'], metadata['input_sha256'])

    def test_normal_extract_rejects_old_sidecar_and_clears_outputs(self):
        mw = self.mw
        directory, job = mw.load_job(self.fixture.jobdir)

        def stale_export(argv, log, **kwargs):
            return self.export_stage(argv, log, wrong_input=True, **kwargs)

        with patch.object(mw.subprocess, 'run', return_value=SimpleNamespace(
                returncode=0, stdout='fixture-commit')), \
                patch.object(mw, 'run_logged', stale_export), self.assertRaises(mw.WorkbenchError):
            mw.extract(directory, job)
        self.assertEqual(job['outputs'], {})
        self.assertIsNone(job['motion_provenance'])
        self.assertEqual(job['failure']['code'], 'MOTION_METADATA_MISMATCH')

    def test_resume_rejects_old_sidecar_and_preserves_failed_snapshot(self):
        mw = self.mw
        receipt = self.fixture.root / 'receipt.json'
        mw.write_json(receipt, self.fixture.make_receipt())
        before = (self.fixture.jobdir / 'job.json').read_bytes()
        directory, job = mw.load_job(self.fixture.jobdir)

        def stale_export(argv, log, **kwargs):
            return self.export_stage(argv, log, wrong_input=True, **kwargs)

        with patch.object(mw, 'run_logged', stale_export), self.assertRaises(mw.WorkbenchError):
            mw.extract(directory, job, resume_checkpoint=receipt, receipt_sha256=mw.digest(receipt))
        self.assertEqual((directory / 'job.json').read_bytes(), before)
        self.assertEqual(job['outputs'], {})


class AuditDestination(FixtureCase):
    def audit(self, destination):
        fixture = self.fixture
        argv = ['audit-export-checkpoint', '--job', str(fixture.jobdir),
                '--attempt', fixture.attempt.name,
                '--raw-sha256', self.mw.digest(fixture.attempt / 'motion_raw.pt'),
                '--processed-sha256', self.mw.digest(fixture.attempt / 'motion.pt'),
                '--original-exporter', str(fixtures.PRIVATE / 'export_gem_bvh.py'),
                '--out', str(destination)]
        with redirect_stdout(io.StringIO()) as output:
            result = self.mw.main(argv)
        return result, json.loads(output.getvalue())

    def test_audit_cannot_replace_original_job(self):
        path = self.fixture.jobdir / 'job.json'
        before = path.read_bytes()
        result, payload = self.audit(path)
        self.assertNotEqual(result, 0)
        self.assertFalse(payload['ok'])
        self.assertEqual(path.read_bytes(), before)

    def test_audit_cannot_replace_existing_receipt(self):
        path = self.fixture.root / 'existing_receipt.json'
        path.write_bytes(b'preserve previous evidence')
        result, payload = self.audit(path)
        self.assertNotEqual(result, 0)
        self.assertFalse(payload['ok'])
        self.assertEqual(path.read_bytes(), b'preserve previous evidence')

    def test_audit_writes_new_receipt_without_changing_job(self):
        path = self.fixture.root / 'new_receipt.json'
        before = (self.fixture.jobdir / 'job.json').read_bytes()
        result, payload = self.audit(path)
        self.assertEqual(result, 0)
        self.assertEqual(payload['code'], 'CHECKPOINT_AUDITED')
        self.assertEqual(payload['receipt']['sha256'], self.mw.digest(path))
        self.assertEqual((self.fixture.jobdir / 'job.json').read_bytes(), before)


class InterruptRecovery(FixtureCase):
    def assert_interrupted_resume_is_retryable(self, error):
        mw = self.mw
        recovery = self.fixture.recovery
        receipt_data = self.fixture.make_receipt()
        receipt = self.fixture.root / 'receipt.json'
        mw.write_json(receipt, receipt_data)
        receipt_sha = mw.digest(receipt)
        directory, job = mw.load_job(self.fixture.jobdir)
        original_bytes = (directory / 'job.json').read_bytes()
        original_job = copy.deepcopy(dict(job))
        original_fingerprint = recovery.fingerprint(job)

        def interrupted_export(argv, log, **kwargs):
            # Raise the real BaseException after resume invalidation and creation
            # of an export-only attempt. These leftovers must not be registered.
            Path(log).write_text('synthetic interrupted CPU export\n', encoding='utf8')
            output = Path(argv[argv.index('--output') + 1])
            output.write_text('partial synthetic BVH', encoding='utf8')
            raise error

        with patch.object(mw, 'run_logged', interrupted_export), self.assertRaises(type(error)) as caught:
            mw.extract(directory, job, resume_checkpoint=receipt, receipt_sha256=receipt_sha)
        if isinstance(error, SystemExit):
            self.assertEqual(caught.exception.code, error.code)
        self.assertEqual((directory / 'job.json').read_bytes(), original_bytes)
        self.assertEqual(dict(job), original_job)
        self.assertEqual(recovery.fingerprint(job), original_fingerprint)
        self.assertEqual(job['outputs'], original_job['outputs'])
        self.assertEqual(job['failure'], original_job['failure'])
        self.assertFalse((directory / '.operation.lock').exists())
        recovery.validate_receipt(mw, directory, job, receipt_data)

        # An actual second call must accept the same receipt and create a fresh
        # export-only attempt, not rerun inference or reuse the partial BVH.
        calls = []

        def complete_export(argv, log, **kwargs):
            self.assertNotIn('gem.inference', argv)
            calls.append(list(map(str, argv)))
            return self.export_stage(argv, log, **kwargs)

        with patch.object(mw, 'run_logged', complete_export):
            result = mw.extract(directory, job, resume_checkpoint=receipt, receipt_sha256=receipt_sha)
        self.assertEqual(result['state'], 'motion_ready')
        self.assertEqual(len(calls), 1)
        self.assertIsNone(job['failure'])
        self.assertFalse((directory / '.operation.lock').exists())
        self.assertEqual(len(list((directory / 'attempts').glob('export-recovery-*'))), 2)
        self.assertEqual(job['history'][-1]['export_recovery']['receipt']['sha256'], receipt_sha)

    def test_keyboard_interrupt_preserves_failed_snapshot_and_receipt_retry(self):
        self.assert_interrupted_resume_is_retryable(KeyboardInterrupt('synthetic Ctrl-C'))

    def test_system_exit_preserves_failed_snapshot_and_receipt_retry(self):
        self.assert_interrupted_resume_is_retryable(SystemExit(17))

    def test_unprotected_base_exceptions_keep_existing_write_semantics(self):
        mw = self.mw
        for error in (KeyboardInterrupt('unprotected'), SystemExit(19)):
            with self.subTest(exception=type(error).__name__):
                directory, job = mw.load_job(self.fixture.jobdir)
                original = (directory / 'job.json').read_bytes()
                with self.assertRaises(type(error)):
                    with mw.operation(directory, job, 'extract'):
                        job['ordinary_branch_marker'] = type(error).__name__
                        raise error
                self.assertNotEqual((directory / 'job.json').read_bytes(), original)
                self.assertEqual(mw.read_json(directory / 'job.json')['ordinary_branch_marker'],
                                 type(error).__name__)
                self.assertFalse((directory / '.operation.lock').exists())


if __name__ == '__main__':
    unittest.main(verbosity=2)
