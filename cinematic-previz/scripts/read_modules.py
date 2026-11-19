"""Read selected skill modules and their dependencies. Standard library, read-only."""
import argparse
import hashlib
import json
import pathlib
import re
import sys


class ModuleError(ValueError):
    pass


def strings(value, label, nonempty=False):
    if not isinstance(value, list) or any(not isinstance(x, str) or not x.strip() for x in value):
        raise ModuleError(label + ' must be a list of nonempty strings')
    if nonempty and not value:
        raise ModuleError(label + ' cannot be empty')
    if len(set(value)) != len(value):
        raise ModuleError(label + ' contains duplicates')
    return value


def headings(text):
    """Find ATX headings outside fenced code, with exact source offsets."""
    found, fence, offset = [], None, 0
    for line in text.splitlines(keepends=True):
        fence_match = re.match(r'^ {0,3}([\x60]{3,}|~{3,})(.*)$', line.rstrip('\r\n'))
        if fence:
            if (fence_match and fence_match[1][0] == fence[0]
                    and len(fence_match[1]) >= fence[1] and not fence_match[2].strip()):
                fence = None
        elif fence_match:
            fence = (fence_match[1][0], len(fence_match[1]))
        else:
            match = re.match(r'^(#{1,6})[ \t]+\S', line)
            if match:
                found.append((line.strip(), len(match[1]), offset))
        offset += len(line)
    return found


def section_text(text, selectors, path):
    entries = headings(text)
    chunks = []
    for selector in selectors:
        matches = [(i, item) for i, item in enumerate(entries) if item[0] == selector]
        if not matches:
            raise ModuleError('missing section ' + selector + ' in ' + path)
        if len(matches) != 1:
            raise ModuleError('ambiguous section ' + selector + ' in ' + path)
        index, (_, level, start) = matches[0]
        end = next((pos for _, other_level, pos in entries[index + 1:] if other_level <= level), len(text))
        chunks.append(text[start:end].strip())
    return '\n\n'.join(chunks) + '\n'


def load_catalog(root):
    root = pathlib.Path(root).resolve()
    raw = (root / 'module-index.json').read_bytes()
    catalog = json.loads(raw.decode('utf-8-sig'))
    if not isinstance(catalog, dict) or type(catalog.get('version')) is not int or catalog['version'] != 1:
        raise ModuleError('unsupported catalog version')
    items = catalog.get('nodes')
    if not isinstance(items, list) or not items:
        raise ModuleError('nodes must be a nonempty list')
    nodes = {}
    for item in items:
        if not isinstance(item, dict):
            raise ModuleError('node must be an object')
        node_id = item.get('id')
        if not isinstance(node_id, str) or not re.fullmatch(r'[a-z][a-z0-9.-]*', node_id):
            raise ModuleError('invalid node id')
        if node_id in nodes:
            raise ModuleError('duplicate node ' + node_id)
        if not isinstance(item.get('title'), str) or not item['title'].strip():
            raise ModuleError('missing title for ' + node_id)
        strings(item.get('requires', []), node_id + '.requires')
        nodes[node_id] = item
    root_id = catalog.get('root')
    if not isinstance(root_id, str) or root_id not in nodes:
        raise ModuleError('missing catalog root')
    for node_id, item in nodes.items():
        if 'parent' not in item:
            raise ModuleError('missing parent field for ' + node_id)
        parent = item['parent']
        if node_id == root_id:
            if parent is not None:
                raise ModuleError('root must have null parent')
        elif not isinstance(parent, str) or parent not in nodes:
            raise ModuleError('missing parent for ' + node_id)
        for dependency in item.get('requires', []):
            if dependency not in nodes:
                raise ModuleError('missing dependency ' + dependency)
    required = strings(catalog.get('required'), 'required', nonempty=True)
    for node_id in required:
        if node_id not in nodes or not nodes[node_id].get('source'):
            raise ModuleError('required node needs a source: ' + node_id)

    visited, visiting = set(), set()
    def visit(node_id):
        if node_id in visiting:
            raise ModuleError('parent/dependency cycle at ' + node_id)
        if node_id in visited:
            return
        visiting.add(node_id)
        item = nodes[node_id]
        edges = ([item['parent']] if item['parent'] is not None else []) + item.get('requires', [])
        for edge in edges:
            visit(edge)
        visiting.remove(node_id)
        visited.add(node_id)
    for node_id in nodes:
        visit(node_id)

    bodies, files = {}, {}
    for node_id, item in nodes.items():
        if 'source' not in item:
            continue
        source = item['source']
        if not isinstance(source, dict) or not isinstance(source.get('path'), str):
            raise ModuleError('invalid source for ' + node_id)
        name = source['path']
        relative = pathlib.PurePosixPath(name)
        if not name or relative.is_absolute() or '\\' in name or ':' in name or '..' in relative.parts:
            raise ModuleError('source must stay inside skill: ' + name)
        target = (root / name).resolve()
        if not target.is_relative_to(root) or not target.is_file():
            raise ModuleError('missing or outside source: ' + name)
        if name not in files:
            data = target.read_bytes()
            files[name] = (data.decode('utf-8-sig'), hashlib.sha256(data).hexdigest())
        text, fingerprint = files[name]
        if 'sections' in source:
            selectors = strings(source['sections'], node_id + '.sections', nonempty=True)
            if any(not re.match(r'^#{1,6} ', s) for s in selectors):
                raise ModuleError('sections must be exact ATX headings: ' + node_id)
            content = section_text(text, selectors, name)
        else:
            content = text
        bodies[node_id] = {'id': node_id, 'title': item['title'], 'source': name,
                           'sections': source.get('sections'), 'source_sha256': fingerprint,
                           'content': content}
    return {'catalog': catalog, 'nodes': nodes, 'bodies': bodies,
            'catalog_sha256': hashlib.sha256(raw).hexdigest()}


