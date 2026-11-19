"""Offline execution-link checks with real temporary files, never generation calls."""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SCRIPT = Path(__file__).with_name('validate_execution.py')
SPEC = importlib.util.spec_from_file_location('execution_validator', SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC) if SCRIPT.exists() else None
DEFAULT = object()
if MODULE:
    SPEC.loader.exec_module(MODULE)


class ExecutionChecks(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.character = self.asset('C1', 'character', 'face.dat', b'approved actor')
        self.expression = self.asset('E1', 'expression', 'expression.dat', b'approved smile')
        self.fresh = {'schema_version': 1, 'shots': [{
            'shot_id': 'SH1', 'mode': 'fresh',
            'actors': [{'person_id': 'P1', 'variant_id': 'young', 'face_visible': True,
                        'expression_required': True, 'gaze': 'looks at the letter',
                        'facial_cues': {'eyes': 'eyelids raised', 'mouth': 'corners lifted'}}],
            'references': {'characters': [self.character], 'expressions': [self.expression],
                           'scenes': [], 'props': []},
            'inputs': [self.input(self.character), self.input(self.expression)]}]}

    def file(self, name, content):
        (self.root / name).write_bytes(content)
        return {'file': name, 'sha256': hashlib.sha256(content).hexdigest()}

    def asset(self, asset_id, role, name, content):
        return dict(self.file(name, content), asset_id=asset_id, version='v1', role=role,
                    person_id='P1', variant_id='young', review_status='passed')

    @staticmethod
    def input(asset):
        return {k: asset[k] for k in ('asset_id', 'version', 'role', 'file', 'sha256')}

    def delivery(self):
        a = dict(self.file('a.mp4', b'clip a'), version='v1')
        b = dict(self.file('b.mp4', b'clip b'), version='v1')
        output = self.file('final.mp4', b'cut with white layer')
        evidence = self.file('review.txt', b'normal playback of transition T1 in current export')
        return {'schema_version': 1,
                'shots': [{'shot_id': 'SH1', 'clip': a}, {'shot_id': 'SH2', 'clip': b}],
                'planned_transitions': [{'transition_id': 'T1', 'version': 'v2',
                                         'from_shot': 'SH1', 'to_shot': 'SH2',
                                         'instructions': 'Insert one white layer at the documented cut'}],
                'output': output,
                'transitions': [{'transition_id': 'T1', 'version': 'v2',
                                 'from_shot': 'SH1', 'to_shot': 'SH2',
                                 'instructions': 'Insert one white layer at the documented cut',
                                 'execution': {'version': 'v2', 'status': 'done',
                                               'from_version': a['version'], 'to_version': b['version'],
                                               'from_sha256': a['sha256'], 'to_sha256': b['sha256'],
                                               'output_sha256': output['sha256'],
                                               'review': {'status': 'passed', 'evidence': evidence,
                                                          'output_sha256': output['sha256']}}}]}

    def continuation(self):
        source = self.file('source.mp4', b'actual source video')
        tail = self.file('tail.png', b'actual selected frame')
        receipt = {'schema_version': 1, 'source_video': str(self.root / source['file']),
                   'source_sha256': source['sha256'], 'image': str(self.root / tail['file']),
                   'image_sha256': tail['sha256'], 'frame_index': 12, 'decoded_frame_count': 20}
        proof = self.file('tail.json', json.dumps(receipt).encode())
        return {'schema_version': 1, 'shots': [{'shot_id': 'SH1', 'mode': 'continue',
                'source_clip': source, 'frame_index': 12, 'tail_frame': tail,
                'input_frame': copy.deepcopy(tail), 'receipt': proof}]}

    def check(self, data=DEFAULT, stage='first_frame'):
        self.assertIsNotNone(MODULE, 'Execution-link validator is not implemented')
        return MODULE.validate(self.fresh if data is DEFAULT else data, stage=stage, base_dir=self.root)

    def codes(self, data=DEFAULT, stage='first_frame'):
        return {e['code'] for e in self.check(data, stage)['errors']}

    def test_bound_references_pass_but_do_not_certify_visuals_or_submission(self):
        report = self.check()
        self.assertEqual(report['errors'], [])
        self.assertEqual(report['automatic_checks'], 'passed')
        self.assertEqual(report['status'], 'needs_visual_review')
        self.assertFalse(report['authorizes_submission'])
        self.assertEqual(report['visual_review'], 'not_run')

    def test_missing_character_reference_is_rejected(self):
        self.fresh['shots'][0]['references']['characters'] = []
        self.assertIn('CHARACTER_REFERENCE', self.codes())

    def test_character_must_match_current_age_and_clothing_variant(self):
        self.character['variant_id'] = 'old'
        self.assertIn('CHARACTER_REFERENCE', self.codes())

    def test_required_expression_must_exist_and_match_actor(self):
        self.expression['person_id'] = 'stranger'
        self.assertIn('EXPRESSION_REFERENCE', self.codes())

    def test_required_expression_cannot_be_omitted(self):
        self.fresh['shots'][0]['references']['expressions'] = []
        self.assertIn('EXPRESSION_REFERENCE', self.codes())

    def test_selected_reference_requires_real_attachment(self):
        self.fresh['shots'][0]['inputs'].pop()
        self.assertIn('ATTACHMENT', self.codes())

    def test_attachment_version_and_hash_must_match_selected_asset(self):
        for key, value in [('version', 'old'), ('sha256', '0' * 64), ('role', 'scene')]:
            with self.subTest(key=key):
                data = copy.deepcopy(self.fresh)
                data['shots'][0]['inputs'][0][key] = value
                self.assertIn('ATTACHMENT', self.codes(data))

    def test_unlisted_attachment_is_rejected(self):
        self.fresh['shots'][0]['inputs'].append(self.input(self.asset('C2', 'character', 'other.dat', b'other')))
        self.assertIn('ATTACHMENT', self.codes())

    def auxiliary(self):
        refs = []
        for role in ('state', 'layout', 'edit_target'):
            ref = self.asset(role, role, role + '.dat', role.encode())
            del ref['person_id'], ref['variant_id']
            refs.append(ref)
        self.fresh['shots'][0]['references']['auxiliary'] = refs
        self.fresh['shots'][0]['inputs'].extend(self.input(ref) for ref in refs)
        return refs

    def test_auxiliary_state_layout_and_edit_target_are_valid_without_person_binding(self):
        self.auxiliary()
        self.assertEqual(self.check()['errors'], [])

    def test_all_selected_auxiliary_images_must_be_attached(self):
        self.auxiliary()
        for count in (2, 0):
            with self.subTest(retained_auxiliary_inputs=count):
                data = copy.deepcopy(self.fresh)
                data['shots'][0]['inputs'] = data['shots'][0]['inputs'][:2 + count]
                self.assertIn('ATTACHMENT', self.codes(data))

    def test_unlisted_auxiliary_input_is_rejected(self):
        ref = self.asset('state', 'state', 'state.dat', b'actual end state')
        self.fresh['shots'][0]['inputs'].append(self.input(ref))
        self.assertIn('ATTACHMENT', self.codes())

    def test_auxiliary_attachment_versions_and_hashes_must_match(self):
        self.auxiliary()
        for field, value in [('version', 'old'), ('sha256', '0' * 64)]:
            with self.subTest(field=field):
                data = copy.deepcopy(self.fresh)
                data['shots'][0]['inputs'][-1][field] = value
                self.assertIn('ATTACHMENT', self.codes(data))

    def test_auxiliary_cannot_replace_actor_or_expression_references(self):
        self.auxiliary()
        self.fresh['shots'][0]['references']['characters'] = []
        self.fresh['shots'][0]['references']['expressions'] = []
        self.assertIn('CHARACTER_REFERENCE', self.codes())
        self.assertIn('EXPRESSION_REFERENCE', self.codes())

    def test_auxiliary_invalid_roles_lists_and_duplicate_asset_keys_are_rejected(self):
        self.auxiliary()
        for value in [None, {}, 'state', False]:
            with self.subTest(value=value):
                data = copy.deepcopy(self.fresh)
                data['shots'][0]['references']['auxiliary'] = value
                self.assertIn('SCHEMA', self.codes(data))
        for role in ['character', 'expression', 'unknown', []]:
            with self.subTest(role=role):
                data = copy.deepcopy(self.fresh)
                data['shots'][0]['references']['auxiliary'][0]['role'] = role
                self.assertIn('SCHEMA', self.codes(data))
        data = copy.deepcopy(self.fresh)
        data['shots'][0]['references']['auxiliary'].append(
            copy.deepcopy(data['shots'][0]['references']['auxiliary'][0]))
        self.assertIn('SCHEMA', self.codes(data))

    def test_auxiliary_files_and_review_receive_same_checks(self):
        refs = self.auxiliary()
        refs[0]['review_status'] = 'pending'
        (self.root / refs[1]['file']).write_bytes(b'changed layout')
        self.assertIn('REFERENCE_REVIEW', self.codes())
        self.assertIn('FILE', self.codes())

    def test_reference_file_must_exist_be_nonempty_and_match_hash(self):
        for content in (None, b'', b'changed face'):
            with self.subTest(content=content):
                file = self.root / self.character['file']
                file.unlink(missing_ok=True)
                if content is not None:
                    file.write_bytes(content)
                self.assertIn('FILE', self.codes())

    def test_reference_review_cannot_be_pending(self):
        self.expression['review_status'] = 'pending'
        self.assertIn('REFERENCE_REVIEW', self.codes())

    def test_visible_face_needs_gaze_and_two_facial_channels(self):
        actor = self.fresh['shots'][0]['actors'][0]
        actor['gaze'] = ''
        actor['facial_cues'] = {'mouth': 'closed'}
        self.assertIn('PERFORMANCE_CUES', self.codes())

    def test_no_expression_needs_reason_not_an_empty_reference_excuse(self):
        actor = self.fresh['shots'][0]['actors'][0]
        actor['expression_required'] = False
        self.assertIn('EXPRESSION_DECISION', self.codes())
        actor['expression_reason'] = 'Back of head only in this framing'
        actor['face_visible'] = False
        self.fresh['shots'][0]['references']['expressions'] = []
        self.fresh['shots'][0]['inputs'].pop()
        self.assertEqual(self.check()['errors'], [])

    def test_unpeopled_shot_can_have_no_actor_assets(self):
        shot = self.fresh['shots'][0]
        shot.update(actors=[], references={k: [] for k in ('characters', 'expressions', 'scenes', 'props')}, inputs=[])
        self.assertEqual(self.check()['errors'], [])

    def test_continue_uses_real_tail_without_new_character_or_expression_images(self):
        data = self.continuation()
        self.assertEqual(self.check(data)['errors'], [])
        data['shots'][0]['input_frame'] = self.file('redrawn.png', b'redrawn frame')
        self.assertIn('CONTINUE_INPUT', self.codes(data))

    def test_continue_requires_existing_frame_bridge_receipt(self):
        data = self.continuation()
        del data['shots'][0]['receipt']
        self.assertTrue(self.check(data)['errors'])

    def test_continue_receipt_binds_source_tail_frame_and_decode_index(self):
        for field, value in [('source_sha256', '0' * 64), ('image_sha256', '1' * 64),
                             ('frame_index', 13), ('schema_version', True),
                             ('decoded_frame_count', 10)]:
            with self.subTest(field=field):
                data = self.continuation()
                receipt = json.loads((self.root / 'tail.json').read_text())
                receipt[field] = value
                data['shots'][0]['receipt'] = self.file('tail.json', json.dumps(receipt).encode())
                self.assertIn('CONTINUE_RECEIPT', self.codes(data))

    def test_continue_receipt_malformed_json_is_reported(self):
        data = self.continuation()
        data['shots'][0]['receipt'] = self.file('tail.json', b'not-json')
        self.assertIn('CONTINUE_RECEIPT', self.codes(data))

    def test_continue_without_source_provenance_is_rejected(self):
        tail = self.file('tail.png', b'actual tail')
        data = {'schema_version': 1, 'shots': [{'shot_id': 'SH1', 'mode': 'continue',
                'tail_frame': tail, 'input_frame': tail, 'frame_index': True}]}
        self.assertTrue(self.check(data)['errors'])

    def test_delivery_binds_transition_materials_execution_and_export_review(self):
        report = self.check(self.delivery(), 'delivery')
        self.assertEqual(report['errors'], [])
        self.assertEqual(report['visual_review'], 'not_run')

    def test_generated_clips_without_edit_task_cannot_complete_transition(self):
        data = self.delivery()
        data['transitions'][0]['execution'] = {'status': 'generated'}
        self.assertIn('TRANSITION_EXECUTION', self.codes(data, 'delivery'))

    def test_a_planned_transition_cannot_be_silently_dropped(self):
        data = self.delivery()
        data['transitions'] = []
        self.assertIn('TRANSITION_COVERAGE', self.codes(data, 'delivery'))

    def test_stale_transition_version_and_materials_are_rejected(self):
        for key, value in [('version', 'v1'), ('from_sha256', '0' * 64), ('to_sha256', '1' * 64)]:
            with self.subTest(key=key):
                data = self.delivery()
                data['transitions'][0]['execution'][key] = value
                self.assertIn('TRANSITION_STALE', self.codes(data, 'delivery'))

    def test_clip_version_change_invalidates_execution_even_if_bytes_are_unchanged(self):
        for index in (0, 1):
            with self.subTest(index=index):
                data = self.delivery()
                data['shots'][index]['clip']['version'] = 'v2'
                self.assertIn('TRANSITION_STALE', self.codes(data, 'delivery'))

    def test_execution_must_record_selected_clip_versions(self):
        for field in ('from_version', 'to_version'):
            with self.subTest(field=field):
                data = self.delivery()
                del data['transitions'][0]['execution'][field]
                self.assertTrue(self.check(data, 'delivery')['errors'])

    def test_modified_current_design_invalidates_old_transition_receipt(self):
        data = self.delivery()
        data['planned_transitions'][0]['version'] = 'v3'
        self.assertIn('TRANSITION_STALE', self.codes(data, 'delivery'))

    def test_design_text_and_endpoint_changes_invalidate_receipts_even_without_version_bump(self):
        for field, value in [('instructions', 'Replace white layer with a matching shape cut'),
                             ('from_shot', 'SH2'), ('to_shot', 'SH1')]:
            with self.subTest(field=field):
                data = self.delivery()
                data['planned_transitions'][0][field] = value
                self.assertIn('TRANSITION_STALE', self.codes(data, 'delivery'))

    def test_extra_unplanned_transition_is_rejected(self):
        data = self.delivery()
        extra = copy.deepcopy(data['transitions'][0])
        extra['transition_id'] = 'obsolete-T2'
        data['transitions'].append(extra)
        self.assertIn('TRANSITION_COVERAGE', self.codes(data, 'delivery'))

    def test_duplicate_transition_records_and_malformed_delivery_are_rejected(self):
        for field, value in [('planned_transitions', 'T1'), ('transitions', {}),
                             ('output', []), ('transitions', [False])]:
            with self.subTest(field=field):
                data = self.delivery()
                data[field] = value
                self.assertIn('SCHEMA', self.codes(data, 'delivery'))
        for field in ('planned_transitions', 'transitions'):
            data = self.delivery()
            data[field].append(copy.deepcopy(data[field][0]))
            self.assertIn('SCHEMA', self.codes(data, 'delivery'))

    def test_output_change_invalidates_execution_and_review(self):
        data = self.delivery()
        data['output'] = self.file('new-final.mp4', b'different final export')
        self.assertIn('TRANSITION_STALE', self.codes(data, 'delivery'))

    def test_review_requires_current_output_and_real_evidence(self):
        data = self.delivery()
        review = data['transitions'][0]['execution']['review']
        review['output_sha256'] = '0' * 64
        review['evidence']['file'] = 'missing-review.txt'
        self.assertIn('TRANSITION_REVIEW', self.codes(data, 'delivery'))
        self.assertIn('FILE', self.codes(data, 'delivery'))

    def test_no_transitions_is_explicit_and_valid(self):
        data = self.delivery()
        data.update(planned_transitions=[], transitions=[])
        self.assertEqual(self.check(data, 'delivery')['errors'], [])

    def test_malformed_schemas_report_errors_without_exceptions(self):
        variants = [None, [], {}, {'schema_version': True, 'shots': []},
                    {'schema_version': 1, 'shots': [True]},
                    {'schema_version': 1, 'shots': {}},
                    {'schema_version': 1, 'shots': []}]
        for value in variants:
            with self.subTest(value=value):
                self.assertIn('SCHEMA', self.codes(value))
        for field, value in [('actors', {}), ('references', []), ('inputs', 'path')]:
            with self.subTest(field=field):
                data = copy.deepcopy(self.fresh)
                data['shots'][0][field] = value
                self.assertIn('SCHEMA', self.codes(data))

    def test_bool_fields_hashes_and_duplicate_ids_are_strict(self):
        for mutate in [
            lambda d: d['shots'][0]['actors'][0].update(face_visible='false'),
            lambda d: d['shots'][0]['actors'][0].update(expression_required=1),
            lambda d: d['shots'][0]['inputs'][0].update(sha256=[]),
            lambda d: d['shots'].append(copy.deepcopy(d['shots'][0])),
            lambda d: d['shots'][0]['actors'].append(copy.deepcopy(d['shots'][0]['actors'][0])),
            lambda d: d['shots'][0]['inputs'].append(copy.deepcopy(d['shots'][0]['inputs'][0])),
        ]:
            data = copy.deepcopy(self.fresh)
            mutate(data)
            self.assertIn('SCHEMA', self.codes(data))

    def test_validation_is_read_only_and_cli_resolves_relative_files_from_json(self):
        self.assertIsNotNone(MODULE, 'Execution-link validator is not implemented')
        package = self.root / 'input.json'
        package.write_text(json.dumps(self.fresh), encoding='utf-8')
        before = {str(p): p.read_bytes() for p in self.root.iterdir()}
        out = subprocess.run([sys.executable, str(SCRIPT), str(package), '--stage', 'first_frame'],
                             cwd=tempfile.gettempdir(), capture_output=True, encoding='utf-8')
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(json.loads(out.stdout)['errors'], [])
        after = {str(p): p.read_bytes() for p in self.root.iterdir()}
        self.assertEqual(before, after)

    def test_cli_reports_invalid_input_and_link_problems_with_separate_codes(self):
        self.assertIsNotNone(MODULE, 'Execution-link validator is not implemented')
        package = self.root / 'input.json'
        for payload, expected in [('not json', 2), (json.dumps({'schema_version': 1, 'shots': []}), 2)]:
            package.write_text(payload, encoding='utf-8')
            out = subprocess.run([sys.executable, str(SCRIPT), str(package), '--stage', 'first_frame'],
                                 capture_output=True, encoding='utf-8')
            self.assertEqual(out.returncode, expected)
            self.assertFalse(json.loads(out.stdout)['authorizes_submission'])
        self.fresh['shots'][0]['inputs'].pop()
        package.write_text(json.dumps(self.fresh), encoding='utf-8')
        out = subprocess.run([sys.executable, str(SCRIPT), str(package), '--stage', 'first_frame'],
                             capture_output=True, encoding='utf-8')
        self.assertEqual(out.returncode, 1)


if __name__ == '__main__':
    unittest.main()
