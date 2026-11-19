"""Read-only checks of declared execution links and local file hashes, never visual approval."""
import argparse
import hashlib
import io
import json
from pathlib import Path
import re
import sys

ROLES = {'characters': 'character', 'expressions': 'expression', 'scenes': 'scene', 'props': 'prop'}


class Check:
    def __init__(self, base_dir):
        self.root = Path(base_dir or '.').resolve()
        self.errors = []

    def issue(self, code, path, message):
        self.errors.append({'code': code, 'path': path, 'message': message})

    def obj(self, value, path):
        if not isinstance(value, dict):
            self.issue('SCHEMA', path, 'Expected an object')
            return {}
        return value

    def rows(self, value, path, nonempty=False):
        if not isinstance(value, list) or (nonempty and not value):
            self.issue('SCHEMA', path, 'Expected a nonempty array' if nonempty else 'Expected an array')
            return []
        return value

    def text(self, value, path):
        if not isinstance(value, str) or not value.strip():
            self.issue('SCHEMA', path, 'Expected a nonempty string')
            return ''
        return value

    def sha(self, value, path):
        if not isinstance(value, str) or not re.fullmatch('[0-9a-fA-F]{64}', value):
            self.issue('SCHEMA', path, 'Expected a SHA256 hex string')
            return ''
        return value.lower()

    def artifact(self, value, path):
        item = self.obj(value, path)
        name = self.text(item.get('file'), path + '.file')
        expected = self.sha(item.get('sha256'), path + '.sha256')
        if name:
            try:
                source = self.root / name
                if not source.is_file() or source.stat().st_size == 0:
                    raise ValueError('File is missing, not regular, or empty')
                value = hashlib.sha256()
                with source.open('rb') as stream:
                    for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                        value.update(chunk)
                actual = value.hexdigest()
                if expected and actual != expected:
                    raise ValueError('File no longer matches its selected SHA256')
            except (OSError, ValueError) as error:
                self.issue('FILE', path, str(error))
        return expected

    def unique(self, value, path, keys):
        result = {}
        for i, raw in enumerate(self.rows(value, path)):
            label = f'{path}.{i}'
            row = self.obj(raw, label)
            key = tuple(self.text(row.get(k), label + '.' + k) for k in keys)
            if key in result:
                self.issue('SCHEMA', label, 'Duplicate ID or binding')
            result[key] = row
        return result

    def fresh(self, shot, path):
        actors = self.unique(shot.get('actors'), path + '.actors', ('person_id', 'variant_id'))
        refs = self.obj(shot.get('references'), path + '.references')
        selected, bindings = {}, {role: set() for role in ROLES.values()}
        for category, expected_role in list(ROLES.items()) + [('auxiliary', None)]:
            allowed = {expected_role} if expected_role else {'layout', 'state', 'edit_target'}
            rows = refs.get(category, [] if category == 'auxiliary' else None)
            for i, raw in enumerate(self.rows(rows, path + '.references.' + category)):
                label = f'{path}.references.{category}.{i}'
                ref = self.obj(raw, label)
                aid = self.text(ref.get('asset_id'), label + '.asset_id')
                version = self.text(ref.get('version'), label + '.version')
                role = ref.get('role')
                if not isinstance(role, str) or role not in allowed:
                    self.issue('SCHEMA', label + '.role', 'Role must match its category; auxiliary uses layout, state or edit_target')
                    role = ''
                sha = self.artifact(ref, label)
                key = (aid, version)
                if key in selected:
                    self.issue('SCHEMA', label, 'Reference asset/version must be unique across categories')
                selected[key] = (role, sha)
                if ref.get('review_status') != 'passed':
                    self.issue('REFERENCE_REVIEW', label, 'Selected reference needs actual image review')
                if role in ('character', 'expression'):
                    binding = tuple(self.text(ref.get(k), label + '.' + k) for k in ('person_id', 'variant_id'))
                    bindings[role].add(binding)
                    if binding not in actors:
                        self.issue('CHARACTER_REFERENCE' if role == 'character' else 'EXPRESSION_REFERENCE', label,
                                   'Reference belongs to a person/variant absent from this shot')
        inputs = self.unique(shot.get('inputs'), path + '.inputs', ('asset_id', 'version'))
        attached = {}
        for key, item in inputs.items():
            label = path + '.inputs.' + '/'.join(key)
            role = self.text(item.get('role'), label + '.role')
            sha = self.artifact(item, label)
            attached[key] = (role, sha)
        if attached != selected:
            self.issue('ATTACHMENT', path + '.inputs', 'Input asset IDs, versions, roles and hashes must exactly match selected references')
        for binding, actor in actors.items():
            label = path + '.actors.' + '/'.join(binding)
            if binding not in bindings['character']:
                self.issue('CHARACTER_REFERENCE', label, 'Missing selected character reference for current person and variant')
            for key in ('face_visible', 'expression_required'):
                if type(actor.get(key)) is not bool:
                    self.issue('SCHEMA', label + '.' + key, 'Expected a boolean')
            if actor.get('expression_required') is True and binding not in bindings['expression']:
                self.issue('EXPRESSION_REFERENCE', label, 'Required expression must be selected for the same person and variant')
            if actor.get('expression_required') is False and not (
                    isinstance(actor.get('expression_reason'), str) and actor['expression_reason'].strip()):
                self.issue('EXPRESSION_DECISION', label, 'Explain why no independent expression reference is needed')
            if actor.get('face_visible') is True:
                cues = self.obj(actor.get('facial_cues'), label + '.facial_cues')
                visible = {k for k in ('eyes', 'brows', 'mouth') if isinstance(cues.get(k), str) and cues[k].strip()}
                if len(visible) < 2 or not (isinstance(actor.get('gaze'), str) and actor['gaze'].strip()):
                    self.issue('PERFORMANCE_CUES', label, 'Visible face needs a gaze target and at least two explicit facial channels; semantics still need review')

    def continuation(self, shot, path):
        source = self.artifact(shot.get('source_clip'), path + '.source_clip')
        index = shot.get('frame_index')
        if type(index) is not int or index < 0:
            self.issue('SCHEMA', path + '.frame_index', 'Expected a 0-based nonnegative decoded frame index')
        tail = self.artifact(shot.get('tail_frame'), path + '.tail_frame')
        actual = self.artifact(shot.get('input_frame'), path + '.input_frame')
        if tail and actual and tail != actual:
            self.issue('CONTINUE_INPUT', path, 'Continue must use the unchanged selected real tail frame')
        proof = self.obj(shot.get('receipt'), path + '.receipt')
        self.artifact(proof, path + '.receipt')
        receipt_file = proof.get('file')
        if not isinstance(receipt_file, str) or not receipt_file.strip():
            return
        try:
            receipt = json.loads((self.root / receipt_file).read_text(encoding='utf-8-sig'))
            if not isinstance(receipt, dict):
                raise ValueError('Frame-bridge receipt must be an object')
            count, selected = receipt.get('decoded_frame_count'), receipt.get('frame_index')
            if (type(receipt.get('schema_version')) is not int or receipt.get('schema_version') != 1 or
                    type(count) is not int or type(selected) is not int or
                    not 0 <= selected < count or selected != index or
                    receipt.get('source_sha256') != source or receipt.get('image_sha256') != tail):
                raise ValueError('Frame-bridge receipt does not bind this source, tail image and decoded frame index')
            for key in ('source_video', 'image'):
                self.text(receipt.get(key), path + '.receipt.' + key)
        except (OSError, ValueError) as error:
            self.issue('CONTINUE_RECEIPT', path + '.receipt', str(error))

    def delivery(self, data, shots):
        output = self.artifact(data.get('output'), 'output')
        clips, clip_versions = {}, {}
        for sid, shot in shots.items():
            clip = self.obj(shot.get('clip'), 'shots.' + sid[0] + '.clip')
            clip_versions[sid[0]] = self.text(clip.get('version'), 'shots.' + sid[0] + '.clip.version')
            clips[sid[0]] = self.artifact(clip, 'shots.' + sid[0] + '.clip')
        plan = self.unique(data.get('planned_transitions'), 'planned_transitions', ('transition_id',))
        executed = self.unique(data.get('transitions'), 'transitions', ('transition_id',))
        if set(plan) != set(executed):
            self.issue('TRANSITION_COVERAGE', 'transitions', 'Every current planned transition needs exactly one execution record')
        for tid, design in plan.items():
            for key in ('version', 'from_shot', 'to_shot', 'instructions'):
                self.text(design.get(key), 'planned_transitions.' + tid[0] + '.' + key)
        for tid, transition in executed.items():
            path = 'transitions.' + tid[0]
            version = self.text(transition.get('version'), path + '.version')
            expected = plan.get(tid, {}).get('version')
            if version != expected:
                self.issue('TRANSITION_STALE', path, 'Execution record does not match the current design version')
            source = self.text(transition.get('from_shot'), path + '.from_shot')
            target = self.text(transition.get('to_shot'), path + '.to_shot')
            if source not in clips or target not in clips:
                self.issue('TRANSITION_EXECUTION', path, 'Transition must reference current selected shot clips')
            self.text(transition.get('instructions'), path + '.instructions')
            if any(transition.get(key) != plan.get(tid, {}).get(key)
                   for key in ('from_shot', 'to_shot', 'instructions')):
                self.issue('TRANSITION_STALE', path, 'Endpoints or edit instructions changed since this execution record')
            execution = self.obj(transition.get('execution'), path + '.execution')
            if execution.get('status') != 'done':
                self.issue('TRANSITION_EXECUTION', path, 'Generated clips do not prove the transition edit/composite was executed')
            actual_version = self.text(execution.get('version'), path + '.execution.version')
            from_version = self.text(execution.get('from_version'), path + '.execution.from_version')
            to_version = self.text(execution.get('to_version'), path + '.execution.to_version')
            actual_source = self.sha(execution.get('from_sha256'), path + '.execution.from_sha256')
            actual_target = self.sha(execution.get('to_sha256'), path + '.execution.to_sha256')
            actual_output = self.sha(execution.get('output_sha256'), path + '.execution.output_sha256')
            if (actual_version != version or from_version != clip_versions.get(source) or
                    to_version != clip_versions.get(target) or actual_source != clips.get(source) or
                    actual_target != clips.get(target) or actual_output != output):
                self.issue('TRANSITION_STALE', path, 'Design, selected source clips or final export changed since execution')
            review = self.obj(execution.get('review'), path + '.execution.review')
            reviewed_output = self.sha(review.get('output_sha256'), path + '.execution.review.output_sha256')
            if review.get('status') != 'passed' or reviewed_output != output:
                self.issue('TRANSITION_REVIEW', path, 'Current export requires an actual playback review record')
            self.artifact(review.get('evidence'), path + '.execution.review.evidence')