def read_modules(state, requested, max_chars=None):
    requested = list(dict.fromkeys(requested))
    for node_id in requested:
        if node_id not in state['nodes']:
            raise ModuleError('unknown module ' + node_id + '; use tree to discover IDs')
        if node_id not in state['bodies']:
            children = [n['id'] for n in state['nodes'].values() if n['parent'] == node_id]
            raise ModuleError(node_id + ' is a category; choose children: ' + ', '.join(children))
    seen, modules = set(), []
    def add(node_id):
        if node_id in seen:
            return
        item = state['nodes'][node_id]
        if item['parent'] is not None:
            add(item['parent'])
        for dependency in item.get('requires', []):
            add(dependency)
        if node_id in state['bodies']:
            modules.append(state['bodies'][node_id])
        seen.add(node_id)
    for node_id in state['catalog']['required'] + requested:
        add(node_id)
    count = sum(len(module['content']) for module in modules)
    if max_chars is not None and (max_chars <= 0 or count > max_chars):
        raise ModuleError('content budget exceeded: need ' + str(count) + ' chars; no partial output')
    return {'status': 'loaded', 'requested': requested, 'catalog_sha256': state['catalog_sha256'],
            'content_chars': count, 'modules': modules}


def tree_nodes(state, node_id):
    if node_id not in state['nodes']:
        raise ModuleError('unknown tree node ' + node_id)
    result = []
    def add(current, depth):
        item = state['nodes'][current]
        result.append({'id': current, 'title': item['title'], 'parent': item['parent'], 'depth': depth,
                       'readable': current in state['bodies'], 'requires': item.get('requires', [])})
        for child in state['nodes'].values():
            if child['parent'] == current:
                add(child['id'], depth + 1)
    add(node_id, 0)
    return result


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    tree = commands.add_parser('tree', help='list the classification tree, without rule bodies')
    tree.add_argument('node', nargs='?')
    read = commands.add_parser('read', help='read modules, ancestors and cross-branch dependencies')
    read.add_argument('modules', nargs='+')
    read.add_argument('--max-chars', type=int, help='rule-content character limit, not tokens; never truncate')
    check = commands.add_parser('check', help='validate the complete catalog, graph, files and sections')
    for command in (tree, read, check):
        command.add_argument('--json', action='store_true')
    args = parser.parse_args(argv)
    try:
        state = load_catalog(pathlib.Path(__file__).resolve().parent.parent)
        if args.command == 'tree':
            data = {'nodes': tree_nodes(state, args.node or state['catalog']['root'])}
            output = json.dumps(data, ensure_ascii=False, indent=2) if args.json else '\n'.join(
                '  ' * node['depth'] + node['id'] + ' — ' + node['title'] for node in data['nodes'])
        elif args.command == 'check':
            data = {'status': 'valid', 'node_count': len(state['nodes']),
                    'readable_count': len(state['bodies']), 'catalog_sha256': state['catalog_sha256']}
            output = json.dumps(data, ensure_ascii=False, indent=2)
        else:
            data = read_modules(state, args.modules, args.max_chars)
            output = json.dumps(data, ensure_ascii=False, indent=2) if args.json else '\n\n'.join(
                '# ' + module['id'] + ' — ' + module['title'] + '\nSource: ' + module['source']
                + '\nSHA256: ' + module['source_sha256'] + '\n\n' + module['content'] for module in data['modules'])
    except (ModuleError, OSError, UnicodeError, json.JSONDecodeError) as error:
        print('module-reader: ' + str(error), file=sys.stderr)
        return 2
    print(output)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
