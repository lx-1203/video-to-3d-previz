#!/usr/bin/env python3
"""Maintainer-only release manifest builder. Never use this to hide local install edits."""
import hashlib
import json
from pathlib import Path
import re
from check_update import validate_manifest, validate_contents


def main():
    root = Path(__file__).resolve().parents[1]
    if not (root / '.git').exists():
        raise SystemExit('Run from the maintenance Git repository, not an installed skill.')
    paths = [root / 'SKILL.md', root / 'agents/openai.yaml', root / 'update-source.json',
             root / 'scripts/check_update.py']
    paths.extend(p for p in (root / 'references').rglob('*') if p.is_file())
    data = {p.relative_to(root).as_posix(): p.read_bytes() for p in sorted(paths)}
    version = re.search(r'^\s+version:\s*"(\d+\.\d+\.\d+)"', data['SKILL.md'].decode(), re.M)[1]
    manifest = {'schema_version': 1, 'skill_name': 'video-to-3d-previz', 'version': version,
                'files': {p: {'sha256': hashlib.sha256(b).hexdigest(), 'size': len(b)} for p, b in data.items()}}
    validate_manifest(manifest)
    validate_contents(data, manifest, json.loads(data['update-source.json']))
    (root / 'release-manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8', newline='\n')
    print(json.dumps({'version': version, 'files': len(data), 'manifest': 'release-manifest.json'}))


if __name__ == '__main__':
    main()
