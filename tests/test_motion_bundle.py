"""Exercise installed motion tools through the release gate, without a companion skill."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from test_update import Source, gate, release

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ('motion_workbench.py', 'blender_bridge.py', 'export_gem_bvh.py',
         'download_backend_assets.py', 'setup_backend.py', 'mediapipe_motion.py',
         'motion_preview.py')


class MotionBundleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        subprocess.run([sys.executable, '-B', str(ROOT / 'scripts/build_manifest.py')],
                       check=True, capture_output=True)
        cls.manifest = json.loads((ROOT / 'release-manifest.json').read_bytes())

    def test_manifest_includes_executable_motion_module(self):
        expected = {'scripts/motion/' + name for name in TOOLS}
        expected |= {'references/motion-workflow.md', 'references/motion/protocol.md',
                     'references/motion/order-workflow.md', 'references/motion/lightweight.md',
                     'references/motion/notices.md', 'references/order-toolkit.md',
                     'references/skeleton-overlay-contract.md', 'scripts/validate_skeleton_overlay.py',
                     'scripts/portable/build_bundle.py', 'scripts/portable/install_bundle.py'}
        self.assertTrue(expected.issubset(self.manifest['files']),
                        sorted(expected - set(self.manifest['files'])))
        self.assertFalse(any(p.endswith('SKILL.md') and p != 'SKILL.md'
                             for p in self.manifest['files']))

    def test_gate_upgrade_installs_tools_and_cli_leaves_install_clean(self):
        files = {name: (ROOT / name).read_bytes() for name in self.manifest['files']}
        files['release-manifest.json'] = (ROOT / 'release-manifest.json').read_bytes()
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            installed = base / 'skills/video-to-3d-previz'
            installed.mkdir(parents=True)
            old = Source(release())
            for name, data in old.files.items():
                target = installed / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
            state = base / 'state'
            gate.check_and_update(installed, source=old, state_base=state)
            source = Source(files, revision='b' * 40)
            result = gate.check_and_update(installed, source=source, state_base=state)
            self.assertEqual(result['status'], 'updated')
            cli = installed / 'scripts/motion/motion_workbench.py'
            proc = subprocess.run([sys.executable, str(cli), 'status', '--job', str(base / 'absent')],
                                  capture_output=True, text=True, encoding='utf-8')
            self.assertNotEqual(proc.returncode, 0)
            self.assertFalse(json.loads(proc.stdout)['ok'])
            for name in ('motion_workbench.py', 'setup_backend.py', 'mediapipe_motion.py'):
                proc = subprocess.run([sys.executable, str(cli.with_name(name)), '--help'],
                                      capture_output=True, text=True, encoding='utf-8')
                self.assertEqual(proc.returncode, 0, proc.stderr)
            for name in ('build_bundle.py', 'install_bundle.py'):
                proc = subprocess.run([sys.executable, str(installed / 'scripts/portable' / name), '--help'],
                                      capture_output=True, text=True, encoding='utf-8')
                self.assertEqual(proc.returncode, 0, proc.stderr)
            # Normal tool use must not create unmanaged __pycache__ in the protected package.
            self.assertEqual(gate.check_and_update(installed, source=source, state_base=state)['status'],
                             'current')


if __name__ == '__main__':
    unittest.main()