def check_video_prompt(check, data):
    """Compare one saved platform draft with its approved workbook source."""
    from validate_excel_layout import ID_HEADERS, PROMPT_HEADERS, read_xlsx
    from validate_production_timing import body, cell
    import xml.etree.ElementTree as ET
    import zipfile

    package = check.text(data.get('package_id'), 'package_id')
    approval = check.obj(data.get('approval'), 'approval')
    approved = approval.get('package_ids')
    if (not package or not isinstance(approved, list) or package not in approved
            or not check.text(approval.get('evidence'), 'approval.evidence')):
        check.issue('PROMPT_NOT_APPROVED', 'approval', 'Record actual approval covering this package and workbook version')
        return
    workbook = check.obj(approval.get('workbook'), 'approval.workbook')
    name = check.text(workbook.get('file'), 'approval.workbook.file')
    expected = check.sha(workbook.get('sha256'), 'approval.workbook.sha256')
    if check.errors:
        return
    try:
        raw = (check.root / name).read_bytes()
        if hashlib.sha256(raw).hexdigest() != expected:
            check.issue('PROMPT_SOURCE_STALE', 'approval.workbook', 'Workbook differs from the approved version')
            return
        tables = read_xlsx(io.BytesIO(raw), literal_only=True)
    except (OSError, ValueError, IndexError, KeyError, ET.ParseError, zipfile.BadZipFile):
        check.issue('PROMPT_SOURCE_UNREADABLE', 'approval.workbook', 'Cannot read the approved workbook')
        return
    main = tables.get('逐镜脚本', {})
    header = next((v for _, v in sorted(main.items()) if v and v[0] in ID_HEADERS), [])
    columns = [i for i, title in enumerate(header) if title in PROMPT_HEADERS]
    rows = body(main, ID_HEADERS) if len(columns) == 1 else []
    matches = [cell(values, columns[0]) for _, values in rows
               if str(values[0]).splitlines()[0].strip() == package]
    if len(matches) != 1 or not matches[0].strip():
        check.issue('PROMPT_SOURCE_MISSING', 'approval.workbook', 'Require exactly one complete source cell for this package')
        return
    node = check.text(data.get('target_node_id'), 'target_node_id')
    saved = check.obj(data.get('readback'), 'readback')
    if not node or saved.get('node_id') != node or saved.get('complete') is not True or not isinstance(saved.get('text'), str):
        check.issue('PROMPT_READBACK_UNAVAILABLE', 'readback', 'Require complete saved text from the target platform node')
    elif saved['text'] != matches[0]:
        check.issue('PROMPT_READBACK_MISMATCH', 'readback.text', 'Pause this package and compare against the approved source')


