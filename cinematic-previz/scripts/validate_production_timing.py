"""Read-only checks of package timing and recorded reading budgets."""
import math
import re


def number(value):
    if value is None or value == '' or isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def cell(row, column):
    return row[column] if len(row) > column else ''


def body(table, header_names):
    header = next((r for r, v in sorted(table.items()) if v and v[0] in header_names), None)
    if header is None:
        return []
    result = []
    for r, values in sorted(table.items()):
        if r <= header:
            continue
        if not values or not str(values[0]).strip():
            continue
        if values[0] in {'合计', '旁白预算合计'}:
            break
        result.append((r, values))
    return result


def interval(text):
    match = re.search(r'(\d+(?::\d+){0,2}(?:\.\d+)?)\s*[—–\-~至]\s*(\d+(?::\d+){0,2}(?:\.\d+)?)', str(text))
    if not match:
        return None
    result = []
    for item in match.groups():
        seconds = 0.0
        for part in item.split(':'):
            seconds = seconds * 60 + float(part)
        result.append(seconds)
    return tuple(result)


def validate(tables, segment_seconds=15, mode='multi_shot'):
    from validate_excel_layout import ID_HEADERS
    from validate_prompt_format import validate as validate_prompt
    errors = []
    def error(code, **details):
        errors.append(dict(code=code, **details))
    if mode not in ('multi_shot', 'single_shot') or number(segment_seconds) is None or float(segment_seconds) <= 0:
        return {'status': 'invalid', 'errors': [{'code': 'TIMING_POLICY'}]}
    segments, cursor = [], 0.0
    for row, values in body(tables.get('逐镜脚本', {}), ID_HEADERS):
        bounds = interval(values[0])
        if bounds is None or not math.isclose(bounds[0], cursor, abs_tol=1e-6) or not math.isclose(bounds[1] - bounds[0], float(segment_seconds), abs_tol=1e-6):
            error('TIMING_SEGMENT_SECONDS', row=row, expected=segment_seconds)
        if bounds:
            cursor = bounds[1]
            segments.append({'id': str(values[0]).splitlines()[0], 'begin': bounds[0], 'end': bounds[1]})
        if mode not in str(cell(values, 8)) or (mode == 'multi_shot' and 'single_shot' in str(cell(values, 8))):
            error('TIMING_ROUTE', row=row, expected=mode)
        prompt = validate_prompt(str(cell(values, 2)), mode)
        if prompt.get('total_seconds') is None or not math.isclose(prompt['total_seconds'], float(segment_seconds), abs_tol=1e-6):
            error('TIMING_PROMPT_SECONDS', row=row, expected=segment_seconds)
        if mode == 'multi_shot' and prompt.get('shots', 0) < 2:
            error('TIMING_ROUTE', row=row, message='Keep the internal shots inside one package')
    if not segments:
        error('TIMING_SEGMENTS_MISSING')
    readings = tables.get('朗读测时')
    if readings is None:
        error('READING_MISSING')
        return {'status': 'invalid', 'errors': errors, 'segments': len(segments)}
    units = body(tables.get('原文与旁白', {}), {'发声单元'})
    silent = cell(readings.get(3, []), 0) == '朗读模式' and cell(readings.get(3, []), 1) == '无朗读文本'
    measured_count, measured_total, estimated_total, budget_total = 0, 0.0, 0.0, 0.0
    if silent:
        if units or body(readings, {'发声单元'}):
            error('READING_FALSE_SILENT')
    else:
        rate = number(cell(readings.get(3, []), 1))
        if rate is None or rate <= 0:
            error('READING_RATE')
        recorded = body(readings, {'发声单元'})
        source_ids = [str(v[0]) for _, v in units]
        read_ids = [str(v[0]) for _, v in recorded]
        if not units or not recorded or source_ids != read_ids or len(set(source_ids)) != len(source_ids):
            error('READING_IDS')
        voice_cursor = 0.0
        for (source_row, source), (read_row, reading) in zip(units, recorded):
            identifier = str(source[0])
            source_text = str(cell(source, 4)).strip()
            spoken = str(cell(reading, 1))
            if not source_text:
                error('READING_SOURCE', unit=identifier)
            elif not re.search(r'[A-Za-z]', source_text):
                # Number readings may change; the other original words must remain.
                plain_spoken = re.sub(r'[^\w\u4e00-\u9fff]', '', spoken)
                location = 0
                for part in re.split(r'\d+(?:\.\d+)?%?', source_text):
                    words = re.sub(r'[^\w\u4e00-\u9fff]', '', part)
                    if not words:
                        continue
                    found = plain_spoken.find(words, location)
                    if found < 0:
                        error('READING_TEXT', unit=identifier)
                        break
                    location = found + len(words)
            count, pause, estimate, measured, budget = [number(cell(reading, i)) for i in [2, 3, 4, 5, 6]]
            start, end, reserved = [number(cell(source, i)) for i in [1, 2, 3]]
            if re.search(r'\d', spoken):
                error('READING_NUMBERS', unit=identifier)
            if not spoken.strip() or count is None or count <= 0 or pause is None or pause < 0 or estimate is None or budget is None:
                error('READING_NUMERIC', unit=identifier)
                continue
            han = len(re.findall(r'[\u3400-\u9fff]', spoken))
            if han and not re.search(r'[A-Za-z]', spoken) and count != han:
                error('READING_COUNT', unit=identifier)
            if rate is not None and rate > 0 and not math.isclose(estimate, count / rate + pause, abs_tol=.011):
                error('READING_ESTIMATE', unit=identifier)
            if cell(reading, 5) not in ('', None) and (measured is None or measured <= 0):
                error('READING_NUMERIC', unit=identifier, field='test_read_seconds')
            if budget <= 0 or budget + .001 < estimate or (measured is not None and budget + .001 < measured):
                error('READING_BUDGET', unit=identifier)
            estimate_margin, test_margin = number(cell(reading, 7)), number(cell(reading, 8))
            if estimate_margin is None or not math.isclose(estimate_margin, budget - estimate, abs_tol=.011):
                error('READING_MARGIN', unit=identifier, field='estimate_margin')
            if measured is not None and (test_margin is None or not math.isclose(test_margin, budget - measured, abs_tol=.011)):
                error('READING_MARGIN', unit=identifier, field='test_margin')
            if start is None or end is None or reserved is None or start < voice_cursor or end <= start or end > cursor + .001 or not math.isclose(end - start, reserved, abs_tol=.001) or not math.isclose(reserved, budget, abs_tol=.001):
                error('READING_WINDOW', unit=identifier, source_row=source_row)
            else:
                voice_cursor = end
                expected = {s['id'] for s in segments if s['begin'] < end and s['end'] > start}
                actual = set(re.split(r'[、,，\s]+', str(cell(reading, 9)).strip()))
                if expected != actual:
                    error('READING_COVERAGE', unit=identifier, reading_row=read_row)
            estimated_total += estimate
            budget_total += budget
            if measured is not None and measured > 0:
                measured_count += 1
                measured_total += measured
    status = 'not_applicable' if silent else 'estimate_only' if not measured_count else 'local_reference_measured' if measured_count == len(units) else 'partial_reference_measured'
    return {'status': 'invalid' if errors else 'valid', 'errors': errors, 'segments': len(segments), 'segment_seconds': segment_seconds, 'mode': mode, 'planned_final_seconds': cursor, 'speech_units': len(units), 'estimated_read_seconds': round(estimated_total, 3), 'reference_read_seconds': round(measured_total, 3) if measured_count else None, 'reading_budget_seconds': round(budget_total, 3), 'measurement_status': status, 'limits': 'Checks recorded planning values; authentic recording, natural performance, final voice and platform support still require verification.'}
