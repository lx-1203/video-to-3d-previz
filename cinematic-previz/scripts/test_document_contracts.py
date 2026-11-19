"""Cross-document contracts: links, rule loading and editable example timing."""
import pathlib
import json
import re
import unittest

import read_modules
import validate_documents
import validate_duration_limits

ROOT = pathlib.Path(__file__).resolve().parent.parent


class DocumentContracts(unittest.TestCase):
    def test_package_links_and_current_examples_are_valid(self):
        report = validate_documents.validate(ROOT)
        self.assertEqual(report['errors'], [], report['errors'])

    def test_missing_heading_and_outside_package_are_reported(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            (root / 'a.md').write_text('[bad](b.md#missing)\n[escape](../outside.md)\n', encoding='utf-8')
            (root / 'b.md').write_text('# Present\n', encoding='utf-8')
            codes = {x['code'] for x in validate_documents.check_links(root)}
            self.assertEqual(codes, {'LINK_ANCHOR', 'LINK_OUTSIDE'})

    def test_code_examples_and_external_urls_are_not_local_links(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            (root / 'a.md').write_text(
                '# A\n[external](https://example.test/x#y)\n'
                '```md\n[illustration](missing.md)\n```\n', encoding='utf-8')
            self.assertEqual(validate_documents.check_links(root), [])

    def test_timing_detects_underlength_generation_and_overlong_edit(self):
        text = ('## Generation plan\n\n'
                '| segment_id | generation_seconds | edit_seconds |\n'
                '| --- | --- | --- |\n| SG01 | 3 | 4 |\n')
        codes = {x['code'] for x in validate_documents.check_example_timing('example.md', text)}
        self.assertEqual(codes, {'EXAMPLE_DURATION', 'EXAMPLE_EDIT'})

    def test_short_edit_can_come_from_valid_generation(self):
        text = ('| segment_id | generation_seconds | edit_seconds |\n'
                '| --- | --- | --- |\n| SG01 | 4 | 2 |\n')
        self.assertEqual(validate_documents.check_example_timing('example.md', text), [])

    def test_default_multishot_example_is_one_fifteen_second_task(self):
        text = ('<!-- generation_mode: multi_shot -->\n'
                '| segment_id | generation_seconds | edit_seconds |\n'
                '| --- | --- | --- |\n| PK01 | 15 | 15 |\n')
        self.assertEqual(validate_documents.check_example_timing('example.md', text), [])
        codes = {e['code'] for e in validate_documents.check_example_timing('example.md', text.replace('15 | 15', '14 | 14'))}
        self.assertIn('EXAMPLE_DURATION', codes)

    def test_example_total_must_match_kept_duration(self):
        text = ('<!-- edit_total_seconds: 15 -->\n'
                '| segment_id | generation_seconds | edit_seconds |\n'
                '| --- | --- | --- |\n| SG01 | 4 | 2 |\n')
        codes = {x['code'] for x in validate_documents.check_example_timing('example.md', text)}
        self.assertIn('EXAMPLE_TOTAL', codes)

    def test_nonfinite_example_values_are_rejected(self):
        text = ('| segment_id | generation_seconds | edit_seconds |\n'
                '| --- | --- | --- |\n| SG01 | nan | nan |\n')
        codes = {x['code'] for x in validate_documents.check_example_timing('example.md', text)}
        self.assertIn('EXAMPLE_NUMBER', codes)

    def test_project_json_is_accepted_by_real_duration_validator(self):
        text = (ROOT / 'references/production-contract.md').read_text(encoding='utf-8')
        data = json.loads(re.search(r'```json\s*\n(.*?)\n```', text, re.S)[1])
        self.assertEqual(validate_duration_limits.validate(data, 4, 8)['errors'], [])
        ids = {s['segment_id'] for s in data['generation_segments']}
        for shot in data['shots']:
            self.assertTrue(set(shot['segments']).issubset(ids))
        for edge in data['handoffs']:
            self.assertTrue({edge['from'], edge['to']}.issubset(ids))

    def test_json_example_checker_rejects_schema_and_reference_drift(self):
        text = '```json\n{"segments": []}\n```'
        self.assertTrue(validate_documents.check_project_example(text))
        text = (ROOT / 'references/production-contract.md').read_text(encoding='utf-8')
        text = text.replace('"to": "SH010/SG02"', '"to": "missing"')
        codes = {x['code'] for x in validate_documents.check_project_example(text)}
        self.assertIn('EXAMPLE_REFERENCE', codes)

    def test_single_method_query_stays_small_and_does_not_load_production(self):
        state = read_modules.load_catalog(ROOT)
        data = read_modules.read_modules(state, ['camera.orbit'])
        ids = {x['id'] for x in data['modules']}
        self.assertEqual(ids, {'core', 'camera', 'camera.orbit'})
        core = next(x for x in data['modules'] if x['id'] == 'core')
        self.assertLess(len(core['content']), 1800)
        self.assertLess(len((ROOT / 'SKILL.md').read_text(encoding='utf-8')), 3500)

    def test_production_routes_load_their_authoritative_rules(self):
        state = read_modules.load_catalog(ROOT)
        for route in ['storyboard', 'generation', 'workflow', 'prompts']:
            with self.subTest(route=route):
                data = read_modules.read_modules(state, [route])
                ids = {x['id'] for x in data['modules']}
                self.assertTrue({'timing', 'sound', 'stages'}.issubset(ids), ids)
                self.assertEqual(len(data['modules']), len(ids))


if __name__ == '__main__':
    unittest.main()
