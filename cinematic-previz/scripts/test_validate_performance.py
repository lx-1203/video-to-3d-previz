"""Behavioral checks for the local performance-package validator."""
import copy
import importlib.util
import json
import pathlib
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'scripts' / 'validate_performance.py'
SPEC = importlib.util.spec_from_file_location('performance_validator', SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC) if SCRIPT.exists() else None
if MODULE:
    SPEC.loader.exec_module(MODULE)


class PerformanceChecks(unittest.TestCase):
    def setUp(self):
        self.data = json.loads((ROOT / 'references' / 'performance-example.json').read_text(encoding='utf-8'))

    def check(self):
        self.assertIsNotNone(MODULE, 'The performance-package validator has not been implemented')
        return MODULE.validate(self.data)

    def codes(self):
        return {e['code'] for e in self.check()['errors']}

    def test_valid_plan_still_requires_semantic_and_video_review(self):
        report = self.check()
        self.assertEqual(report['errors'], [])
        self.assertEqual(report['status'], 'needs_review')
        self.assertEqual(report['video_review'], 'not_run')
        self.assertEqual(report['negative_transport'], 'constraints_only')

    def test_banned_intent_is_rejected_in_action_prompt(self):
        self.data['positive_prompt']['action'] += '她强撑笑意。'
        self.assertIn('ABSTRACT_WORD', self.codes())

    def test_banned_intent_is_rejected_in_beats(self):
        self.data['beats'][1]['cues'][0]['text'] = '眼神复杂'
        self.assertIn('ABSTRACT_WORD', self.codes())

    def test_original_dialogue_with_emotion_word_is_preserved(self):
        for key in ['dialogue']:
            self.data[key][0]['text'] = '妈，我很开心。'
        self.data['source']['dialogue'][0]['text'] = '妈，我很开心。'
        self.data['capabilities'].update(verified=True, audio=True)
        report = self.check()
        self.assertEqual(report['errors'], [])
        self.assertIn('妈，我很开心。', report['compiled_positive_prompt'])

    def test_dialogue_change_and_speaker_change_are_rejected(self):
        self.data['dialogue'][0]['text'] = '换我陪你。'
        self.data['dialogue'][0]['speaker_id'] = 'mother'
        self.assertIn('DIALOGUE_CHANGED', self.codes())

    def test_gap_overlap_and_nonfinite_time_are_rejected(self):
        for value in [2.1, 1.9, float('nan'), True]:
            with self.subTest(value=value):
                self.data['beats'][1]['start'] = value
                self.assertIn('TIMELINE', self.codes())

    def test_two_primary_actions_in_one_beat_are_rejected(self):
        self.data['beats'][0]['primary_action'] = [self.data['beats'][0]['primary_action'], {'id':'A2', 'actor_id':'daughter', 'text':'转身离开'}]
        self.assertIn('PRIMARY_ACTION', self.codes())

    def test_action_actor_and_cue_visibility_are_checked(self):
        self.data['beats'][0]['primary_action']['actor_id'] = 'stranger'
        self.data['beats'][0]['cues'][0]['channel'] = 'tail'
        codes = self.codes()
        self.assertIn('ACTOR_REFERENCE', codes)
        self.assertIn('VISIBILITY', codes)

    def test_missing_facial_part_and_voice_parameters_are_rejected(self):
        for b in self.data['beats']:
            b['cues'] = [c for c in b['cues'] if c['channel'] != 'mouth']
        self.data['dialogue'][0]['voice'] = {'volume':'轻声'}
        codes = self.codes()
        self.assertIn('OBSERVABLE_CUES', codes)
        self.assertIn('VOICE', codes)

    def test_hidden_face_does_not_require_invisible_microexpressions(self):
        self.data['actors'][0]['visible_channels'] = ['hands']
        for b in self.data['beats']:
            b['cues'] = [{'actor_id':'daughter', 'channel':'hands', 'text':'保持双手握持'}]
        self.data['actors'][1]['visible_channels'] = []
        self.assertEqual(self.check()['errors'], [])

    def test_silent_scene_needs_no_voice_parameters(self):
        self.data['source']['dialogue'] = []
        self.data['dialogue'] = []
        self.assertEqual(self.check()['errors'], [])

    def test_negative_transport_requires_verified_support(self):
        self.data['capabilities']['negative_prompt'] = True
        self.assertEqual(self.check()['negative_transport'], 'constraints_only')
        self.data['capabilities']['verified'] = True
        self.assertEqual(self.check()['negative_transport'], 'native')

    def test_segment_limit_and_state_reset_are_rejected(self):
        self.data['capabilities'].update(verified=True, max_seconds=4)
        self.assertIn('SEGMENT_LIMIT', self.codes())
        self.data['segments'] = [dict(id='SG01', start=0, end=4, handoff='start', start_state='手靠近', end_state='握住'),dict(id='SG02', start=4, end=7, handoff='continue', start_state='手靠近', end_state='握住')]
        self.assertIn('CONTINUITY', self.codes())

    def test_no_automatic_split_at_four_seconds(self):
        self.assertEqual(len(self.data['segments']), 1)
        self.assertEqual(self.check()['errors'], [])

    def test_forged_quality_report_is_ignored(self):
        self.data['quality'] = {'status':'passed', 'blacklist_count':0}
        self.data['positive_prompt']['action'] = '她难过。'
        self.assertNotEqual(self.check()['status'], 'passed')
        self.assertIn('ABSTRACT_WORD', self.codes())

    def test_cli_reads_utf8_and_returns_nonzero_for_invalid_json(self):
        self.assertTrue(SCRIPT.exists(), 'Validator CLI is missing')
        with tempfile.TemporaryDirectory() as temp:
            file = pathlib.Path(temp) / '表演.json'
            file.write_text('{', encoding='utf-8')
            result = subprocess.run([sys.executable, '-X', 'utf8', str(SCRIPT), str(file)], capture_output=True, text=True, encoding='utf-8')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(json.loads(result.stdout)['status'], 'invalid_input')

    def test_wrong_root_type_does_not_crash(self):
        self.data = []
        self.assertIn('SCHEMA', self.codes())


    def test_malformed_types_are_reported_without_crashing(self):
        cases = [('schema_version', True), ('dialogue', [{'mode': {}}]), ('beats', [None]), ('actors', [{'id': [], 'visible_channels': {}}])]
        for field, value in cases:
            with self.subTest(field=field):
                original = copy.deepcopy(self.data)
                self.data[field] = value
                self.assertTrue(self.check()['errors'])
                self.data = original

    def test_voice_is_separate_until_audio_support_is_verified(self):
        result = self.check()
        self.assertNotIn('原文台词：', result['compiled_positive_prompt'])
        self.assertEqual(len(result['dialogue_plan']), 1)
        self.data['capabilities'].update(verified=True, audio=True)
        self.assertIn('原文台词：', self.check()['compiled_positive_prompt'])


    def test_dialogue_time_order_must_match_source_order(self):
        self.data['source']['dialogue'] = [{'speaker_id':'daughter', 'text':'妈。', 'mode':'on_screen'}, {'speaker_id':'daughter', 'text':'我陪你。', 'mode':'on_screen'}]
        first = copy.deepcopy(self.data['dialogue'][0])
        second = copy.deepcopy(first)
        first.update(text='妈。', start=4, end=5)
        second.update(text='我陪你。', start=2, end=3)
        self.data['dialogue'] = [first, second]
        self.assertIn('DIALOGUE_ORDER', self.codes())

    def set_dialogue_text(self, value):
        self.data['source']['dialogue'][0]['text'] = value
        self.data['dialogue'][0]['text'] = value

    def split_segments(self, at):
        self.data['segments'] = [
            dict(id='SG01', start=0, end=at, handoff='start', start_state='before', end_state='ongoing'),
            dict(id='SG02', start=at, end=7, handoff='continue', start_state='ongoing', end_state='after'),
        ]

    def two_spoken_sentences(self):
        first = copy.deepcopy(self.data['dialogue'][0])
        second = copy.deepcopy(first)
        first.update(text='妈。', start=1, end=2)
        second.update(text='我陪你。', start=4, end=5)
        self.data['dialogue'] = [first, second]
        self.data['source']['dialogue'] = [
            {key: line[key] for key in ('speaker_id', 'text', 'mode')}
            for line in self.data['dialogue']
        ]

    def test_matching_source_and_compiled_fragments_are_rejected(self):
        for value in ['就这样，北京春播、', '等你回来，', '等你回来；', '等你回来：',
                      '等你回来', '收益增长13.6%', '等等……', '等等...', '等等—', '等等--']:
            with self.subTest(text=value):
                self.set_dialogue_text(value)
                self.assertIn('SENTENCE_BOUNDARY', self.codes())

    def test_sentence_endings_quotes_and_decimal_are_accepted(self):
        for value in ['等你回来。', '你回来吗？', '等你回来！', 'Come back.', 'Will you?',
                      'Come back!', '“等你回来。”', '（等你回来。）', '收益增长13.6%。',
                      'Growth is 13.6%.', '真的吗？！', 'Wait…really!', '等你回来。\n']:
            with self.subTest(text=value):
                self.set_dialogue_text(value)
                self.assertEqual(self.check()['errors'], [])

    def test_leading_decimal_is_not_a_sentence_boundary(self):
        for value in ['It takes .5 seconds.', 'The change is -.5 percent.',
                      'The change is +.5 percent.', 'The probability is (.5).']:
            with self.subTest(text=value):
                self.set_dialogue_text(value)
                self.assertEqual(self.check()['errors'], [])

    def test_each_spoken_text_unit_has_exactly_one_sentence(self):
        for value in ['妈。我陪你。', 'Hello. Come back!', '你回来吗？快点！']:
            with self.subTest(text=value):
                self.set_dialogue_text(value)
                self.assertIn('SENTENCE_COUNT', self.codes())

    def test_source_fragment_is_checked_independently_of_compiled_text(self):
        self.data['source']['dialogue'][0]['text'] = '就这样，北京春播、'
        codes = self.codes()
        self.assertIn('SENTENCE_BOUNDARY', codes)
        self.assertIn('DIALOGUE_CHANGED', codes)

    def test_segment_cannot_split_a_spoken_sentence(self):
        self.split_segments(4)
        self.assertIn('DIALOGUE_SPLIT', self.codes())

    def test_segment_boundary_at_speech_endpoint_is_allowed(self):
        for at in [2.4, 5.5]:
            with self.subTest(at=at):
                self.split_segments(at)
                self.assertEqual(self.check()['errors'], [])

    def test_one_segment_cannot_mix_multiple_spoken_sentences(self):
        self.two_spoken_sentences()
        self.assertIn('SEGMENT_SENTENCES', self.codes())

    def test_one_shot_can_have_sentences_in_different_segments(self):
        self.two_spoken_sentences()
        self.split_segments(3)
        report = self.check()
        self.assertEqual(report['errors'], [])
        self.assertEqual(report['status'], 'needs_review')
        self.assertEqual(report['semantic_review']['status'], 'pending')

    def test_silent_action_segments_need_no_spoken_sentence(self):
        self.data['source']['dialogue'] = []
        self.data['dialogue'] = []
        self.split_segments(4)
        self.assertEqual(self.check()['errors'], [])


if __name__ == '__main__':
    unittest.main()
