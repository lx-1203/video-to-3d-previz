from pathlib import Path
import tempfile
import unittest
from orderflow import core, media
import test_core
import os
import subprocess
from unittest import mock


class MediaTests(unittest.TestCase):
    setUp = test_core.CoreTests.setUp
    tearDown = test_core.CoreTests.tearDown
    def test_environment_rejects_missing_media_import_before_ffmpeg(self):
        with mock.patch('importlib.import_module', side_effect=ImportError('missing av')):
            with self.assertRaisesRegex(ImportError, 'missing av'):
                media.check_environment(self.spec, self.job)

    def test_environment_rejects_non_ffmpeg_executable(self):
        import sys
        spec = dict(self.spec, ffmpeg=sys.executable)
        with mock.patch('importlib.import_module'):
            with self.assertRaises(RuntimeError):
                media.check_environment(spec, self.job)

    def test_partial_interval_and_nonzero_source_mapping(self):
        self.assertAlmostEqual(media.source_offset(self.spec), 0.2 + 1/30)
        self.assertAlmostEqual(media.render_duration(self.spec), 3/30)

    def test_failed_subprocess_cannot_complete(self):
        import sys
        core.write_status(self.job, 'encoding')
        with self.assertRaises(RuntimeError):
            media.run_process([sys.executable,'-c','raise SystemExit(7)'], self.job/'failure.log', self.job)
        self.assertNotEqual(core.read_status(self.job)['state'], 'complete')

    def test_child_cancel_exit_is_preserved_by_outer_worker(self):
        import sys
        from orderflow import worker
        command = "from pathlib import Path; import sys; from orderflow import core; job=Path(sys.argv[1]); (job/'cancel.request').touch(); core.write_status(job,'cancelled'); raise SystemExit(2)"
        caught = []
        def run(_):
            try:
                media.run_process([sys.executable, '-c', command, str(self.job)], self.job/'cancel.log', self.job)
            except Exception as exc:
                caught.append(type(exc))
                raise
        with mock.patch.object(worker, 'run_job', side_effect=run):
            self.assertEqual(worker.main(['--job', str(self.job/'job.json')]), 2)
        self.assertEqual(caught, [media.Cancelled])
        self.assertEqual(core.read_status(self.job)['state'], 'cancelled')

    def test_stale_source_and_frame_rejected(self):
        core.write_job(self.job,self.spec)
        spec=core.load_job(self.job/'job.json')
        (self.root/'reference.mp4').write_bytes(b'changed')
        with self.assertRaises(ValueError): media.validate_inputs(spec, self.job)
        (self.root/'reference.mp4').write_bytes(b'reference.mp4')
        self.assertTrue(media.validate_inputs(spec,self.job))
        frames=self.job/'frames'
        frames.mkdir()
        test_core.png(frames/'frame_000002.png')
        record=core.inspect_png_frames(frames,2,2,2,2)
        test_core.png(frames/'frame_000002.png',3,2)
        with self.assertRaises(ValueError): media.validate_frame_records(record, frames, 2,2,2,2)


