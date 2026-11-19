"""Update only the bridge's managed MCP table, preserving all other config text."""
from pathlib import Path
import argparse
import copy
import json
import os
import re
import shutil
import time
import tomllib

NAME = 'updream-node-helper'
MARKER = '# Managed by Updream MCP Bridge'

def update_config(text, launch):
    command, entry = launch.get('command'), launch.get('entry')
    if not isinstance(command, str) or not command or not isinstance(entry, str) or not entry:
        raise ValueError('Invalid local launch configuration')
    before = tomllib.loads(text)
    block = (f'[mcp_servers.{NAME}]\n{MARKER}\n'
             f'command = {json.dumps(command, ensure_ascii=False)}\n'
             f'args = [{json.dumps(entry, ensure_ascii=False)}]\n'
             'startup_timeout_sec = 40\n'
             'tool_timeout_sec = 240\n')
    if NAME in before.get('mcp_servers', {}):
        match = re.search(r'(?m)^\[mcp_servers\.updream-node-helper\]\s*\n', text)
        if not match:
            raise ValueError('Existing MCP table is not managed by this project')
        next_table = re.search(r'(?m)^\[', text[match.end():])
        end = match.end() + next_table.start() if next_table else len(text)
        if MARKER not in text[match.end():end]:
            raise ValueError('Existing MCP table is not managed by this project')
        updated = text[:match.start()] + block + ('\n' if end < len(text) else '') + text[end:]
    else:
        updated = text + ('\n' if text and not text.endswith('\n') else '') + '\n' + block
    after = tomllib.loads(updated)
    previous, current = copy.deepcopy(before), copy.deepcopy(after)
    previous.get('mcp_servers', {}).pop(NAME, None)
    current.get('mcp_servers', {}).pop(NAME, None)
    # Adding the first server also adds its parent table; normalize empty tables.
    if previous.get('mcp_servers') == {}:
        previous.pop('mcp_servers')
    if current.get('mcp_servers') == {}:
        current.pop('mcp_servers')
    if previous != current:
        raise ValueError('Refusing to change settings outside the bridge MCP table')
    return updated

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--node', type=Path, required=True)
    parser.add_argument('--entry', type=Path, required=True)
    parser.add_argument('--config', type=Path, default=Path(os.environ.get('CODEX_HOME', str(Path.home() / '.codex'))) / 'config.toml')
    args = parser.parse_args()
    if not args.node.is_file() or not args.entry.is_file():
        raise ValueError('Node executable and stdio entry must exist')
    existed = args.config.exists()
    def read_current():
        if not args.config.exists():
            return ''
        with args.config.open('r', encoding='utf-8', newline='') as handle:
            return handle.read()
    original = read_current()
    updated = update_config(original, {'command': str(args.node.resolve()), 'entry': str(args.entry.resolve())})
    if updated != original:
        args.config.parent.mkdir(parents=True, exist_ok=True)
        if args.config.exists():
            backup = args.config.with_name(args.config.name + f'.updream-backup-{time.time_ns()}')
            shutil.copy2(args.config, backup)
        temporary = args.config.with_name(args.config.name + f'.updream-tmp-{time.time_ns()}')
        with temporary.open('x', encoding='utf-8', newline='') as handle:
            handle.write(updated)
        if args.config.exists() != existed or read_current() != original:
            temporary.unlink()
            raise ValueError('Codex config changed during setup; refusing to overwrite concurrent edits')
        os.replace(temporary, args.config)
    print(f'Configured {NAME} in {args.config}')

if __name__ == '__main__':
    main()
