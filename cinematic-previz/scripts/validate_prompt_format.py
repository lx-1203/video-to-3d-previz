"""Read-only structure checks for director video prompts in TXT or saved XLSX.

Passing does not establish semantic quality, real asset bindings, user approval,
model support or successful generation. Review those separately.
"""
import argparse
import json
import math
import pathlib
import re
import xml.etree.ElementTree as ET
import zipfile

SECTIONS = ['整体视频风格设定：', '光源设定：', '光影系统：', '【角色与场景定义】', '【角色比例强制约束】', '【角色状态特别说明】', '【具体情节描述】', '【全局补充（结尾）】']
PHASE = re.compile(r'(?m)^\s*第[一二三四五六七八九十百\d]+阶段[:：]([^\n]*)')
SHOT = re.compile(r'(?m)^\s*【镜头(?P<number>\d+|[①-⑳])——(?P<title>[^】]+)】(?P<time>[^\n]*)')
TIME = re.compile(r'(\d+(?::\d+){0,2}(?:\.\d+)?)\s*[—–\-~至]\s*(\d+(?::\d+){0,2}(?:\.\d+)?)(?:\s*秒|(?=[）)；;]))')
PLACEHOLDER = re.compile(r'\[(?=[^\]\n]*(?:描述|角色|身高|状态|时长|起止|景别|运镜|完整段落|阶段|原生|比例|光线|禁止|最后|本片|本镜|对应))[^\]\n]+\]|<原生[^>]*引用>|同上|同前镜|待填写')


def bounds(text):
    match = TIME.search(text)
    if not match:
        return None
    def seconds(value):
        result = 0.0
        for part in value.split(':'):
            result = result * 60 + float(part)
        return result
    return tuple(seconds(v) for v in match.groups())


def covered(intervals, start, end):
    cursor = start
    for interval in intervals:
        if interval is None or not math.isclose(interval[0], cursor, abs_tol=1e-6) or interval[1] <= interval[0]:
            return False
        cursor = interval[1]
    return bool(intervals) and math.isclose(cursor, end, abs_tol=1e-6)


def validate(text, mode='auto'):
    errors = []
    def error(code, **details):
        errors.append(dict(code=code, **details))
    if not isinstance(text, str) or not text.strip():
        return {'status': 'invalid', 'errors': [{'code': 'PROMPT_EMPTY'}], 'shots': 0}
    # Workbook markdown previews may contain explicit line breaks.
    text = text.replace('<br>', '\n').replace('\r\n', '\n')
    markers = []
    for title in SECTIONS:
        pattern = re.escape(title).replace('：', '[:：]')
        found = list(re.finditer(r'(?m)^[ \t]*' + pattern, text))
        if not found:
            error('PROMPT_SECTION_MISSING', section=title)
        elif len(found) != 1:
            error('PROMPT_SECTION_DUPLICATE', section=title)
        else:
            markers.append((title, found[0]))
    if len(markers) != len(SECTIONS):
        return {'status': 'invalid', 'errors': errors, 'shots': 0}
    if [m.start() for _, m in markers] != sorted(m.start() for _, m in markers):
        error('PROMPT_SECTION_ORDER')
        return {'status': 'invalid', 'errors': errors, 'shots': 0}
    bodies = {title: text[m.end():markers[i + 1][1].start() if i + 1 < len(markers) else len(text)].strip() for i, (title, m) in enumerate(markers)}
    for title, body in bodies.items():
        if not re.search(r'[\w\u4e00-\u9fff]', body):
            error('PROMPT_SECTION_EMPTY', section=title)
    if PLACEHOLDER.search(text):
        error('PROMPT_PLACEHOLDER', value=PLACEHOLDER.search(text)[0])
    story = bodies['【具体情节描述】']
    total_match = re.search(r'总计\s*(\d+(?:\.\d+)?)\s*秒', story)
    total = float(total_match[1]) if total_match else None
    if total is None or not math.isfinite(total) or total <= 0:
        error('PROMPT_TOTAL')
    phases = list(PHASE.finditer(story))
    if not phases:
        error('PROMPT_PHASE_MISSING')
    phase_times, numbers = [], []
    for i, phase in enumerate(phases):
        phase_times.append(bounds(phase[1]))
        body = story[phase.end():phases[i + 1].start() if i + 1 < len(phases) else len(story)]
        picture = list(re.finditer(r'(?m)^\s*◈\s*运镜与画面描述[:：]', body))
        audio = list(re.finditer(r'(?m)^\s*◈\s*音效[:：]', body))
        shots = list(SHOT.finditer(body))
        if len(picture) != 1 or (shots and picture[0].end() > shots[0].start()):
            error('PROMPT_PHASE_PICTURE', phase=i + 1)
        if len(audio) != 1 or not body[audio[0].end():].strip() or (shots and audio[0].start() < shots[-1].end()):
            error('PROMPT_PHASE_AUDIO', phase=i + 1)
        if not shots:
            error('PROMPT_SHOT_MISSING', phase=i + 1)
        shot_times = []
        for j, shot in enumerate(shots):
            number = shot['number']
            numbers.append(int(number) if number.isdecimal() else ord(number) - ord('①') + 1)
            shot_times.append(bounds(shot['time']))
            if len(re.split(r'[，,]', shot['title'])) < 2:
                error('PROMPT_SHOT_TITLE', shot=number)
            end = shots[j + 1].start() if j + 1 < len(shots) else audio[0].start() if audio else len(body)
            if not body[shot.end():end].strip():
                error('PROMPT_SHOT_EMPTY', shot=number)
        if phase_times[-1] is None or not covered(shot_times, *phase_times[-1]):
            error('PROMPT_SHOT_TIME', phase=i + 1)
    if total is not None and not covered(phase_times, 0, total):
        error('PROMPT_PHASE_TIME')
    if numbers and (numbers[0] < 1 or numbers != list(range(numbers[0], numbers[0] + len(numbers)))):
        error('PROMPT_SHOT_SEQUENCE')
    if mode not in {'auto', 'single_shot', 'multi_shot', 'full_film'} or (mode == 'single_shot' and len(numbers) != 1) or (mode == 'multi_shot' and len(numbers) < 2):
        error('PROMPT_MODE', mode=mode, shots=len(numbers))
    ending = bodies['【全局补充（结尾）】'].split('素材与执行说明：', 1)[0]
    for field in ['镜头逻辑与节奏', '声音策略', '禁止项', '结尾']:
        if not re.search(re.escape(field) + r'[:：][^\n\S]*\S[^\n]*', ending):
            error('PROMPT_ENDING_FIELD', field=field)
    return {'status': 'invalid' if errors else 'valid', 'errors': errors, 'shots': len(numbers), 'phases': len(phases), 'total_seconds': total, 'manual_review': 'Still required: shot meaning and prose density, performance/camera causality, local real asset bindings, sound policy and final state. Structure is not execution readiness.'}


