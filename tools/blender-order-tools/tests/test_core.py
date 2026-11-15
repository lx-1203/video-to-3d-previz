import hashlib
import json
from pathlib import Path
import struct
import tempfile
import unittest
import zlib
from unittest import mock

from orderflow import core


def png(path, width=2, height=2):
    def chunk(kind, data):
        return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data))
    path.write_bytes(b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 2, 0, 0, 0)) + chunk(b'IDAT', zlib.compress((b'\0' + b'\x80' * width * 3) * height)) + chunk(b'IEND', b''))


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        for name in ('source.blend', 'reference.mp4', 'ffmpeg', 'python'):
            (self.root / name).write_bytes(name.encode())
        self.job = core.new_job_dir(self.root / 'outputs', 'test')
        (self.job / 'snapshot.blend').write_bytes(b'snapshot')
        self.spec = dict(schema_version=1, name='test', source_blend=str(self.root/'source.blend'), snapshot_blend=str(self.job/'snapshot.blend'), reference_video=str(self.root/'reference.mp4'), output_root=str(self.root/'outputs'), frame_start=2, frame_end=4, scene_frame_start=1, fps_num=30, fps_den=1, source_start_seconds=0.2, mode='preview', camera='Camera', ffmpeg=str(self.root/'ffmpeg'), media_python=str(self.root/'python'), model_standard='')

    def tearDown(self):
        self.temp.cleanup()

    def test_invalid_specs_are_rejected(self):
        for changes in ({'frame_end': 1}, {'fps_num': 0}, {'source_start_seconds': -1}, {'mode':'bad'}, {'camera':''}, {'source_blend':str(self.root/'missing')}, {'frame_start':0}, {'snapshot_blend':self.spec['source_blend']}, {'name':'../escape'}, {'schema_version':2}, {'fps_num':True}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                core.validate_spec(dict(self.spec, **changes))

    def test_jobs_unique_and_no_traversal(self):
        self.assertNotEqual(self.job, core.new_job_dir(self.root/'outputs','test'))
        for name in ('../escape', '/absolute', 'a/b', 'a\\b', '..'):
            with self.assertRaises(ValueError):
                core.new_job_dir(self.root/'outputs', name)

    def test_write_freezes_inputs_and_refuses_overwrite_escape(self):
        job_path = core.write_job(self.job, self.spec)
        saved = core.load_job(job_path)
        self.assertEqual(saved['input_sha256']['reference_video'], hashlib.sha256(b'reference.mp4').hexdigest())
        with self.assertRaises(FileExistsError):
            core.write_job(self.job, self.spec)
        outside = self.root/'outside'
        outside.mkdir()
        with self.assertRaises(ValueError):
            core.write_job(outside, self.spec)

    def test_missing_corrupt_wrong_dimensions_pngs(self):
        frames = self.job/'frames'
        frames.mkdir()
        with self.assertRaises(ValueError):
            core.inspect_png_frames(frames,1,1,2,2)
        png(frames/'frame_000001.png', 3,2)
        with self.assertRaises(ValueError):
            core.inspect_png_frames(frames,1,1,2,2)
        png(frames/'frame_000001.png')
        self.assertEqual(core.inspect_png_frames(frames,1,1,2,2)[0]['frame'],1)
        (frames/'frame_000001.png').write_bytes(b'bad')
        with self.assertRaises(ValueError):
            core.inspect_png_frames(frames,1,1,2,2)

    def test_extra_frame_is_not_part_of_frozen_sequence(self):
        frames=self.job/'frames'
        frames.mkdir()
        png(frames/'frame_000001.png')
        png(frames/'frame_000002.png')
        with self.assertRaises(ValueError):
            core.inspect_png_frames(frames,1,1,2,2)

    def test_package_rejects_traversal_even_with_correct_hash(self):
        self.make_delivery()
        (self.job/'unexpected.txt').write_text('private')
        manifest=core.read_json(self.job/'technical_manifest.json')
        manifest['members']['dependencies/../unexpected.txt']=core.sha256(self.job/'unexpected.txt')
        (self.job/'dependencies').mkdir()
        core.atomic_json(self.job/'technical_manifest.json',manifest)
        with self.assertRaises(ValueError): core.package_submission(self.job)
        self.assertEqual(list(self.job.glob('*.zip')), [])

    def make_delivery(self):
        core.write_job(self.job, dict(self.spec, mode='final'))
        for name in ('scene.blend','white.mp4','comparison.mp4','usage.txt'):
            (self.job/name).write_bytes(name.encode())
        core.atomic_json(self.job/'verification.json',dict(technical_passed=True,mode='final',native_render_dimensions=[3840,2160],visual_status='unverified',file_sha256={'scene.blend':core.sha256(self.job/'scene.blend')}))
        members={name:core.sha256(self.job/name) for name in ('scene.blend','white.mp4','comparison.mp4','usage.txt','verification.json')}
        core.atomic_json(self.job/'technical_manifest.json',dict(technical_passed=True,mode='final',native_render_dimensions=[3840,2160],visual_status='unverified',members=members))
        core.write_status(self.job,'complete')

    def test_status_rejects_unknown_and_worker_is_safe_argv(self):
        with self.assertRaises(ValueError): core.write_status(self.job, 'passed')
        core.write_status(self.job,'failed',error='actual failure')
        self.assertEqual(core.read_status(self.job)['state'],'failed')
        argv=core.blender_worker_command('blender',self.job/'job.json')
        self.assertIn('--disable-autoexec', argv)
        self.assertEqual(argv[-2:],['--job',str(self.job/'job.json')])

    def test_package_requires_technical_complete(self):
        core.write_status(self.job,'failed')
        with self.assertRaises(ValueError): core.package_submission(self.job)

    def test_customer_zip_excludes_internal_evidence(self):
        import zipfile
        self.make_delivery()
        with mock.patch('orderflow.acceptance.validate_for_package', return_value={}), mock.patch('orderflow.acceptance.assert_unchanged'):
            archive = core.package_submission(self.job, acceptance={'test_only': True})
        with zipfile.ZipFile(archive) as zipped:
            self.assertEqual(set(zipped.namelist()), {'scene.blend', 'white.mp4', 'comparison.mp4', 'usage.txt'})

    def test_customer_zip_requires_overlay_acceptance_even_when_technical_final_passed(self):
        self.make_delivery()
        with self.assertRaisesRegex(ValueError, '骨骼|overlay'):
            core.package_submission(self.job)
        self.assertEqual(list(self.job.glob('*.zip')), [])

    def test_cleanup_never_deletes_a_preexisting_colliding_archive(self):
        from types import SimpleNamespace
        self.make_delivery()
        destination=self.job/(self.job.name+'_12345678.zip')
        destination.write_bytes(b'existing archive must remain untouched')
        before=core.sha256(destination)
        with mock.patch('orderflow.acceptance.validate_for_package', return_value={}), mock.patch('orderflow.acceptance.assert_unchanged'), mock.patch('orderflow.core.uuid.uuid4', return_value=SimpleNamespace(hex='12345678')):
            with self.assertRaises(FileExistsError):
                core.package_submission(self.job, acceptance={'test_only':True})
        self.assertEqual(core.sha256(destination), before)

    def test_customer_zip_rejects_preview_or_inconsistent_native_geometry(self):
        self.make_delivery()
        path = self.job/'job.json'
        job = core.read_json(path)
        job['mode'] = 'preview'
        core.atomic_json(path, job)
        with self.assertRaisesRegex(ValueError, '4K'):
            core.package_submission(self.job)
        job['mode'] = 'final'
        core.atomic_json(path, job)
        manifest = core.read_json(self.job/'technical_manifest.json')
        manifest['native_render_dimensions'] = [1280,720]
        core.atomic_json(self.job/'technical_manifest.json', manifest)
        with self.assertRaises(ValueError):
            core.package_submission(self.job)


if __name__ == '__main__': unittest.main()
