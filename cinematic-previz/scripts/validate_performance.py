"""Check declared performance plans; never certify model or acting quality."""
import argparse
import json
import math
from pathlib import Path
import sys

ABSTRACT_WORDS = ('难过', '开心', '生气', '委屈', '悲伤', '激动', '复杂', '深情', '痛苦', '撒娇', '求证', '强撑')
FACE_CHANNELS = {'eyes', 'brows', 'mouth'}
VOICE_KEYS = {'volume', 'rate', 'pause', 'airiness', 'ending'}
MODES = {'on_screen', 'off_screen', 'voiceover', 'system'}
SENTENCE_ENDINGS = frozenset('。？！.!?')
SENTENCE_CLOSERS = '\"\'”’」』）》】〕〉›»)]}'


def validate_sentence(value, path):
    """Check one punctuated sentence, not semantic completeness; never alter text."""
    if not isinstance(value, str) or not value.strip():
        return [{'code': 'SCHEMA', 'path': path, 'message': 'Expected a nonempty string'}]
    body = value.rstrip().rstrip(SENTENCE_CLOSERS + ' \t\r\n')
    count, last_end, index = 0, -1, 0
    while index < len(body):
        if body[index] not in SENTENCE_ENDINGS:
            index += 1
            continue
        end = index + 1
        while end < len(body) and body[end] in SENTENCE_ENDINGS:
            end += 1
        punctuation = body[index:end]
        decimal = (punctuation == '.' and end < len(body) and body[end].isdigit()
                   and (index == 0 or body[index - 1].isdigit()
                        or body[index - 1].isspace() or body[index - 1] in '+-([（='))
        ellipsis = len(punctuation) > 1 and set(punctuation) == {'.'}
        if not decimal and not ellipsis:
            count += 1
            last_end = end
        index = end
    errors = []
    if last_end != len(body) or not any(char.isalnum() for char in body):
        errors.append({'code': 'SENTENCE_BOUNDARY', 'path': path,
                       'message': 'End each spoken unit with 。？！.!?; fragments, ellipses and dashes require revision or human review. Do not add punctuation to the source automatically.'})
    if count > 1:
        errors.append({'code': 'SENTENCE_COUNT', 'path': path,
                       'message': 'Use exactly one punctuated sentence per spoken unit; preserve source text when separating sentences.'})
    return errors


def finite_number(value):
    return type(value) in (int, float) and math.isfinite(value)


