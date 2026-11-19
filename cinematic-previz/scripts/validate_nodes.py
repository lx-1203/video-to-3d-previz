"""Check sentence nodes without rewriting source text or certifying its meaning."""
import argparse
import importlib.util
import json
from pathlib import Path
import sys

# Resolve the sibling by file path so direct CLI and external importlib loads agree.
SPEC = importlib.util.spec_from_file_location('node_sentence_rules', Path(__file__).with_name('validate_performance.py'))
RULES = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RULES)


def validate(data):
    errors = []

    def issue(code, path, message):
        errors.append({'code': code, 'path': path, 'message': message})

    if not isinstance(data, dict):
        issue('SCHEMA', '$', 'Expected an object')
        data = {}
    collections = [key for key in ('nodes', 'shots') if key in data]
    if len(collections) != 1:
        issue('SCHEMA', '$', 'Provide exactly one collection: nodes or shots')
    collection = collections[0] if collections else 'nodes'
    rows = data.get(collection)
    if not isinstance(rows, list) or not rows:
        issue('SCHEMA', collection, 'Expected a nonempty array of text nodes')
        rows = []
    id_key, text_key = ('id', 'text') if collection == 'nodes' else ('shot_id', 'narration')
    identifiers, texts = set(), []
    for index, row in enumerate(rows):
        path = f'{collection}.{index}'
        if not isinstance(row, dict):
            issue('SCHEMA', path, 'Expected an object')
            continue
        identifier = row.get(id_key)
        if not isinstance(identifier, str) or not identifier.strip():
            issue('SCHEMA', path + '.' + id_key, 'Expected a nonempty string identifier')
        elif identifier in identifiers:
            issue('SCHEMA', path + '.' + id_key, 'Identifiers must be unique')
        else:
            identifiers.add(identifier)
        text = row.get(text_key)
        errors.extend(RULES.validate_sentence(text, path + '.' + text_key))
        if isinstance(text, str):
            texts.append(text)
    if 'source_text' in data:
        source = data['source_text']
        if not isinstance(source, str):
            issue('SCHEMA', 'source_text', 'Expected a string')
        elif ''.join(texts) != source:
            issue('SOURCE_COVERAGE', collection, 'Concatenated node text must match source_text exactly, including punctuation and whitespace')
    return {
        'status': 'needs_revision' if errors else 'needs_review',
        'automatic_checks': 'failed' if errors else 'passed',
        'errors': errors,
        'semantic_review': {'status': 'pending', 'items': [
            'Punctuation boundaries do not prove semantic completeness; review sentence meaning and node continuity.']}
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('package', type=Path)
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding='utf-8')
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
