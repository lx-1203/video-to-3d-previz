"""Actual child imports must not contaminate a protected installed tool copy."""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]


class MotionCacheLifecycleTests(unittest.TestCase):
    def test_normal_doctor_init_status_preserve_protected_copy(self):
        from test_content_audit import video
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            installed = root / 'installed/scripts/motion'
            installed.mkdir(parents=True)
            for name in ('motion_workbench.py', 'blender_bridge.py'):
                shutil.copy2(ROOT / 'scripts/motion' / name, installed / name)
            reference = root / 'reference.mp4'
            video(reference, [120] * 9)
            template, profile, yaml = root/'template.blend', root/'profile.json', root/'motion.yaml'
            template.write_bytes(b'only hashed by init; never loaded by Blender')
            profile.write_text('{}')
            yaml.write_text('test: true')
            config = root/'config.json'
            config.write_text(json.dumps({'schema_version': 1, 'blender': sys.executable,
                'ffmpeg': sys.executable, 'ffprobe': '', 'media_python': sys.executable,
                'backend': {'python': sys.executable, 'root': str(root),
                            'motion_config': str(yaml), 'soma_assets': str(root)}}))
            before = {p.name: p.read_bytes() for p in installed.iterdir()}
            env = os.environ.copy()
            env.pop('PYTHONDONTWRITEBYTECODE', None)
            cli = [sys.executable, str(installed/'motion_workbench.py')]
            commands = [['doctor', '--config', str(config)],
                ['init', '--job', str(root/'job'), '--video', str(reference),
                 '--template', str(template), '--profile', str(profile), '--config', str(config),
                 '--duration', '0.1'], ['status', '--job', str(root/'job')]]
            for command in commands:
                result = subprocess.run(cli + command, capture_output=True, encoding='utf-8', env=env)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertTrue(json.loads(result.stdout)['ok'])
            self.assertEqual({p.name: p.read_bytes() for p in installed.iterdir()}, before)

    def test_task_environment_prevents_real_sibling_import_bytecode(self):
        spec = importlib.util.spec_from_file_location('cache_lifecycle_mwb',
                                                      ROOT / 'scripts/motion/motion_workbench.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            installed = root / 'installed/scripts/motion'
            installed.mkdir(parents=True)
            bridge = installed / 'blender_bridge.py'
            shutil.copy2(ROOT / 'scripts/motion/blender_bridge.py', bridge)
            before = bridge.read_bytes()
            with patch.dict(os.environ, {}, clear=False):
                os.environ.pop('PYTHONDONTWRITEBYTECODE', None)
                environment = module.task_environment(root / 'job')
            code = ('import importlib.util,sys; '
                    's=importlib.util.spec_from_file_location("bridge_probe",sys.argv[1]); '
                    'm=importlib.util.module_from_spec(s);s.loader.exec_module(m)')
            process = subprocess.run([sys.executable, '-c', code, str(bridge)],
                                     capture_output=True, text=True, env=environment)
            self.assertEqual(process.returncode, 0, process.stderr)
            self.assertEqual(bridge.read_bytes(), before)
            self.assertEqual(list(installed.rglob('*.pyc')), [])


if __name__ == '__main__':
    unittest.main()