def validate(data):
    errors = []

    def issue(code, path, message):
        errors.append({'code': code, 'path': path, 'message': message})

    def obj(value, path):
        if not isinstance(value, dict):
            issue('SCHEMA', path, 'Expected an object')
            return {}
        return value

    def items(value, path):
        if not isinstance(value, list):
            issue('SCHEMA', path, 'Expected an array')
            return []
        return value

    def text(value, path):
        if not isinstance(value, str) or not value.strip():
            issue('SCHEMA', path, 'Expected a nonempty string')
            return ''
        return value

    def observable(value, path):
        value = text(value, path)
        for word in ABSTRACT_WORDS:
            if word in value:
                issue('ABSTRACT_WORD', path, 'Replace the intent label with visible or audible behavior: ' + word)
        return value

    data = obj(data, '$')
    if type(data.get('schema_version')) is not int or data.get('schema_version') != 1:
        issue('SCHEMA', 'schema_version', 'Supported schema_version is 1')
    source = obj(data.get('source'), 'source')
    text(source.get('intent'), 'source.intent')
    shot = obj(data.get('shot'), 'shot')
    duration = shot.get('duration_seconds')
    valid_duration = finite_number(duration) and duration > 0
    if not valid_duration:
        issue('TIMELINE', 'shot.duration_seconds', 'Duration must be finite and positive')
    for key in ('id', 'ratio', 'framing', 'camera', 'style_profile'):
        text(shot.get(key), 'shot.' + key)
    trigger = obj(data.get('trigger'), 'trigger')
    observable(trigger.get('event'), 'trigger.event')
    if trigger.get('source') not in ('provided', 'inferred'):
        issue('SCHEMA', 'trigger.source', 'Use provided or inferred')
    items(data.get('inferences'), 'inferences')
    caps = obj(data.get('capabilities'), 'capabilities')
    for key in ('negative_prompt', 'audio'):
        if caps.get(key) is not None and type(caps[key]) is not bool:
            issue('SCHEMA', 'capabilities.' + key, 'Use true, false or null')
    if type(caps.get('verified')) is not bool:
        issue('SCHEMA', 'capabilities.verified', 'Use a boolean')
    limit = caps.get('max_seconds')
    if limit is not None and (not finite_number(limit) or limit <= 0):
        issue('SCHEMA', 'capabilities.max_seconds', 'Use a positive duration or null')
        limit = None

    actors = {}
    for i, raw in enumerate(items(data.get('actors'), 'actors')):
        actor = obj(raw, 'actors.' + str(i))
        aid = text(actor.get('id'), 'actors.' + str(i) + '.id')
        channels = items(actor.get('visible_channels'), 'actors.' + str(i) + '.visible_channels')
        visible = {text(c, 'actors.' + str(i) + '.visible_channels') for c in channels}
        if aid in actors:
            issue('SCHEMA', 'actors', 'Actor IDs must be unique')
        actors[aid] = visible
    if not actors:
        issue('SCHEMA', 'actors', 'At least one actor is required')

    def actor_ref(value, path):
        aid = text(value, path)
        if aid not in actors:
            issue('ACTOR_REFERENCE', path, 'Actor ID is not declared')
        return aid

    def interval(row, path):
        start, end = row.get('start'), row.get('end')
        if not (finite_number(start) and finite_number(end)):
            issue('TIMELINE', path, 'Times must be finite numbers, not booleans')
            return False
        if start < 0 or end <= start or (valid_duration and end > duration + 1e-6):
            issue('TIMELINE', path, 'Interval is outside the shot or has no duration')
            return False
        return True

    def timeline(value, path, minimum):
        rows = [obj(r, path + '.' + str(i)) for i, r in enumerate(items(value, path))]
        if len(rows) < minimum:
            issue('TIMELINE', path, 'Insufficient timed units')
        previous = 0
        ids = set()
        for i, row in enumerate(rows):
            label = path + '.' + str(i)
            rid = text(row.get('id'), label + '.id')
            if rid in ids:
                issue('SCHEMA', label + '.id', 'IDs must be unique')
            ids.add(rid)
            if interval(row, label):
                if abs(row['start'] - previous) > 1e-6:
                    issue('TIMELINE', label, 'Intervals must cover the shot without gaps or overlap')
                previous = row['end']
        if valid_duration and abs(previous - duration) > 1e-6:
            issue('TIMELINE', path, 'Timeline must end at the shot duration')
        return rows

    beats = timeline(data.get('beats'), 'beats', 2)
    observed = {aid: set() for aid in actors}
    action_ids = set()
    action_owners = {}
    for i, beat in enumerate(beats):
        path = 'beats.' + str(i)
        observable(beat.get('trigger'), path + '.trigger')
        action = beat.get('primary_action')
        if not isinstance(action, dict):
            issue('PRIMARY_ACTION', path, 'Declare one primary_action object per beat')
        else:
            aid = actor_ref(action.get('actor_id'), path + '.primary_action.actor_id')
            action_id = text(action.get('id'), path + '.primary_action.id')
            if action_id in action_owners and action_owners[action_id] != aid:
                issue('PRIMARY_ACTION', path, 'One action ID cannot change actor')
            action_owners[action_id] = aid
            action_ids.add(action_id)
            observable(action.get('text'), path + '.primary_action.text')
        for j, raw in enumerate(items(beat.get('cues'), path + '.cues')):
            cue_path = path + '.cues.' + str(j)
            cue = obj(raw, cue_path)
            aid = actor_ref(cue.get('actor_id'), cue_path + '.actor_id')
            channel = text(cue.get('channel'), cue_path + '.channel')
            observable(cue.get('text'), cue_path + '.text')
            if aid in actors:
                if channel not in actors[aid]:
                    issue('VISIBILITY', cue_path, 'Cue is not visible in this shot')
                else:
                    observed[aid].add(channel)
    for aid, available in actors.items():
        required = available & FACE_CHANNELS
        if len(required) < 2:
            required = available
        if len(observed[aid] & required) < min(2, len(required)):
            issue('OBSERVABLE_CUES', 'actors.' + aid, 'Insufficient declared visible parts; revise framing or cues')

    original = items(source.get('dialogue'), 'source.dialogue')
    dialogue = [obj(r, 'dialogue.' + str(i)) for i, r in enumerate(items(data.get('dialogue'), 'dialogue'))]
    original_lines = []
    for i, raw in enumerate(original):
        line = obj(raw, 'source.dialogue.' + str(i))
        errors.extend(validate_sentence(line.get('text'), 'source.dialogue.' + str(i) + '.text'))
        original_lines.append((line.get('speaker_id'), line.get('text'), line.get('mode')))
    compiled_lines = []
    spoken = []
    previous_speech_start = -1
    for i, line in enumerate(dialogue):
        path = 'dialogue.' + str(i)
        aid = actor_ref(line.get('speaker_id'), path + '.speaker_id')
        content = text(line.get('text'), path + '.text')
        if content:
            errors.extend(validate_sentence(content, path + '.text'))
        mode = line.get('mode')
        if not isinstance(mode, str) or mode not in MODES:
            issue('SCHEMA', path + '.mode', 'Unknown dialogue mode')
        if interval(line, path):
            if line['start'] < previous_speech_start - 1e-6:
                issue('DIALOGUE_ORDER', path, 'Dialogue start times must follow the original line order')
            previous_speech_start = line['start']
        voice = obj(line.get('voice'), path + '.voice')
        controls = {key: value for key, value in voice.items() if key in VOICE_KEYS and isinstance(value, str) and value.strip()}
        if len(controls) < 2:
            issue('VOICE', path + '.voice', 'Supply at least two concrete voice controls')
        for key, value in controls.items():
            observable(value, path + '.voice.' + key)
        compiled_lines.append((aid, content, mode))
        spoken.append(f'{aid}，{mode}，{line.get("start")}至{line.get("end")}秒；语音参数：' + json.dumps(controls, ensure_ascii=False) + f'；原文台词：“{content}”')
    if original_lines != compiled_lines:
        issue('DIALOGUE_CHANGED', 'dialogue', 'Preserve exact source text, order, speaker and mode')

    segments = timeline(data.get('segments'), 'segments', 1)
    for i, segment in enumerate(segments):
        path = 'segments.' + str(i)
        start_state = observable(segment.get('start_state'), path + '.start_state')
        observable(segment.get('end_state'), path + '.end_state')
        if segment.get('handoff') != ('start' if i == 0 else 'continue'):
            issue('CONTINUITY', path, 'This package describes one shot; later segments use continue')
        if i and start_state != segments[i - 1].get('end_state'):
            issue('CONTINUITY', path, 'Continuation must inherit the preceding planned end state')
        if caps.get('verified') is True and limit is not None and interval(segment, path):
            if segment['end'] - segment['start'] > limit + 1e-6:
                issue('SEGMENT_LIMIT', path, 'Segment exceeds the verified model duration limit')
        start, end = segment.get('start'), segment.get('end')
        if not (finite_number(start) and finite_number(end) and end > start):
            continue
        spoken_units = 0
        for j, line in enumerate(dialogue):
            speech_start, speech_end = line.get('start'), line.get('end')
            if not (finite_number(speech_start) and finite_number(speech_end) and speech_end > speech_start):
                continue
            if speech_start < end - 1e-6 and speech_end > start + 1e-6:
                spoken_units += 1
                if speech_start < start - 1e-6 or speech_end > end + 1e-6:
                    issue('DIALOGUE_SPLIT', path, 'Segment boundary splits dialogue.' + str(j) + '; keep the entire spoken sentence in one segment')
        if spoken_units > 1:
            issue('SEGMENT_SENTENCES', path, 'Each segment may contain at most one spoken sentence; use separate segments within the same shot')
    prompt = obj(data.get('positive_prompt'), 'positive_prompt')
    action_prompt = observable(prompt.get('action'), 'positive_prompt.action')
    negatives = items(data.get('negative_prompt'), 'negative_prompt')
    if not negatives:
        issue('SCHEMA', 'negative_prompt', 'Provide targeted avoidances; transport depends on tool capability')
    for i, negative in enumerate(negatives):
        text(negative, 'negative_prompt.' + str(i))
    return {
        'status': 'needs_revision' if errors else 'needs_review',
        'automatic_checks': 'failed' if errors else 'passed',
        'errors': errors,
        'counts': {'beats': len(beats), 'declared_primary_actions': len(action_ids), 'visible_cue_parts': {a: len(c) for a, c in observed.items()}},
        'negative_transport': 'native' if caps.get('verified') is True and caps.get('negative_prompt') is True else 'constraints_only',
        'audio_transport': 'native' if caps.get('verified') is True and caps.get('audio') is True else 'separate_or_unverified',
        'compiled_positive_prompt': (action_prompt + ('\n' + '\n'.join(spoken) if spoken and caps.get('verified') is True and caps.get('audio') is True else '')) if not errors else None,
        'dialogue_plan': spoken,
        'semantic_review': {'status': 'pending', 'items': ['event and performance causality', 'prompt matches timed beats and visible framing', 'one main intention per beat and natural reactions', 'speech duration and dialogue-free visual readability', 'negative constraints do not forbid intended acting']},
        'video_review': 'not_run'
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('package', type=Path)
    args = parser.parse_args()
    try:
        data = json.loads(args.package.read_text(encoding='utf-8-sig'))
    except (OSError, ValueError) as error:
        print(json.dumps({'status': 'invalid_input', 'errors': [{'code': 'INPUT', 'message': str(error)}]}, ensure_ascii=False))
        return 2
    report = validate(data)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 1 if report['errors'] else 0


if __name__ == '__main__':
    sys.exit(main())
