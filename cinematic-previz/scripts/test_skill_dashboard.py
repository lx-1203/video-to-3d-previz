"""Behavior checks for the live skill viewer; local fixtures, no internet."""
import hashlib
import http.client
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest

try:
    import skill_dashboard as dashboard
except ImportError:
    dashboard = None


class DashboardTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(dashboard, 'The live dashboard has not been implemented')
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'installed'
        (self.root / 'references').mkdir(parents=True)
        (self.root / 'SKILL.md').write_text('---\nname: cinematic-previz\nmetadata:\n  version: "0.6.0"\n---\n# Demo\n\nCurrent rule.\n', encoding='utf-8')
        (self.root / 'CHANGELOG.md').write_text('# Updates\n\n## 0.6.0 — 2026-11-03 · New feature\n\n- Latest change\n\n## Older\n\n- Old change\n', encoding='utf-8')
        for name, body in {'core':'# Core\nBase rule.', 'timing':'# Timing\nRead aloud first.', 'camera':'# Camera\n\n## Public\nCamera rules.\n\n## Other\nNot selected.', 'focus':'# Focus\nFocus shift.'}.items():
            (self.root / 'references' / (name+'.md')).write_text(body, encoding='utf-8')
        self.catalog = {'version':1, 'root':'aigc', 'required':['core'], 'nodes':[
            {'id':'aigc','title':'Root','parent':None},
            {'id':'core','title':'Core','parent':'aigc','source':{'path':'references/core.md'}},
            {'id':'timing','title':'Timing','parent':'aigc','source':{'path':'references/timing.md'}},
            {'id':'camera','title':'Camera','parent':'aigc','source':{'path':'references/camera.md','sections':['## Public']}},
            {'id':'camera.focus','title':'Focus','parent':'camera','requires':['timing'],'source':{'path':'references/focus.md'}},
        ]}
        (self.root / 'module-index.json').write_text(json.dumps(self.catalog), encoding='utf-8')
        self.store = dashboard.DashboardStore(self.root)

    def register(self):
        self.source = Path(self.temp.name) / 'source'
        shutil.copytree(self.root, self.source)
        registered = dashboard.fingerprint(dashboard.file_hashes(self.root))
        (self.root / '.skill-source.json').write_text(json.dumps({'schema':1,'project_root':str(self.source),'source':str(self.source),'revision':registered,'files':dashboard.file_hashes(self.root)}), encoding='utf-8')
        return registered

    def test_dynamic_state_and_source_drift(self):
        registered = self.register()
        before = self.store.state()
        self.assertEqual(before['local']['status'], 'current')
        self.assertEqual(before['local']['installed_revision'], registered)
        (self.source / 'references/core.md').write_text('# Core\nChanged source', encoding='utf-8')
        after = self.store.state()
        self.assertEqual(after['local']['status'], 'needs_sync')
        self.assertIn('references/core.md', [f['path'] for f in after['local']['differences']])
        self.assertNotEqual(before['local']['source_revision'], after['local']['source_revision'])

    def test_standalone_and_stale_registration(self):
        self.assertEqual(self.store.state()['local']['status'], 'standalone')
        self.register()
        for root in (self.root, self.source):
            (root / 'references/core.md').write_text('# Core\nBoth changed', encoding='utf-8')
        self.assertEqual(self.store.state()['local']['status'], 'metadata_stale')

    def test_missing_registered_source_is_not_standalone(self):
        self.register()
        shutil.rmtree(self.source)
        self.assertEqual(self.store.state()['local']['status'], 'error')

    def test_malformed_registration_cannot_be_current(self):
        self.register()
        path = self.root / '.skill-source.json'
        valid = json.loads(path.read_text(encoding='utf-8'))
        for change in ({'schema':999}, {'files':[]}, {'files':{}}, {'project_root':None}, {'source':None}):
            with self.subTest(change=change):
                path.write_text(json.dumps(dict(valid, **change)), encoding='utf-8')
                self.assertEqual(self.store.state()['local']['status'], 'error')

    def test_route_reuses_actual_order_and_exact_sections(self):
        result = self.store.route(['camera.focus'])
        self.assertEqual([m['id'] for m in result['modules']], ['core','camera','timing','camera.focus'])
        self.assertEqual(result['modules'][0]['reason'], 'required')
        self.assertEqual(result['modules'][-1]['reason'], 'requested')
        self.assertIn('camera.focus', result['modules'][1]['required_by'])
        self.assertNotIn('Not selected', result['modules'][1]['content'])
        self.assertEqual(result['content_chars'], sum(len(m['content']) for m in result['modules']))
        with self.assertRaises(ValueError): self.store.route(['not-here'])

    def test_file_read_refresh_and_path_boundaries(self):
        doc = self.store.document('references/core.md')
        self.assertEqual(doc['sha256'], hashlib.sha256((self.root/'references/core.md').read_bytes()).hexdigest())
        (self.root/'references/core.md').write_text('# Updated', encoding='utf-8')
        self.assertEqual(self.store.document('references/core.md')['content'], '# Updated')
        for bad in ('../outside.md', '/SKILL.md', '.skill-source.json', 'references/../../SKILL.md', 'D:/secret.md'):
            with self.subTest(path=bad), self.assertRaises(ValueError): self.store.document(bad)

    def test_changelog_from_actual_file_and_unchecked_remote(self):
        state = self.store.state()
        self.assertEqual(state['remote']['status'], 'unchecked')
        self.assertEqual(state['changelog'][0]['date'], '2026-11-03')
        self.assertIn('Latest change', state['changelog'][0]['body'])
        self.assertEqual(state['version'], '0.6.0')

    def add_trigger_fixture(self):
        catalog = dict(self.catalog, nodes=self.catalog['nodes'] + [
            {'id':'runtime','title':'Maintenance','parent':'aigc','source':{'path':'references/core.md'}}])
        (self.root/'module-index.json').write_text(json.dumps(catalog),encoding='utf-8')
        (self.root/'references/reading-triggers.json').write_text(json.dumps({
            'version':1, 'tasks':{'maintenance':{'when':'When maintaining rules','action':'Read maintenance rules','gate':'Preserve local changes','source':'references/core.md'}},
            'conditions':[{'id':'focus','title':'Need focus transfer','when':'A shot changes focus','tasks':['maintenance'],
                           'modules':['camera.focus'],'source':'references/focus.md'}]}),encoding='utf-8')

    def test_trigger_plan_does_not_load_optional_modules_without_condition(self):
        self.add_trigger_fixture()
        result = self.store.plan('maintenance', [])
        self.assertEqual(result['requested'], ['runtime'])
        self.assertEqual([m['id'] for m in result['route']['modules']], ['core','runtime'])
        self.assertFalse(result['conditions'][0]['active'])
        self.assertEqual(result['task']['gate'], 'Preserve local changes')

    def test_trigger_plan_adds_real_dependencies_and_can_remove_condition(self):
        self.add_trigger_fixture()
        result = self.store.plan('maintenance', ['focus'])
        expected = self.store.route(['runtime','camera.focus'])
        self.assertEqual(result['route']['modules'], expected['modules'])
        self.assertTrue(result['conditions'][0]['active'])
        self.assertNotIn('camera.focus', [m['id'] for m in self.store.plan('maintenance', [])['route']['modules']])

    def test_trigger_plan_rejects_unknown_or_inapplicable_conditions(self):
        self.add_trigger_fixture()
        for task, conditions in [('unknown',[]),('maintenance',['unknown']),('camera',['focus'])]:
            with self.subTest(task=task), self.assertRaises(ValueError): self.store.plan(task,conditions)

    def git(self, *args):
        return subprocess.check_output(['git',*args],cwd=self.root,stderr=subprocess.PIPE)

    def test_remote_content_check_and_staleness(self):
        self.git('init','-b','main')
        self.git('config','core.autocrlf','false')
        self.git('config','user.name','Dashboard test')
        self.git('config','user.email','test@example.invalid')
        self.git('add','--all')
        self.git('commit','-m','fixture')
        self.git('remote','add','origin',str(self.root))
        verified = self.store.check_remote()
        self.assertEqual(verified['status'], 'verified')
        self.assertGreater(verified['files_checked'], 0)
        self.assertEqual(self.store.state()['remote']['status'], 'verified')
        (self.root/'references/core.md').write_text('# Modified', encoding='utf-8')
        self.assertEqual(self.store.state()['remote']['status'], 'stale')
        changed = self.store.check_remote()
        self.assertEqual(changed['status'], 'different')
        self.assertIn('references/core.md', [f['path'] for f in changed['differences']])
        self.assertFalse((self.root/'.skill-source.json').exists())

    def test_remote_failure_has_no_success_claim(self):
        result = self.store.check_remote()
        self.assertEqual(result['status'], 'error')
        self.assertTrue(result['error'])

    def test_http_routes_and_host_origin_guards(self):
        self.add_trigger_fixture()
        server = dashboard.make_server(self.store, 0)
        self.addCleanup(server.server_close)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.shutdown)
        port = server.server_address[1]
        def request(path, method='GET', headers=None):
            conn = http.client.HTTPConnection('127.0.0.1',port,timeout=5)
            conn.request(method,path,headers=headers or {})
            response = conn.getresponse()
            content = response.read()
            status = response.status
            conn.close()
            return status,content
        self.assertEqual(request('/api/state')[0],200)
        self.assertEqual(request('/api/route?modules=camera.focus')[0],200)
        status, payload = request('/api/plan?task=maintenance&triggers=focus')
        self.assertEqual(status,200)
        self.assertIn('camera.focus',json.loads(payload)['requested'])
        self.assertEqual(request('/api/plan?task=maintenance&triggers=unknown')[0],400)
        self.assertEqual(request('/api/document?path=..%2Foutside.md')[0],400)
        self.assertEqual(request('/api/state',headers={'Host':'evil.example'})[0],403)
        self.assertEqual(request('/api/remote-check','POST',{'Origin':'https://evil.example'})[0],403)
        self.assertEqual(request('/not-available')[0],404)
        self.assertEqual(request('/')[0],200)

    def test_listener_cannot_bind_an_existing_port(self):
        server = dashboard.make_server(self.store, 0)
        self.addCleanup(server.server_close)
        try:
            duplicate = dashboard.make_server(self.store, server.server_port)
        except OSError:
            return
        duplicate.server_close()
        self.fail('A second listener bound an already owned port')

    def test_cli_reuses_only_same_skill_listener(self):
        server = dashboard.make_server(self.store, 0)
        self.addCleanup(server.server_close)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.shutdown)
        completed = subprocess.run([sys.executable, str(Path(dashboard.__file__).resolve()),
                                    '--skill-dir', str(self.root), '--port', str(server.server_port)],
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=8, check=True)
        result = json.loads(completed.stdout)
        self.assertEqual(result['status'], 'reused')
        self.assertEqual(result['url'], f'http://127.0.0.1:{server.server_port}/')

    def test_cli_remembers_an_automatically_selected_port(self):
        server = dashboard.make_server(self.store, 0)
        self.addCleanup(server.server_close)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.shutdown)
        record = self.root / '.local/dashboard/server.json'
        record.parent.mkdir(parents=True)
        record.write_text(json.dumps({'root':str(self.root),'port':server.server_port}),encoding='utf-8')
        completed = subprocess.run([sys.executable,str(Path(dashboard.__file__).resolve()),'--skill-dir',str(self.root)],
                                   stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=8,check=True)
        result = json.loads(completed.stdout)
        self.assertEqual(result['status'],'reused')
        self.assertEqual(result['url'],f'http://127.0.0.1:{server.server_port}/')


if __name__ == '__main__':
    unittest.main()
