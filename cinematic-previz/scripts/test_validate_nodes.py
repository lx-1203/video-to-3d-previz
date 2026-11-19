"""Behavioral checks for sentence-node validation and its standalone CLI."""
import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SCRIPT = Path(__file__).with_name('validate_nodes.py')
SPEC = importlib.util.spec_from_file_location('node_validator', SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC) if SCRIPT.is_file() else None
if MODULE:
    SPEC.loader.exec_module(MODULE)


class NodeChecks(unittest.TestCase):
    def setUp(self):
        self.data = {'nodes': [{'id': 'N01', 'text': '春播开始了。'},
                               {'id': 'N02', 'text': 'Are you ready?'}]}

    def check(self):
        self.assertIsNotNone(MODULE, 'The sentence-node validator is missing')
        return MODULE.validate(self.data)

    def codes(self):
        return {error['code'] for error in self.check()['errors']}

    def test_valid_nodes_still_need_semantic_review(self):
        result = self.check()
        self.assertEqual(result['errors'], [])
        self.assertEqual(result['automatic_checks'], 'passed')
        self.assertEqual(result['status'], 'needs_review')
        self.assertEqual(result['semantic_review']['status'], 'pending')

    def test_shots_accept_narration_alias(self):
        self.data = {'shots': [{'shot_id': 'SH01', 'narration': '增长了13.6%。'},
                               {'shot_id': 'SH02', 'narration': '“Really?!”'}]}
        self.assertEqual(self.check()['errors'], [])

    def test_fragments_are_rejected_even_when_source_matches(self):
        for fragment in ['就这样，北京春播、', '春播开始，', '因为', 'Wait...', '等等……', '但是——']:
            with self.subTest(fragment=fragment):
                self.data = {'nodes': [{'id': 'N01', 'text': fragment}], 'source_text': fragment}
                self.assertIn('SENTENCE_BOUNDARY', self.codes())

    def test_multiple_sentences_in_one_node_are_rejected(self):
        self.data['nodes'][0]['text'] = '春播开始了。我们出发吧！'
        self.assertIn('SENTENCE_COUNT', self.codes())

    def test_source_text_is_optional_but_must_match_exact_concatenation(self):
        self.assertEqual(self.check()['errors'], [])
        self.data['source_text'] = ''.join(row['text'] for row in self.data['nodes'])
        self.assertEqual(self.check()['errors'], [])
        self.data['source_text'] += '被漏掉的原文。'
        self.assertIn('SOURCE_COVERAGE', self.codes())

    def test_source_comparison_preserves_punctuation_and_whitespace(self):
        self.data = {'nodes': [{'id': 'N01', 'text': '  春播开始了。\n'}],
                     'source_text': '  春播开始了。\n'}
        original = copy.deepcopy(self.data)
        self.assertEqual(self.check()['errors'], [])
        self.assertEqual(self.data, original)
        self.data['source_text'] = '春播开始了。'
        self.assertIn('SOURCE_COVERAGE', self.codes())

    def test_wrong_root_and_node_types_do_not_crash(self):
        for value in [None, [], {'nodes': None}, {'nodes': []}, {'nodes': [None]},
                      {'nodes': [{'id': [], 'text': '完整句。'}]},
                      {'nodes': [{'id': 'N01', 'text': 7}]}, {'shots': 'bad'},
                      {'nodes': [{'id': 'N01', 'text': ''}]},
                      {'nodes': [{'id': 'N01', 'text': '完整句。'}], 'source_text': None}]:
            with self.subTest(value=value):
                self.data = value
                self.assertIn('SCHEMA', self.codes())

    def test_exactly_one_node_collection_is_required(self):
        for value in [{}, {'nodes': [], 'shots': []}]:
            with self.subTest(value=value):
                self.data = value
                self.assertIn('SCHEMA', self.codes())

    def test_identifiers_are_nonempty_and_unique(self):
        for value in ['', None, 'N02']:
            with self.subTest(value=value):
                self.data['nodes'][0]['id'] = value
                self.assertIn('SCHEMA', self.codes())

    def test_importlib_load_works_without_scripts_in_sys_path(self):
        self.assertTrue(SCRIPT.is_file(), 'The sentence-node CLI is missing')
        code = ('import importlib.util, sys; '
                'spec=importlib.util.spec_from_file_location("external_node_validator", sys.argv[1]); '
                'module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module); '
                'assert module.validate({"nodes":[{"id":"N1","text":"Ready."}]})["errors"] == []')
        result = subprocess.run([sys.executable, '-B', '-c', code, str(SCRIPT)],
                                capture_output=True, text=True, encoding='utf-8', cwd=tempfile.gettempdir())
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_cli_exit_codes_distinguish_pass_revision_and_invalid_input(self):
        self.assertTrue(SCRIPT.is_file(), 'The sentence-node CLI is missing')
        cases = [(json.dumps(self.data, ensure_ascii=False), 0, 'needs_review'),
                 (json.dumps({'nodes': [{'id': 'N01', 'text': '半句，'}]}, ensure_ascii=False),
                  1, 'needs_revision'), ('{', 2, 'invalid_input')]
        with tempfile.TemporaryDirectory() as folder:
            package = Path(folder) / '句子节点.json'
            for content, returncode, status in cases:
                with self.subTest(status=status):
                    package.write_text(content, encoding='utf-8')
                    result = subprocess.run([sys.executable, '-B', '-X', 'utf8', str(SCRIPT), str(package)],
                                            capture_output=True, text=True, encoding='utf-8')
                    self.assertEqual(result.returncode, returncode, result.stderr)
                    self.assertEqual(json.loads(result.stdout)['status'], status)
                    self.assertEqual(package.read_text(encoding='utf-8'), content)


if __name__ == '__main__':
    unittest.main()