def validate(data, *, stage='first_frame', base_dir=None):
    check = Check(base_dir)
    data = check.obj(data, '$')
    if type(data.get('schema_version')) is not int or data.get('schema_version') != 1:
        check.issue('SCHEMA', 'schema_version', 'Supported schema_version is integer 1')
    if stage == 'video_prompt':
        if not check.errors:
            check_video_prompt(check, data)
        return {'status': 'blocked' if check.errors else 'matched', 'errors': check.errors,
                'authorizes_submission': False,
                'evidence_scope': 'Local approved workbook and declared platform readback; no live observation'}
    check.rows(data.get('shots'), 'shots', nonempty=True)
    shots = check.unique(data.get('shots'), 'shots', ('shot_id',))
    if stage == 'first_frame':
        for sid, shot in shots.items():
            path = 'shots.' + sid[0]
            if shot.get('mode') == 'fresh':
                check.fresh(shot, path)
            elif shot.get('mode') == 'continue':
                check.continuation(shot, path)
            else:
                check.issue('SCHEMA', path + '.mode', 'Use fresh or continue')
    elif stage == 'delivery':
        check.delivery(data, shots)
    else:
        check.issue('SCHEMA', 'stage', 'Use first_frame, video_prompt or delivery')
    return {'status': 'needs_revision' if check.errors else 'needs_visual_review',
            'automatic_checks': 'failed' if check.errors else 'passed', 'errors': check.errors,
            'authorizes_submission': False, 'visual_review': 'not_run',
            'evidence_scope': 'Declared links and local file hashes only; no tool observation or pixel validation'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('package', type=Path)
    parser.add_argument('--stage', required=True, choices=('first_frame', 'video_prompt', 'delivery'))
    args = parser.parse_args()
    try:
        data = json.loads(args.package.read_text(encoding='utf-8-sig'))
    except (OSError, ValueError) as error:
        report = validate(None, stage=args.stage)
        report['errors'] = [{'code': 'INPUT', 'path': '$', 'message': str(error)}]
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 2
    report = validate(data, stage=args.stage, base_dir=args.package.resolve().parent)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 2 if any(e['code'] == 'SCHEMA' for e in report['errors']) else (1 if report['errors'] else 0)


if __name__ == '__main__':
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    sys.exit(main())
