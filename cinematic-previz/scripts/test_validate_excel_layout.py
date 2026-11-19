"""Regression cases for the default main/guidance sheet separation."""
import copy
from pathlib import Path
import unittest

import validate_excel_layout as layout


class ExcelLayoutTests(unittest.TestCase):
    def setUp(self):
        self.tables = {
            '逐镜脚本': {5: ['镜号与时间', '场次与人物阶段', '当前完整视频规划词', '首帧', '人物参考', '场景参考', '表情参考', '物品参考', '生成设置与状态', '审阅意见'], 6: ['SH01\n00:00—00:05', '夜田', '完整视频词']},
            '提示词指导': {5: ['镜号与时间', *layout.GUIDANCE_HEADERS], 6: ['SH01\n00:00—00:05', *['本镜设计'] * 6]},
        }

    def codes(self):
        return {e['code'] for e in layout.validate(self.tables)['errors']}

    def test_split_layout_is_valid(self):
        self.assertEqual(self.codes(), set())

    def test_guidance_columns_in_main_are_rejected_even_when_hidden(self):
        self.tables['逐镜脚本'][5].extend(layout.GUIDANCE_HEADERS)
        self.assertIn('MAIN_GUIDANCE_COLUMNS', self.codes())

    def test_legacy_synonym_is_rejected(self):
        self.tables['逐镜脚本'][5].append('动作与表演')
        self.assertIn('MAIN_GUIDANCE_COLUMNS', self.codes())

    def test_missing_guidance_sheet_is_rejected(self):
        del self.tables['提示词指导']
        self.assertIn('SHEET_MISSING', self.codes())

    def test_missing_guidance_column_is_rejected(self):
        self.tables['提示词指导'][5].pop()
        self.assertIn('GUIDANCE_HEADERS', self.codes())

    def test_duplicate_missing_or_reordered_shots_are_rejected(self):
        self.tables['逐镜脚本'][7] = ['SH02\n00:05—00:09', '夜田', '完整视频词']
        self.tables['提示词指导'][7] = copy.deepcopy(self.tables['提示词指导'][6])
        self.assertIn('SHOT_DUPLICATE', self.codes())
        self.assertIn('SHOT_MAPPING', self.codes())
        self.tables['提示词指导'][7][0] = 'SH02\n00:05—00:09'
        self.assertEqual(self.codes(), set())
        self.tables['提示词指导'][6], self.tables['提示词指导'][7] = self.tables['提示词指导'][7], self.tables['提示词指导'][6]
        self.assertIn('SHOT_MAPPING', self.codes())

    def test_empty_design_and_summary_prompt_are_rejected(self):
        self.tables['提示词指导'][6][2] = ''
        self.tables['逐镜脚本'][6][2] = ''
        self.assertIn('GUIDANCE_EMPTY', self.codes())
        self.assertIn('PROMPT_EMPTY', self.codes())

    def test_guidance_does_not_become_second_video_prompt_source(self):
        self.tables['提示词指导'][5].append('当前完整视频规划词')
        self.assertIn('GUIDANCE_HEADERS', self.codes())

    def test_shipped_example_matches_the_same_layout_contract(self):
        text = (Path(__file__).resolve().parent.parent / 'references/storyboard-example.md').read_text(encoding='utf-8')
        tables = {}
        for section, name in [('## A｜', '逐镜脚本'), ('## A1｜', '提示词指导')]:
            block = next(part for part in text.split('\n## ') if (part if part.startswith('## ') else '## ' + part).startswith(section))
            rows = [[v.strip().replace('<br>', '\n') for v in line.strip('|').split('|')] for line in block.splitlines() if line.startswith('|')]
            tables[name] = {5: rows[0], **{i + 6: row for i, row in enumerate(rows[2:])}}
        self.assertIn('<!-- reading_mode: no_text -->', text)
        tables['朗读测时'] = {3: ['朗读模式', '无朗读文本']}
        report = layout.validate_delivery(tables)
        self.assertEqual(report['errors'], [])
        self.assertEqual(report['shots'], 1)
        self.assertEqual(report['production_timing']['segment_seconds'], 15)


if __name__ == '__main__':
    unittest.main()
