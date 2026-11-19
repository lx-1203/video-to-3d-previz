"""Read-only checks for internal links, rule ownership and example timing."""
import argparse
import json
import math
import pathlib
import re
import urllib.parse

from read_modules import headings, load_catalog, ModuleError
from validate_duration_limits import validate as validate_duration


def outside_code(text):
    result, fence = [], None
    for line in text.splitlines():
        match = re.match(r'^ {0,3}(`{3,}|~{3,})(.*)$', line)
        if fence:
            if match and match[1][0] == fence[0] and len(match[1]) >= fence[1] and not match[2].strip():
                fence = None
            result.append('')
        elif match:
            fence = (match[1][0], len(match[1]))
            result.append('')
        else:
            result.append(line)
    return '\n'.join(result)


def anchors(text):
    counts, result = {}, set()
    for line, _, _ in headings(text):
        title = re.sub(r'^#+\s+', '', line).rstrip('#').strip()
        title = re.sub(r'\[([^]]+)\]\([^)]*\)', r'\1', title)
        slug = re.sub(r'[^\w\- ]', '', title.lower()).replace(' ', '-')
        count = counts.get(slug, 0)
        counts[slug] = count + 1
        result.add(slug if not count else slug + '-' + str(count))
    return result


def documents(root):
    return sorted(p for p in root.rglob('*.md')
                  if not any(part.startswith('.') for part in p.relative_to(root).parts))


def check_links(root):
    root = root.resolve()
    errors = []
    for path in documents(root):
        text = outside_code(path.read_text(encoding='utf-8-sig'))
        for match in re.finditer(r'!?\[[^\]\n]*\]\(([^)\n]+)\)', text):
            href = match[1].strip().strip('<>')
            url = urllib.parse.urlsplit(href)
            if url.scheme or url.netloc:
                continue
            target = (path.parent / urllib.parse.unquote(url.path)).resolve() if url.path else path
            where = {'file': path.relative_to(root).as_posix(), 'link': href}
            if not target.is_relative_to(root):
                errors.append(dict(where, code='LINK_OUTSIDE'))
            elif not target.exists():
                errors.append(dict(where, code='LINK_FILE'))
            elif url.fragment and target.suffix == '.md':
                if urllib.parse.unquote(url.fragment) not in anchors(target.read_text(encoding='utf-8-sig')):
                    errors.append(dict(where, code='LINK_ANCHOR'))
    return errors


def check_example_timing(name, text):
    errors = []
    kept_total = 0
    multi_shot = '<!-- generation_mode: multi_shot -->' in text
    lines = outside_code(text).splitlines()
    for i, line in enumerate(lines):
        if [x.strip() for x in line.strip('|').split('|')] != [
                'segment_id', 'generation_seconds', 'edit_seconds']:
            continue
        for row in lines[i + 2:]:
            if not row.startswith('|'):
                break
            values = [x.strip() for x in row.strip('|').split('|')]
            try:
                segment, generated, kept = values
                generated, kept = float(generated), float(kept)
            except ValueError:
                errors.append({'code': 'EXAMPLE_FORMAT', 'file': name, 'row': row})
                continue
            if not math.isfinite(generated) or not math.isfinite(kept):
                errors.append({'code': 'EXAMPLE_NUMBER', 'file': name, 'segment': segment})
                continue
            kept_total += kept
            if (multi_shot and not math.isclose(generated, 15, abs_tol=1e-6)) or (not multi_shot and not 4 <= generated <= 8):
                errors.append({'code': 'EXAMPLE_DURATION', 'file': name, 'segment': segment})
            if not 0 < kept <= generated:
                errors.append({'code': 'EXAMPLE_EDIT', 'file': name, 'segment': segment})
    declared = re.search(r'<!--\s*edit_total_seconds:\s*(\d+(?:\.\d+)?)\s*-->', text)
    if declared and not math.isclose(kept_total, float(declared[1]), abs_tol=1e-6):
        errors.append({'code': 'EXAMPLE_TOTAL', 'file': name,
                       'declared': float(declared[1]), 'actual': kept_total})
    return errors


def check_project_example(text):
    """Use the actual generation-plan interface rather than a parallel summary."""
    match = re.search(r'```json\s*\n(.*?)\n```', text, re.S)
    if not match:
        return [{'code': 'EXAMPLE_JSON', 'message': 'Project JSON is missing'}]
    try:
        data = json.loads(match[1])
    except ValueError as exc:
        return [{'code': 'EXAMPLE_JSON', 'message': str(exc)}]
    if not isinstance(data, dict):
        return [{'code': 'EXAMPLE_JSON', 'message': 'Expected project object'}]
    errors = [{'code': 'EXAMPLE_JSON', 'detail': e} for e in validate_duration(data, 4, 8)['errors']]
    if errors:
        return errors
    ids = {s['segment_id'] for s in data['generation_segments']}
    for shot in data.get('shots', []):
        if not set(shot.get('segments', [])).issubset(ids):
            errors.append({'code': 'EXAMPLE_REFERENCE', 'shot': shot.get('shot_id')})
    for edge in data.get('handoffs', []):
        if not {edge.get('from'), edge.get('to')}.issubset(ids):
            errors.append({'code': 'EXAMPLE_REFERENCE', 'edge': edge})
    return errors


def validate(root):
    errors = check_links(root)
    try:
        state = load_catalog(root)
        owners = state['catalog'].get('rule_sources', {})
        for key in ['duration', 'sentence', 'sound', 'approval', 'prompt_execution', 'handoff', 'budget', 'delivery']:
            owner = owners.get(key, {})
            body = state['bodies'].get(owner.get('module'))
            if not body or owner.get('section') not in {h[0] for h in headings(body['content'])}:
                errors.append({'code': 'RULE_SOURCE', 'rule': key})
    except (ModuleError, OSError, ValueError) as exc:
        errors.append({'code': 'CATALOG', 'message': str(exc)})
    for name in ['storyboard-example.md', 'production-contract.md']:
        path = root / 'references' / name
        if path.exists():
            text = path.read_text(encoding='utf-8-sig')
            if '| segment_id | generation_seconds | edit_seconds |' not in text:
                errors.append({'code': 'EXAMPLE_PLAN_MISSING', 'file': name})
            errors.extend(check_example_timing(name, text))
            if name == 'production-contract.md':
                errors.extend(dict(e, file=name) for e in check_project_example(text))
    return {'status': 'valid' if not errors else 'invalid', 'errors': errors,
            'limits': 'Links, declared ownership, timing tables and project JSON; semantic and media review remain separate.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=pathlib.Path, default=pathlib.Path(__file__).resolve().parent.parent)
    args = parser.parse_args()
    report = validate(args.root.resolve())
    print(json.dumps(report, ensure_ascii=True, indent=2))
    return 0 if not report['errors'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