@unittest.skipUnless(os.environ.get('ORDERFLOW_FFMPEG'), 'Set ORDERFLOW_FFMPEG for real media integration')
class RealMediaTests(unittest.TestCase):
    def test_real_nonzero_interval_audio_tail_and_zip(self):
        import av
        import numpy as np
        from PIL import Image
        import wave
        import sys
        import zipfile
        ffmpeg=os.environ['ORDERFLOW_FFMPEG']
        with tempfile.TemporaryDirectory(prefix='orderflow_media_') as folder:
            root=Path(folder)
            rate=48000
            times=np.arange(round(1.07*rate))/rate
            waveform=(np.sin(2*np.pi*np.where(times<0.5,440,880)*times)*10000).astype('<i2')
            with wave.open(str(root/'audio.wav'),'wb') as output:
                output.setnchannels(2); output.setsampwidth(2); output.setframerate(rate)
                output.writeframes(np.column_stack((waveform,waveform)).tobytes())
            subprocess.run([ffmpeg,'-hide_banner','-loglevel','error','-n','-f','lavfi','-i','testsrc2=size=96x64:rate=10:duration=1','-i',str(root/'audio.wav'),'-c:v','libx264','-pix_fmt','yuv420p','-c:a','aac',str(root/'source.mp4')],check=True)
            (root/'source.blend').write_bytes(b'synthetic')
            for frame_end, tail, expected_audio in ((3,False,0.3),(5,True,0.57)):
                job=core.new_job_dir(root/'jobs','media')
                (job/'snapshot.blend').write_bytes(b'snapshot')
                spec=dict(schema_version=1,name='media',source_blend=str(root/'source.blend'),snapshot_blend=str(job/'snapshot.blend'),reference_video=str(root/'source.mp4'),output_root=str(root/'jobs'),frame_start=1,frame_end=frame_end,scene_frame_start=1,fps_num=10,fps_den=1,source_start_seconds=0.5,mode='preview',camera='Camera',ffmpeg=ffmpeg,media_python=sys.executable,model_standard='')
                job_path=core.write_job(job,spec)
                (job/'scene.blend').write_bytes(b'saved scene')
                (job/'frames').mkdir()
                for frame in range(1,frame_end+1): Image.new('RGB',(1280,720),(20,100,160)).save(job/'frames'/f'frame_{frame:06d}.png')
                records=core.inspect_png_frames(job/'frames',1,frame_end,1280,720)
                core.atomic_json(job/'render_manifest.json',dict(scene_sha256=core.sha256(job/'scene.blend'),frames=records,dependencies=[]))
                report=media.encode(job_path)
                self.assertTrue(report['technical_passed'])
                self.assertEqual(report['visual_status'],'unverified')
                self.assertEqual(report['preserved_audio_tail'],tail)
                self.assertAlmostEqual(report['audio_duration_seconds'],expected_audio,delta=0.04)
                with av.open(str(job/'white.mp4')) as container:
                    audio=np.concatenate([frame.to_ndarray().reshape(2,-1)[0] for frame in container.decode(audio=0)])
                frequencies=np.fft.rfftfreq(len(audio),1/rate)
                frequency=frequencies[np.abs(np.fft.rfft(audio)).argmax()]
                self.assertAlmostEqual(frequency,880,delta=8)
                self.assertIn('unknown', (job/'usage.txt').read_text())
                self.assertNotIn('4.2 or later', (job/'usage.txt').read_text())
                self.assertTrue((job/'usage.txt').read_text().startswith('OrderFlow rendered scene\n'))
                self.assertEqual(core.read_status(job)['delivery_status'], 'internal_candidate_pending_overlay')
                with self.assertRaisesRegex(ValueError, '4K'):
                    core.package_submission(job)
                (job/'white.mp4').write_bytes(b'changed')
                with self.assertRaises(ValueError): core.package_submission(job)

    def test_real_reference_rejected_before_worker_opens_snapshot(self):
        import sys
        from types import SimpleNamespace
        from orderflow import worker
        ffmpeg = os.environ['ORDERFLOW_FFMPEG']
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            silent = root/'silent.mp4'
            voiced = root/'voiced.mp4'
            subprocess.run([ffmpeg,'-hide_banner','-loglevel','error','-f','lavfi','-i','color=size=64x64:rate=10:duration=0.5','-c:v','libx264',str(silent)],check=True)
            subprocess.run([ffmpeg,'-hide_banner','-loglevel','error','-i',str(silent),'-f','lavfi','-i','sine=duration=0.5','-c:v','copy','-c:a','aac',str(voiced)],check=True)
            (root/'source.blend').write_bytes(b'synthetic')
            for video, end in ((silent, 3), (voiced, 20)):
                job = core.new_job_dir(root/'jobs','negative')
                (job/'snapshot.blend').write_bytes(b'snapshot')
                spec = dict(schema_version=1,name='negative',source_blend=str(root/'source.blend'),snapshot_blend=str(job/'snapshot.blend'),reference_video=str(video),output_root=str(root/'jobs'),frame_start=1,frame_end=end,scene_frame_start=1,fps_num=10,fps_den=1,source_start_seconds=0,mode='preview',camera='Camera',ffmpeg=ffmpeg,media_python=sys.executable)
                path = core.write_job(job,spec)
                bpy = SimpleNamespace(ops=mock.Mock())
                with mock.patch.dict(sys.modules, {'bpy': bpy}):
                    with self.assertRaises(RuntimeError):
                        worker.run_job(path)
                bpy.ops.wm.open_mainfile.assert_not_called()
                self.assertFalse((job/'frames').exists())


if __name__ == '__main__': unittest.main()
