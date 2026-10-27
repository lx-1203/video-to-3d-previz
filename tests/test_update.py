import importlib.util
import json
import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/check_update.py'
spec = importlib.util.spec_from_file_location('update_gate', SCRIPT)
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)


def release(version='1.2.0', note='one', updater='old updater'):
    files = {
        'SKILL.md': f'---\nname: video-to-3d-previz\ndescription: Test skill\nmetadata:\n  version: "{version}"\n---\n\n[Guide](references/guide.md)\n'.encode(),
        'agents/openai.yaml': b'interface:\n  display_name: "Test"\n',
        'references/guide.md': note.encode(),
        'scripts/check_update.py': updater.encode(),
        'update-source.json': json.dumps({'repository': 'lx-1203/video-to-3d-previz', 'branch': 'main'}).encode(),
    }
    manifest = {'schema_version': 1, 'skill_name': 'video-to-3d-previz', 'version': version,
                'files': {p: {'sha256': hashlib.sha256(b).hexdigest(), 'size': len(b)} for p, b in files.items()}}
    files['release-manifest.json'] = json.dumps(manifest).encode()
    return files


class Source:
    def __init__(self, files, revision='a' * 40):
        self.files = files
        self.rev = revision
        self.checks = 0
        self.offline = False

    def revision(self):
        self.checks += 1
        if self.offline:
            raise gate.GateError('NETWORK_ERROR', 'offline')
        return self.rev

    def file(self, revision, path):
        assert revision == self.rev
        return self.files[path]


class UpdateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.root = self.base / 'skills/video-to-3d-previz'
        self.root.mkdir(parents=True)
        self.state = self.base / 'state'
        self.source = Source(release())
        for path, data in self.source.files.items():
            p = self.root / path
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(data)

    def tearDown(self):
        self.tmp.cleanup()

    def run_gate(self):
        return gate.check_and_update(self.root, source=self.source, state_base=self.state)

    def initialized(self):
        self.assertEqual(self.run_gate()['status'], 'current')

    def next_release(self, **kwargs):
        self.source.files = release(**kwargs)
        self.source.rev = 'b' * 40

    def blocked(self, code):
        with self.assertRaises(gate.GateError) as ctx:
            self.run_gate()
        self.assertEqual(ctx.exception.code, code)

    def test_current_checks_network_on_every_invocation(self):
        self.initialized()
        self.initialized()
        self.assertEqual(self.source.checks, 2)

    def test_new_commit_updates_and_preserves_backup(self):
        self.initialized()
        self.next_release(version='1.3.0', note='two')
        result = self.run_gate()
        self.assertEqual(result['status'], 'updated')
        self.assertEqual((self.root / 'references/guide.md').read_text(), 'two')
        self.assertEqual((Path(result['backup']) / 'references/guide.md').read_text(), 'one')
        self.assertEqual(self.run_gate()['status'], 'current')

    def test_no_offline_fallback_after_success(self):
        self.initialized()
        self.source.offline = True
        self.blocked('NETWORK_ERROR')

    def test_local_edit_preserved(self):
        self.initialized()
        p = self.root / 'references/guide.md'
        p.write_text('local edit')
        self.next_release(note='remote edit')
        self.blocked('LOCAL_MODIFIED')
        self.assertEqual(p.read_text(), 'local edit')

    def test_extra_file_preserved(self):
        self.initialized()
        p = self.root / 'my-notes.txt'
        p.write_text('keep me')
        self.blocked('LOCAL_EXTRA_FILES')
        self.assertEqual(p.read_text(), 'keep me')

    def test_missing_file_blocks(self):
        self.initialized()
        (self.root / 'references/guide.md').unlink()
        self.blocked('LOCAL_MODIFIED')

    def test_changed_manifest_cannot_hide_local_edits(self):
        self.initialized()
        for path, data in release(note='altered with matching digest').items():
            (self.root / path).write_bytes(data)
        self.blocked('LOCAL_MANIFEST_CHANGED')

    def test_remote_hash_failure_keeps_install(self):
        self.initialized()
        self.next_release()
        self.source.files['references/guide.md'] = b'corrupt'
        self.blocked('REMOTE_CHECKSUM_FAILED')
        self.assertEqual((self.root / 'references/guide.md').read_text(), 'one')

    def test_manifest_path_traversal_rejected(self):
        self.initialized()
        self.next_release()
        m = json.loads(self.source.files['release-manifest.json'])
        m['files']['../escape.py'] = {'sha256': 'a' * 64, 'size': 1}
        self.source.files['release-manifest.json'] = json.dumps(m).encode()
        self.blocked('INVALID_MANIFEST')
        self.assertFalse((self.root.parent / 'escape.py').exists())

    def test_case_collision_rejected(self):
        self.initialized()
        self.next_release()
        m = json.loads(self.source.files['release-manifest.json'])
        m['files']['references/Guide.md'] = m['files']['references/guide.md']
        self.source.files['release-manifest.json'] = json.dumps(m).encode()
        self.blocked('INVALID_MANIFEST')

    def test_remote_redirect_to_other_repository_rejected(self):
        self.initialized()
        self.next_release()
        files = self.source.files
        files['update-source.json'] = b'{"repository":"other/project","branch":"main"}'
        m = json.loads(files['release-manifest.json'])
        m['files']['update-source.json'] = {'sha256': hashlib.sha256(files['update-source.json']).hexdigest(), 'size': len(files['update-source.json'])}
        files['release-manifest.json'] = json.dumps(m).encode()
        self.blocked('REMOTE_SOURCE_CHANGED')

    def test_downgrade_blocked(self):
        self.initialized()
        self.next_release(version='1.1.0')
        self.blocked('REMOTE_DOWNGRADE')

    def test_unknown_initial_package_not_overwritten(self):
        self.next_release(note='different initial release')
        self.blocked('UNREGISTERED_LOCAL')
        self.assertEqual((self.root / 'references/guide.md').read_text(), 'one')

    def test_live_lock_not_removed(self):
        self.state.mkdir()
        lock = self.state / 'update.lock'
        lock.write_text('another process')
        self.blocked('LOCKED')
        self.assertEqual(lock.read_text(), 'another process')

    def test_updater_change_requires_second_check(self):
        self.initialized()
        self.next_release(updater='new updater')
        result = self.run_gate()
        self.assertTrue(result['updater_changed'])
        self.assertTrue(result['rerun_required'])
        self.assertFalse(self.run_gate()['rerun_required'])

    def test_install_failure_rolls_back(self):
        self.initialized()
        self.next_release(note='new')
        original = gate.os.replace
        failed = False

        def replace(src, dst):
            nonlocal failed
            if Path(src).name == 'stage' and Path(dst) == self.root and not failed:
                failed = True
                raise OSError('simulated rename failure')
            return original(src, dst)

        with patch.object(gate.os, 'replace', side_effect=replace):
            self.blocked('INSTALL_FAILED')
        self.assertEqual((self.root / 'references/guide.md').read_text(), 'one')
        self.assertEqual(self.run_gate()['status'], 'updated')

    def test_state_write_failure_rolls_back(self):
        self.initialized()
        self.next_release(note='new')
        with patch.object(gate, 'atomic_json', side_effect=OSError('state not writable')):
            self.blocked('INSTALL_FAILED')
        self.assertEqual((self.root / 'references/guide.md').read_text(), 'one')
        self.assertEqual(self.run_gate()['status'], 'updated')

    def test_invalid_remote_version_does_not_replace_files(self):
        self.initialized()
        self.next_release()
        files = self.source.files
        files['SKILL.md'] = files['SKILL.md'].replace(b'1.2.0', b'9.9.9')
        m = json.loads(files['release-manifest.json'])
        m['files']['SKILL.md'] = {'sha256': hashlib.sha256(files['SKILL.md']).hexdigest(), 'size': len(files['SKILL.md'])}
        files['release-manifest.json'] = json.dumps(m).encode()
        self.blocked('INVALID_MANIFEST')

    def test_remote_network_failure_during_download_preserves_install(self):
        self.initialized()
        self.next_release(note='new')
        original = self.source.file

        def fetch(revision, path):
            if path == 'references/guide.md':
                raise gate.GateError('NETWORK_ERROR', 'download failed')
            return original(revision, path)

        with patch.object(self.source, 'file', side_effect=fetch):
            self.blocked('NETWORK_ERROR')
        self.assertEqual((self.root / 'references/guide.md').read_text(), 'one')

    def test_changed_local_file_during_download_is_preserved(self):
        self.initialized()
        self.next_release(note='new')
        original = self.source.file

        def fetch(revision, path):
            result = original(revision, path)
            if path == 'references/guide.md':
                (self.root / path).write_text('concurrent user edit')
            return result

        with patch.object(self.source, 'file', side_effect=fetch):
            self.blocked('LOCAL_MODIFIED')
        self.assertEqual((self.root / 'references/guide.md').read_text(), 'concurrent user edit')

    def test_cli_failure_has_nonzero_exit_and_blocked_json(self):
        import subprocess
        import sys
        r = subprocess.run([sys.executable, str(SCRIPT), '--skill-dir', str(self.base / 'not-installed')], capture_output=True, text=True)
        self.assertEqual(r.returncode, 2)
        self.assertEqual(json.loads(r.stdout)['status'], 'blocked')

    def test_empty_state_is_not_treated_as_first_install(self):
        self.initialized()
        (self.state / 'state.json').write_text('{}')
        self.blocked('INVALID_STATE')


if __name__ == '__main__':
    unittest.main()
