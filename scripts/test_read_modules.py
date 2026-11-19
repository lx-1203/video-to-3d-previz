"""Read-only module loader behavior tests using real files and subprocesses."""
import json
import pathlib
import subprocess
import sys
import tempfile
import unittest

SCRIPT = pathlib.Path(__file__).with_name('read_modules.py')
ROOT = SCRIPT.parent.parent

class ModuleReaderTests(unittest.TestCase):
    def invoke(self, *args, root=None):
        script = SCRIPT
        if root is not None:
            script = root / 'scripts' / SCRIPT.name
            script.parent.mkdir(exist_ok=True)
            if SCRIPT.exists():
                script.write_bytes(SCRIPT.read_bytes())
        return subprocess.run([sys.executable, '-X', 'utf8', str(script), *args],
                              capture_output=True, text=True, encoding='utf-8', cwd=tempfile.gettempdir())

    def fixture(self, root):
        (root / 'references').mkdir()
        (root / 'references/base.md').write_text('# Base\nShared constraint\n', encoding='utf-8')
        (root / 'references/camera.md').write_text(
            '# Camera\n## Common\nCamera constraint\n## Orbit\nCircle path\n'
            '\x60\x60\x60text\n## Not a heading\n\x60\x60\x60\nEnd orbit\n'
            '## Whip\nFast pan\n', encoding='utf-8')
        (root / 'references/light.md').write_text('# Light\nFixed world light\n', encoding='utf-8')
        catalog = {'version': 1, 'root': 'aigc', 'required': ['core'], 'nodes': [
            {'id': 'aigc', 'title': 'Root', 'parent': None},
            {'id': 'core', 'title': 'Core', 'parent': 'aigc', 'source': {'path': 'references/base.md'}},
            {'id': 'direction', 'title': 'Direction', 'parent': 'aigc'},
            {'id': 'camera', 'title': 'Camera', 'parent': 'direction',
             'source': {'path': 'references/camera.md', 'sections': ['## Common']}},
            {'id': 'camera.orbit', 'title': 'Orbit', 'parent': 'camera',
             'source': {'path': 'references/camera.md', 'sections': ['## Orbit']}, 'requires': ['lighting']},
            {'id': 'camera.whip', 'title': 'Whip', 'parent': 'camera',
             'source': {'path': 'references/camera.md', 'sections': ['## Whip']}},
            {'id': 'lighting', 'title': 'Light', 'parent': 'direction',
             'source': {'path': 'references/light.md'}}]}
        self.write_catalog(root, catalog)
        return catalog

    def write_catalog(self, root, catalog):
        (root / 'module-index.json').write_text(json.dumps(catalog), encoding='utf-8')

    def test_real_tree_lists_camera_leaves_without_loading_rule_bodies(self):
        out = self.invoke('tree', '--json')
        self.assertEqual(out.returncode, 0, out.stderr)
        data = json.loads(out.stdout)
        self.assertIn('camera.orbit', [n['id'] for n in data['nodes']])
        self.assertNotIn('content', out.stdout)

    def test_multi_branch_selection_includes_parent_and_dependencies_once(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td); self.fixture(root)
            out = self.invoke('read', 'camera.orbit', 'lighting', 'camera.orbit', '--json', root=root)
            self.assertEqual(out.returncode, 0, out.stderr)
            data = json.loads(out.stdout)
            self.assertEqual([n['id'] for n in data['modules']], ['core', 'camera', 'lighting', 'camera.orbit'])
            self.assertEqual(data['requested'], ['camera.orbit', 'lighting'])
            text = '\n'.join(n['content'] for n in data['modules'])
            self.assertNotIn('Fast pan', text)
            self.assertIn('## Not a heading', text)
            self.assertIn('End orbit', text)
            self.assertEqual(data['content_chars'], sum(len(n['content']) for n in data['modules']))

    def test_source_fingerprint_matches_original_file(self):
        import hashlib
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td); self.fixture(root)
            out = self.invoke('read', 'camera.orbit', '--json', root=root)
            self.assertEqual(out.returncode, 0, out.stderr)
            node = next(n for n in json.loads(out.stdout)['modules'] if n['id'] == 'camera.orbit')
            self.assertEqual(node['source_sha256'], hashlib.sha256((root/'references/camera.md').read_bytes()).hexdigest())

    def test_budget_failure_never_returns_partial_rules(self):
        out = self.invoke('read', 'camera.orbit', '--max-chars', '1', '--json')
        self.assertEqual(out.returncode, 2)
        self.assertEqual(out.stdout, '')
        self.assertIn('budget', out.stderr)

    def test_unknown_module_is_actionable_and_has_no_partial_output(self):
        out = self.invoke('read', 'camera.does-not-exist', '--json')
        self.assertEqual(out.returncode, 2)
        self.assertEqual(out.stdout, '')
        self.assertIn('camera.does-not-exist', out.stderr)

    def test_group_does_not_implicitly_expand_all_children(self):
        out = self.invoke('read', 'direction', '--json')
        self.assertEqual(out.returncode, 2)
        self.assertIn('children', out.stderr)

    def test_storyboard_route_requires_all_delivery_facets(self):
        out = self.invoke('read', 'storyboard', '--json')
        self.assertEqual(out.returncode, 0, out.stderr)
        ids = {n['id'] for n in json.loads(out.stdout)['modules']}
        self.assertTrue({'core', 'storyboard', 'style', 'camera', 'transitions', 'lighting', 'performance',
                         'previs.start', 'prompts', 'continuity', 'delivery'}.issubset(ids), ids)
        self.assertNotIn('platform.tools', ids)

    def test_tool_task_does_not_pull_filmmaking_delivery_branch(self):
        out = self.invoke('read', 'platform.upload', '--json')
        self.assertEqual(out.returncode, 0, out.stderr)
        ids = {n['id'] for n in json.loads(out.stdout)['modules']}
        self.assertNotIn('storyboard', ids)
        self.assertNotIn('performance', ids)
        self.assertIn('core', ids)

    def test_character_assets_route_includes_full_reference_and_review_rules(self):
        out = self.invoke('read', 'character-assets', '--json')
        self.assertEqual(out.returncode, 0, out.stderr)
        data = json.loads(out.stdout)
        ids = {n['id'] for n in data['modules']}
        self.assertEqual(ids, {'core', 'character-assets'})
        body = next(n['content'] for n in data['modules'] if n['id'] == 'character-assets')
        self.assertIn('## 先收集两张职责明确的参考图', body)
        self.assertIn('图1作为唯一面部参考图', body)
        self.assertIn('图2作为唯一服装搭配参考图', body)
        self.assertIn('2px宽、#E2E2E2', body)
        self.assertIn('## 批量更新与实际审图', body)

    def test_first_frame_and_storyboard_load_character_expression_and_execution_rules(self):
        for route in ('previs.start', 'storyboard'):
            with self.subTest(route=route):
                out = self.invoke('read', route, '--json')
                self.assertEqual(out.returncode, 0, out.stderr)
                ids = [n['id'] for n in json.loads(out.stdout)['modules']]
                for required in ('character-assets', 'performance', 'expression-assets', 'execution'):
                    self.assertEqual(ids.count(required), 1, (route, required, ids))
                self.assertNotIn('platform.tools', ids)

    def test_expression_assets_are_discoverable_without_loading_video_workflow(self):
        out = self.invoke('read', 'expression-assets', '--json')
        self.assertEqual(out.returncode, 0, out.stderr)
        ids = {n['id'] for n in json.loads(out.stdout)['modules']}
        self.assertTrue({'core', 'character-assets', 'performance', 'expression-assets'}.issubset(ids))
        self.assertNotIn('workflow', ids)
        self.assertNotIn('platform.updream', ids)

    def test_scene_assets_load_when_preparing_shots_and_can_be_read_alone(self):
        for route in ('scene-assets', 'previs.start', 'storyboard', 'generation', 'handoff'):
            with self.subTest(route=route):
                out = self.invoke('read', route, '--json')
                self.assertEqual(out.returncode, 0, out.stderr)
                ids = [node['id'] for node in json.loads(out.stdout)['modules']]
                self.assertEqual(ids.count('scene-assets'), 1)
                if route == 'scene-assets':
                    self.assertEqual(set(ids), {'core', 'scene-assets'})

    def test_generation_routes_include_transition_execution_division(self):
        for route in ('generation', 'workflow'):
            with self.subTest(route=route):
                out = self.invoke('read', route, '--json')
                self.assertEqual(out.returncode, 0, out.stderr)
                modules = json.loads(out.stdout)['modules']
                ids = {n['id'] for n in modules}
                self.assertTrue({'transitions', 'expression-assets', 'execution'}.issubset(ids), ids)
                generation = next(n for n in modules if n['id'] == 'generation')['content']
                self.assertIn('## 转场的生成与剪辑分工', generation)
                self.assertNotIn('transitions.whip', ids)

    def test_delivery_and_transition_leaf_load_execution_or_revision_contract(self):
        out = self.invoke('read', 'delivery', '--json')
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn('execution', {n['id'] for n in json.loads(out.stdout)['modules']})
        out = self.invoke('read', 'transitions.whip', '--json')
        self.assertEqual(out.returncode, 0, out.stderr)
        common = next(n for n in json.loads(out.stdout)['modules'] if n['id'] == 'transitions')
        self.assertIn('## 转场改版与逐项落实', common['content'])

    def test_generation_route_cannot_skip_character_reference_requirements(self):
        for route in ('generation', 'workflow'):
            with self.subTest(route=route):
                out = self.invoke('read', route, '--json')
                self.assertEqual(out.returncode, 0, out.stderr)
                ids = [n['id'] for n in json.loads(out.stdout)['modules']]
                self.assertEqual(ids.count('character-assets'), 1)

    def test_real_orbit_slice_excludes_sibling_full_prompt(self):
        out = self.invoke('read', 'camera.orbit', '--json')
        self.assertEqual(out.returncode, 0, out.stderr)
        data = json.loads(out.stdout)
        content = '\n'.join(n['content'] for n in data['modules'])
        self.assertIn('## M03｜360°环绕', content)
        self.assertNotIn('## M01｜甩镜', content)
        self.assertNotIn('## M16｜光学变焦', content)

    def test_catalog_validation_checks_all_references(self):
        out = self.invoke('check', '--json')
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertGreater(json.loads(out.stdout)['node_count'], 30)

    def test_invalid_catalogs_fail_closed(self):
        changes = {
            'duplicate id': lambda c: c['nodes'].append(dict(c['nodes'][1])),
            'missing parent': lambda c: c['nodes'][3].update(parent='missing'),
            'parent cycle': lambda c: c['nodes'][2].update(parent='camera'),
            'dependency cycle': lambda c: c['nodes'][3].update(requires=['camera.orbit']),
            'missing dependency': lambda c: c['nodes'][4].update(requires=['absent']),
            'outside path': lambda c: c['nodes'][1]['source'].update(path='../secret.md'),
            'missing section': lambda c: c['nodes'][4]['source'].update(sections=['## Missing']),
            'duplicate section selector': lambda c: c['nodes'][4]['source'].update(sections=['## Orbit', '## Orbit']),
            'missing source': lambda c: c['nodes'][1]['source'].update(path='references/missing.md'),
            'invalid required module': lambda c: c.update(required=['absent']),
            'non-source required module': lambda c: c.update(required=['direction']),
            'unsupported version': lambda c: c.update(version=999),
        }
        for label, change in changes.items():
            with self.subTest(label=label), tempfile.TemporaryDirectory() as td:
                root = pathlib.Path(td); cat = self.fixture(root)
                change(cat); self.write_catalog(root, cat)
                out = self.invoke('read', 'lighting', '--json', root=root)
                self.assertEqual(out.returncode, 2, out.stdout)
                self.assertEqual(out.stdout, '')
                self.assertIn('module-reader:', out.stderr)

    def test_ambiguous_real_heading_fails_instead_of_choosing_first(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td); self.fixture(root)
            p = root/'references/camera.md'
            p.write_text(p.read_text(encoding='utf-8') + '\n## Orbit\nOther orbit\n', encoding='utf-8')
            out = self.invoke('read', 'camera.orbit', '--json', root=root)
            self.assertEqual(out.returncode, 2)
            self.assertIn('ambiguous', out.stderr)

    def test_reader_has_no_project_side_effects(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td); self.fixture(root)
            self.invoke('tree', '--json', root=root)
            before = {str(p.relative_to(root)): p.read_bytes() for p in root.rglob('*') if p.is_file()}
            out = self.invoke('read', 'camera.orbit', '--json', root=root)
            self.assertEqual(out.returncode, 0, out.stderr)
            after = {str(p.relative_to(root)): p.read_bytes() for p in root.rglob('*') if p.is_file()}
            self.assertEqual(before, after)

    def test_cli_outputs_utf8_even_when_host_default_is_ascii(self):
        import os
        env = dict(os.environ, PYTHONIOENCODING='ascii')
        out = subprocess.run([sys.executable, str(SCRIPT), 'tree'], env=env,
                             capture_output=True, text=True, encoding='utf-8')
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn('星迹的AIGC', out.stdout)

    def test_next_read_uses_current_source_and_fingerprint(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td); self.fixture(root)
            first = self.invoke('read', 'lighting', '--json', root=root)
            self.assertEqual(first.returncode, 0, first.stderr)
            (root/'references/light.md').write_text('# Light\nUpdated light\n', encoding='utf-8')
            second = self.invoke('read', 'lighting', '--json', root=root)
            self.assertEqual(second.returncode, 0, second.stderr)
            a = json.loads(first.stdout)['modules'][-1]
            b = json.loads(second.stdout)['modules'][-1]
            self.assertNotEqual(a['source_sha256'], b['source_sha256'])
            self.assertIn('Updated light', b['content'])

    def test_transition_leaf_combines_camera_dependency_without_sibling_templates(self):
        out = self.invoke('read', 'transitions.whip', '--json')
        self.assertEqual(out.returncode, 0, out.stderr)
        data = json.loads(out.stdout)
        ids = {n['id'] for n in data['modules']}
        self.assertTrue({'core', 'transitions', 'camera', 'camera.whip', 'transitions.whip'}.issubset(ids))
        content = '\n'.join(n['content'] for n in data['modules'])
        self.assertIn('## T04｜运动模糊／甩镜转场', content)
        self.assertNotIn('## T06｜屏幕拉远／画中空间揭示', content)

if __name__ == '__main__':
    unittest.main()
