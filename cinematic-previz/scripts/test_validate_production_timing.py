"""Regressions for the 15-second package route and reading-first evidence."""
import unittest

import validate_production_timing as timing
import validate_excel_layout as layout
from test_validate_prompt_format import two_shots


def workbook():
    text = two_shots().replace('总计8秒', '总计15秒').replace('4—8秒', '4—15秒')
    return {
        '逐镜脚本': {5: ['镜号与时间', '场次与人物阶段', '当前完整视频规划词', '首帧', '人物参考', '场景参考', '表情参考', '物品参考', '生成设置与状态', '审阅意见'], 6: ['PK01\n00:00—00:15\n15秒大段', '课堂', text, '', '', '', '', '', '生成模式：multi_shot；15秒', '']},
        '提示词指导': {5: ['镜号与时间', *layout.GUIDANCE_HEADERS], 6: ['PK01\n00:00—00:15\n15秒大段', *['本段设计'] * 6]},
        '原文与旁白': {5: ['发声单元', '开始秒', '结束秒', '时长秒', '完整句原文'], 6: ['AU01', 0, 5, 5, '共有1349朵。']},
        '朗读测时': {3: ['估算音节/秒', 3.2], 5: ['发声单元', '朗读文本（数字读法）', '估算音节数', '标点停顿秒', '估算朗读秒', '本地试读秒', '旁白规划秒', '较估算余量秒', '较试读余量秒', '覆盖15秒大段'], 6: ['AU01', '共有一千三百四十九朵。', 10, .5, 3.625, 3.2, 5, 1.375, 1.8, 'PK01']},
    }


class ProductionTimingTests(unittest.TestCase):
    def setUp(self):
        self.tables = workbook()

    def codes(self):
        return {e['code'] for e in timing.validate(self.tables)['errors']}

    def test_default_route_and_reading_evidence_pass(self):
        self.assertEqual(self.codes(), set())
        self.assertEqual(layout.validate_delivery(self.tables)['errors'], [])

    def test_old_independent_shot_mode_is_rejected_by_default(self):
        self.tables['逐镜脚本'][6][8] = '生成模式：single_shot；每镜4—8秒'
        self.assertIn('TIMING_ROUTE', self.codes())

    def test_non_15_second_packages_and_prompt_totals_are_rejected(self):
        self.tables['逐镜脚本'][6][0] = 'PK01\n00:00—00:14'
        self.assertIn('TIMING_SEGMENT_SECONDS', self.codes())
        self.tables['逐镜脚本'][6][0] = 'PK01\n00:00—00:15'
        self.tables['逐镜脚本'][6][2] = self.tables['逐镜脚本'][6][2].replace('总计15秒', '总计16秒')
        self.assertIn('TIMING_PROMPT_SECONDS', self.codes())

    def test_missing_reading_evidence_is_not_excused_by_draft_status(self):
        del self.tables['朗读测时']
        self.tables['逐镜脚本'][6][8] += '；文字草稿待审核，尚未生成'
        self.assertIn('READING_MISSING', self.codes())

    def test_spoken_numbers_must_be_expanded_without_changing_source(self):
        self.tables['朗读测时'][6][1] = '共有1349朵。'
        self.assertIn('READING_NUMBERS', self.codes())

    def test_budget_below_estimate_or_test_reading_is_rejected(self):
        self.tables['原文与旁白'][6][2:4] = [2, 2]
        self.tables['朗读测时'][6][6] = 2
        self.assertIn('READING_BUDGET', self.codes())

    def test_missing_or_incorrect_rate_pause_count_and_estimate_fail(self):
        for row, index, value, expected in [(3, 1, 0, 'READING_RATE'), (6, 3, '', 'READING_NUMERIC'), (6, 2, 99, 'READING_COUNT'), (6, 4, 1, 'READING_ESTIMATE')]:
            with self.subTest(expected=expected):
                self.tables = workbook()
                self.tables['朗读测时'][row][index] = value
                self.assertIn(expected, self.codes())

    def test_reading_ids_must_match_original_complete_units(self):
        self.tables['朗读测时'][6][0] = 'AU02'
        self.assertIn('READING_IDS', self.codes())

    def test_original_text_cannot_be_empty_or_unrelated_to_spoken_text(self):
        self.tables['原文与旁白'][6][4] = ''
        self.assertIn('READING_SOURCE', self.codes())
        self.tables['原文与旁白'][6][4] = '这是一段完全不同的长句，必须保留并重新核算。'
        self.assertIn('READING_TEXT', self.codes())

    def test_recorded_margins_cannot_disagree_with_estimate_or_measurement(self):
        self.tables['朗读测时'][6][7:9] = [-999, -999]
        self.assertIn('READING_MARGIN', self.codes())

    def test_blank_rows_cannot_hide_later_overlapping_or_overlong_units(self):
        self.tables['原文与旁白'][7] = []
        self.tables['朗读测时'][7] = []
        self.tables['原文与旁白'][8] = ['AU02', 0, 100, 100, '共有1349朵。']
        self.tables['朗读测时'][8] = ['AU02', '共有一千三百四十九朵。', 10, .5, 3.625, 3.2, 100, 96.375, 96.8, 'PK01']
        self.assertIn('READING_WINDOW', self.codes())

    def test_voice_windows_cannot_overlap_or_exceed_film(self):
        self.tables['原文与旁白'][7] = ['AU02', 4, 9, 5, '共有一朵。']
        self.tables['朗读测时'][7] = ['AU02', '共有一朵。', 4, .5, 1.75, '', 5, 3.25, '', 'PK01']
        self.assertIn('READING_WINDOW', self.codes())

    def test_no_test_recording_is_estimate_only_not_fake_actual(self):
        self.tables['朗读测时'][6][5] = ''
        self.tables['朗读测时'][6][8] = ''
        report = timing.validate(self.tables)
        self.assertEqual(report['errors'], [])
        self.assertEqual(report['measurement_status'], 'estimate_only')

    def test_explicit_no_readable_text_is_allowed_but_not_with_source_units(self):
        self.tables['朗读测时'] = {3: ['朗读模式', '无朗读文本']}
        self.assertIn('READING_FALSE_SILENT', self.codes())
        del self.tables['原文与旁白']
        self.tables['逐镜脚本'][6][8] += '；无可朗读对白或旁白'
        self.assertEqual(self.codes(), set())

    def test_joint_delivery_default_rejects_old_flat_route_even_with_valid_prose(self):
        self.tables['逐镜脚本'][6][8] = 'single_shot'
        self.assertEqual(layout.validate(self.tables)['errors'], [])
        self.assertIn('TIMING_ROUTE', {e['code'] for e in layout.validate_delivery(self.tables)['errors']})


if __name__ == '__main__':
    unittest.main()