def validate_workbook(tables, mode='auto'):
    from validate_excel_layout import ID_HEADERS, PROMPT_HEADERS
    errors, count = [], 0
    rows = tables.get('逐镜脚本', {})
    header = next((r for r, v in sorted(rows.items()) if v and v[0] in ID_HEADERS), None)
    if header is None:
        return {'status': 'invalid', 'errors': [{'code': 'PROMPT_HEADER_MISSING'}], 'prompts': 0}
    columns = [i for i, title in enumerate(rows[header]) if title in PROMPT_HEADERS]
    if len(columns) != 1:
        return {'status': 'invalid', 'errors': [{'code': 'PROMPT_COLUMN_MISSING'}], 'prompts': 0}
    column = columns[0]
    letters, value = '', column + 1
    while value:
        value, digit = divmod(value - 1, 26)
        letters = chr(65 + digit) + letters
    for row, values in sorted(rows.items()):
        if row <= header or not values or not str(values[0]).strip():
            continue
        count += 1
        report = validate(values[column] if len(values) > column else '', mode)
        errors.extend(dict(e, sheet='逐镜脚本', cell=f'{letters}{row}', shot_id=str(values[0]).splitlines()[0]) for e in report['errors'])
    if count == 0:
        errors.append({'code': 'PROMPTS_EMPTY'})
    return {'status': 'invalid' if errors else 'valid', 'errors': errors, 'prompts': count, 'scope': 'Saved prompt body structure and timeline only; manual semantic and readiness checks remain required.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input', type=pathlib.Path)
    parser.add_argument('--mode', choices=['auto', 'single_shot', 'multi_shot', 'full_film'], default='auto')
    args = parser.parse_args()
    try:
        if args.input.suffix.lower() == '.xlsx':
            from validate_excel_layout import read_xlsx
            report = validate_workbook(read_xlsx(args.input), args.mode)
        else:
            report = validate(args.input.read_text(encoding='utf-8-sig'), args.mode)
    except (OSError, ValueError, KeyError, IndexError, ET.ParseError, zipfile.BadZipFile) as exc:
        print(json.dumps({'status': 'unreadable', 'error': str(exc)}, ensure_ascii=True))
        return 2
    print(json.dumps(report, ensure_ascii=True, indent=2))
    return 0 if not report['errors'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
