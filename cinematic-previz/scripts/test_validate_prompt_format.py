"""Reject flat prose and incomplete director prompt bodies before delivery."""
import unittest

import validate_prompt_format as prompt
import validate_excel_layout as layout


VALID = '''整体视频风格设定：16:9写实课堂，纸张与木桌保留自然材质，动作克制。
光源设定：左窗自然光照向桌面，光向持续一致。
光影系统：米白书页亮于深色桌面，人物衣装与背景分离。
【角色与场景定义】
1. 教师：中年，沿用同阶段人物的脸型与时代衣装，无镜内对白。
场景——教室：窗在左侧，书桌在中间，教材与记录本并列。
关键物品：教材、笔、记录本。
【角色比例强制约束】手、笔与教材大小合理，手指和纸面真实接触。
【角色状态特别说明】从翻页完成的状态继续，保持右手握笔，不重新翻页。
【具体情节描述】（按时间轴分镜，总计4秒）
第一阶段：0—4秒【核对记录】
◈ 运镜与画面描述：
【镜头①——手部近景，固定观察】（片段内0—4秒）
教师右手停在教材旁，先看向书页，再将笔尖落到记录本上，写完后抬笔。机位固定在桌侧，焦点保持在手和纸面所在平面，末幅保留清楚的落笔结果。
◈ 音效：
先保留纸张轻响，再出现落笔和书写声；无对白、无旁白、不铺配乐。
【全局补充（结尾）】
镜头逻辑与节奏：核对后记录，动作完成后短暂停留。
声音策略：只留纸页与笔触，保持安静的教室底声。
禁止项：换手、重复翻页、手指穿纸、突然改变光向。
结尾：停在笔尖抬起后的纸面，最后声音为笔触和底声，自然结束。
素材与执行说明：素材尚未绑定，未提交。
'''


def two_shots():
    prefix, story = VALID.split('第一阶段：', 1)
    phase, ending = story.split('【全局补充（结尾）】', 1)
    second = phase.replace('0—4秒', '4—8秒').replace('镜头①', '镜头②')
    return prefix.replace('总计4秒', '总计8秒') + '第一阶段：' + phase + '第二阶段：' + second + '【全局补充（结尾）】' + ending


class PromptFormatTests(unittest.TestCase):
    def codes(self, text, mode='auto'):
        return {e['code'] for e in prompt.validate(text, mode)['errors']}

    def test_structured_single_shot_and_explicit_unbound_assets_are_valid(self):
        self.assertEqual(self.codes(VALID, 'single_shot'), set())

    def test_old_flat_paragraph_is_rejected(self):
        self.assertIn('PROMPT_SECTION_MISSING', self.codes('16:9写实。教师核对后写字，固定近景，保留笔触声。'))

    def test_headings_with_empty_bodies_are_rejected(self):
        empty = '\n'.join(['整体视频风格设定：', '光源设定：', '光影系统：', '【角色与场景定义】', '【角色比例强制约束】', '【角色状态特别说明】', '【具体情节描述】', '【全局补充（结尾）】'])
        self.assertIn('PROMPT_SECTION_EMPTY', self.codes(empty))

    def test_section_order_and_duplicate_sections_are_rejected(self):
        self.assertIn('PROMPT_SECTION_ORDER', self.codes(VALID.replace('【角色比例强制约束】', 'TEMP').replace('【角色状态特别说明】', '【角色比例强制约束】').replace('TEMP', '【角色状态特别说明】')))
        self.assertIn('PROMPT_SECTION_DUPLICATE', self.codes(VALID + '\n【角色与场景定义】重复'))

    def test_unfilled_placeholders_are_rejected(self):
        self.assertIn('PROMPT_PLACEHOLDER', self.codes(VALID.replace('手部近景', '[景别]')))
        self.assertIn('PROMPT_PLACEHOLDER', self.codes(VALID.replace('教师右手停在教材旁', '同上')))
        self.assertIn('PROMPT_PLACEHOLDER', self.codes(VALID.replace('教师右手停在教材旁', '[完整段落：主体旁原生引用、具体表演与动作、光线材质、摄影机起落幅、结束状态]')))

    def test_every_phase_needs_its_own_audio(self):
        text = two_shots()
        location = text.rfind('◈ 音效：')
        self.assertIn('PROMPT_PHASE_AUDIO', self.codes(text[:location] + text[location:].replace('◈ 音效：', '声音另见片尾：', 1)))

    def test_shot_needs_actual_description_and_lens_camera_title(self):
        text = VALID.split('\n教师右手')[0] + '\n◈ 音效：' + VALID.split('\n◈ 音效：', 1)[1]
        self.assertIn('PROMPT_SHOT_EMPTY', self.codes(text))
        self.assertIn('PROMPT_SHOT_TITLE', self.codes(VALID.replace('手部近景，固定观察', '动态运镜')))

    def test_phase_and_shot_times_must_cover_declared_total(self):
        self.assertIn('PROMPT_PHASE_TIME', self.codes(VALID.replace('总计4秒', '总计6秒')))
        self.assertIn('PROMPT_SHOT_TIME', self.codes(VALID.replace('片段内0—4秒', '片段内1—4秒')))

    def test_multi_shot_is_not_forced_into_one_shot(self):
        self.assertEqual(self.codes(two_shots(), 'multi_shot'), set())
        self.assertIn('PROMPT_MODE', self.codes(VALID, 'multi_shot'))
        self.assertIn('PROMPT_MODE', self.codes(two_shots(), 'single_shot'))

    def test_shot_numbers_continue_across_phases_but_may_start_later(self):
        self.assertEqual(self.codes(VALID.replace('镜头①', '镜头⑲')), set())
        self.assertEqual(self.codes(VALID.replace('镜头①', '镜头１２')), set())
        self.assertIn('PROMPT_SHOT_SEQUENCE', self.codes(two_shots().replace('镜头②', '镜头①')))

    def test_ending_needs_rhythm_constraints_and_final_state(self):
        self.assertIn('PROMPT_ENDING_FIELD', self.codes(VALID.replace('禁止项：', '注意：')))
        self.assertIn('PROMPT_ENDING_FIELD', self.codes(VALID.replace('声音策略：', '声音另记：')))

    def test_workbook_delivery_command_checks_prompt_cells_not_only_layout(self):
        tables = {'逐镜脚本': {5: ['镜号与时间', '场次与人物阶段', '当前完整视频规划词', '首帧', '人物参考', '场景参考', '表情参考', '物品参考', '生成设置与状态', '审阅意见'], 6: ['SH01', '课堂', '旧版平铺描述']}, '提示词指导': {5: ['镜号与时间', *layout.GUIDANCE_HEADERS], 6: ['SH01', *['本镜设计'] * 6]}}
        self.assertEqual(layout.validate(tables)['errors'], [])
        report = layout.validate_delivery(tables, prompt_mode='single_shot', check_timing=False)
        self.assertEqual(report['status'], 'invalid')
        self.assertTrue(any(e.get('cell') == 'C6' and e['code'] == 'PROMPT_SECTION_MISSING' for e in report['errors']))
        tables['逐镜脚本'][6][2] = VALID
        self.assertEqual(layout.validate_delivery(tables, prompt_mode='single_shot', check_timing=False)['errors'], [])
        tables['逐镜脚本'] = {5: ['镜号与时间', '当前完整视频规划词'], 6: ['SH01', VALID]}
        self.assertIn('MAIN_HEADERS', {e['code'] for e in layout.validate_delivery(tables)['errors']})


if __name__ == '__main__':
    unittest.main()
