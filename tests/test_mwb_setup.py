import importlib.util
from pathlib import Path
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/motion/setup_backend.py'


class SetupTests(unittest.TestCase):
    def module(self):
        self.assertTrue(SCRIPT.exists(), 'Portable backend setup is not implemented')
        spec = importlib.util.spec_from_file_location('setup_backend', SCRIPT)
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        return m

    def test_plan_pins_source_and_writes_no_files(self):
        m = self.module()
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / 'new-computer'
            p = m.build_plan(root, 'uv', 'blender.exe', 'ffmpeg.exe')
            self.assertFalse(root.exists())
            self.assertEqual(p['source_commit'], '20555befe2fbbe5b2015e1fff7ff214dc18ea9d6')
            checkouts = [c['argv'] for c in p['commands'] if 'checkout' in c['argv']]
            self.assertEqual(len(checkouts), 1)
            self.assertIn(p['source_commit'], checkouts[0])
            for value in p['environment'].values():
                self.assertTrue(Path(value).is_relative_to(root))

    def test_existing_non_git_vendor_is_not_overwritten(self):
        m = self.module()
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            vendor = root/'vendor/GEM-X'
            vendor.mkdir(parents=True)
            (vendor/'important.txt').write_text('keep')
            with self.assertRaises(ValueError):
                m.build_plan(root, 'uv', 'blender.exe', 'ffmpeg.exe')
            self.assertEqual((vendor/'important.txt').read_text(), 'keep')


if __name__ == '__main__':
    unittest.main()
