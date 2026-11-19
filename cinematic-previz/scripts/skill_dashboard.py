#!/usr/bin/env python3
"""Local live viewer for skill status, module reading order and source documents."""
import argparse
from datetime import datetime, timezone
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
from pathlib import Path, PurePosixPath
import re
import socket
import subprocess
import sys
import threading
from urllib.parse import parse_qs, urlsplit
from urllib.request import urlopen
import webbrowser

import read_modules
from sync_from_project import snapshot, source_folder


ASSETS = Path(__file__).resolve().parent.parent / 'assets' / 'dashboard'
TEXT_EXTENSIONS = {'.md', '.json', '.py', '.html', '.css', '.js', '.bat', '.yaml', '.toml'}
PRESETS = [
    ('storyboard', '完整分镜', '查看导演分镜连同所需专业分支', ['storyboard']),
    ('prompt', '提示词编写', '正文格式、时长、声音和审核边界', ['prompts']),
    ('frame', '人物与首帧', '人物阶段、表情、物品和首帧输入', ['previs.start']),
    ('video', '生成视频', '生成规则、输入和能力检查；接力按需追加', ['generation']),
    ('classroom', '课堂方法', '六类情境及60项镜头索引', ['camera.classroom']),
    ('camera', '单项运镜', '以焦点转移为例，只带入必要依赖', ['camera.focus']),
    ('maintenance', '技能维护', '同步门禁及运行维护规则', ['runtime']),
]


def now():
    return datetime.now(timezone.utc).isoformat()


def file_hashes(root):
    return {name: hashlib.sha256(data).hexdigest() for name, data in snapshot(Path(root)).items()}


def fingerprint(hashes):
    return hashlib.sha256(json.dumps(hashes, sort_keys=True).encode()).hexdigest()


def differences(current, other):
    return [{'path': name, 'kind': 'only_local' if name not in other else 'only_other' if name not in current else 'modified'}
            for name in sorted(current.keys() | other.keys()) if current.get(name) != other.get(name)]


def git(root, *args, timeout=10):
    options = {'cwd': root, 'stdout': subprocess.PIPE, 'stderr': subprocess.PIPE, 'timeout': timeout, 'check': True}
    if sys.platform == 'win32':
        options['creationflags'] = subprocess.CREATE_NO_WINDOW
    return subprocess.run(['git', *args], **options).stdout


class DashboardStore:
    def __init__(self, root):
        self.root = Path(root).resolve()
        if not (self.root / 'SKILL.md').is_file():
            raise ValueError('The selected directory does not contain SKILL.md')
        self.remote_lock = threading.Lock()
        self.cache_path = self.root / '.local' / 'dashboard' / 'remote-check.json'
        try:
            cache = json.loads(self.cache_path.read_text(encoding='utf-8'))
            self.remote = cache if isinstance(cache, dict) and 'status' in cache else {'status': 'unchecked'}
        except (OSError, ValueError):
            self.remote = {'status': 'unchecked'}

    def source(self):
        registration = self.root / '.skill-source.json'
        if not registration.exists():
            return self.root, None
        data = json.loads(registration.read_text(encoding='utf-8-sig'))
        if (not isinstance(data, dict) or data.get('schema') != 1 or
                not isinstance(data.get('files'), dict) or not isinstance(data.get('source'), str) or
                not isinstance(data.get('project_root'), str) or not isinstance(data.get('revision'), str)):
            raise ValueError('INVALID_STATE: unsupported local update manifest')
        if (any(not isinstance(k, str) or not isinstance(v, str) or not re.fullmatch(r'[0-9a-f]{64}', v)
                for k,v in data['files'].items()) or fingerprint(data['files']) != data['revision']):
            raise ValueError('INVALID_STATE: inconsistent registered content fingerprint')
        source = Path(data['source']).resolve()
        if source_folder(Path(data['project_root']).resolve()) != source:
            raise ValueError('INVALID_STATE: registered source does not match its project')
        if not (source / 'SKILL.md').is_file():
            raise ValueError('The registered skill source is missing; it is not a standalone installation')
        return source, data

    def trigger_rules(self):
        path = self.root / 'references/reading-triggers.json'
        if not path.exists():
            return {'tasks': {}, 'conditions': []}
        data = json.loads(path.read_text(encoding='utf-8-sig'))
        if (not isinstance(data, dict) or data.get('version') != 1 or
                not isinstance(data.get('tasks'), dict) or not isinstance(data.get('conditions'), list)):
            raise ValueError('Invalid reading trigger configuration')
        ids = set()
        for key, task in data['tasks'].items():
            if not isinstance(task, dict) or any(not isinstance(task.get(k), str) or not task[k].strip()
                                                for k in ('when','action','gate','source')):
                raise ValueError('Invalid task trigger: ' + str(key))
        for condition in data['conditions']:
            if (not isinstance(condition, dict) or any(not isinstance(condition.get(k), str) or not condition[k].strip()
                                                     for k in ('id','title','when','source')) or condition['id'] in ids):
                raise ValueError('Invalid or duplicate optional trigger')
            ids.add(condition['id'])
            read_modules.strings(condition.get('tasks'), 'trigger.tasks', nonempty=True)
            read_modules.strings(condition.get('modules'), 'trigger.modules', nonempty=True)
        return data

    def state(self):
        contents = snapshot(self.root)
        hashes = {name: hashlib.sha256(data).hexdigest() for name, data in contents.items()}
        revision = fingerprint(hashes)
        source, local = self.root, {'installed_revision': revision, 'differences': []}
        try:
            source, registration = self.source()
            source_hashes = hashes if source == self.root else file_hashes(source)
            source_revision = fingerprint(source_hashes)
            changes = differences(hashes, source_hashes)
            status = ('standalone' if registration is None else 'needs_sync' if changes else
                      'metadata_stale' if registration.get('revision') != revision else 'current')
            local.update(status=status, source_revision=source_revision,
                         registered_revision=registration.get('revision') if registration else None, differences=changes)
        except (OSError, ValueError, KeyError, TypeError) as error:
            local.update(status='error', error=str(error))
            source_revision = None
        remote = dict(self.remote)
        if remote.get('status') in ('verified', 'different') and (
                remote.get('basis_revision') != source_revision or remote.get('source_root') != str(source)):
            remote['previous_status'] = remote['status']
            remote['status'] = 'stale'
        entry = contents['SKILL.md'].decode('utf-8-sig')
        title = re.search(r'^# (.+)$', entry, re.M)
        version = re.search(r'^\s+version:\s*[\'"]?([^\s\'"]+)', entry, re.M)
        name = re.search(r'^name:\s*(.+)$', entry, re.M)
        try:
            state = read_modules.load_catalog(self.root)
            nodes = [dict(n, readable=n['id'] in state['bodies']) for n in state['nodes'].values()]
            catalog = {'nodes': nodes, 'root': state['catalog']['root'], 'required': state['catalog']['required'], 'sha256': state['catalog_sha256']}
        except (ValueError, OSError, UnicodeError) as error:
            catalog = {'nodes': [], 'required': [], 'root': None, 'error': str(error)}
        files = []
        for path, data in contents.items():
            if PurePosixPath(path).suffix.lower() not in TEXT_EXTENSIONS:
                continue
            try:
                text = data.decode('utf-8-sig')
                modified = datetime.fromtimestamp((self.root / path).stat().st_mtime, timezone.utc).isoformat()
            except (UnicodeError, OSError):
                continue
            heading = re.search(r'^# (.+)$', text, re.M)
            files.append({'path': path, 'title': heading[1] if heading else PurePosixPath(path).name,
                          'bytes': len(data), 'modified_at': modified, 'sha256': hashes[path]})
        log = contents.get('CHANGELOG.md', b'').decode('utf-8-sig')
        sections = [(line, offset) for line, level, offset in read_modules.headings(log) if level == 2]
        changes = []
        for i, (line, begin) in enumerate(sections):
            stop = sections[i+1][1] if i+1 < len(sections) else len(log)
            date = re.search(r'20\d{2}-\d{2}-\d{2}', line)
            changes.append({'title': line[3:], 'date': date[0] if date else None, 'body': log[begin+len(line):stop].strip()})
        readable = {n['id'] for n in catalog['nodes'] if n['readable']}
        triggers = self.trigger_rules()
        presets = [dict(triggers['tasks'].get(i, {}), id=i, title=t, description=d, modules=m)
                   for i,t,d,m in PRESETS if all(x in readable for x in m)]
        conditions = [c for c in triggers['conditions'] if all(m in readable for m in c['modules'])]
        return {'name': name[1].strip() if name else self.root.name, 'title': title[1] if title else self.root.name,
                'version': version[1] if version else '未声明', 'checked_at': now(), 'root': str(self.root), 'source_root': str(source),
                'local': local, 'remote': remote, 'files': files, 'catalog': catalog, 'changelog': changes,
                'entry_text': entry, 'presets': presets, 'conditions': conditions}

    def plan(self, task_id, enabled):
        state = self.state()
        task = next((p for p in state['presets'] if p['id'] == task_id), None)
        if task is None:
            raise ValueError('Unknown or unavailable task')
        options = [c for c in state['conditions'] if task_id in c['tasks']]
        if set(enabled) - {c['id'] for c in options}:
            raise ValueError('Unknown or inapplicable trigger for this task')
        base = self.route(task['modules'])
        base_ids = {m['id'] for m in base['modules']}
        options = [dict(c, included=all(m in base_ids for m in c['modules']),
                        active=c['id'] in enabled or all(m in base_ids for m in c['modules'])) for c in options]
        requested = list(dict.fromkeys(task['modules'] + [m for c in options if c['active'] for m in c['modules']]))
        return {'task': task, 'conditions': options, 'requested': requested, 'route': self.route(requested)}

    def route(self, requested):
        if not requested or len(requested) > 24:
            raise ValueError('Select between 1 and 24 readable modules')
        state = read_modules.load_catalog(self.root)
        result = read_modules.read_modules(state, requested)
        for module in result['modules']:
            key = module['id']
            module['reason'] = 'required' if key in state['catalog']['required'] else 'requested' if key in requested else 'dependency'
            module['required_by'] = [n['id'] for n in state['nodes'].values()
                                     if n['id'] in {m['id'] for m in result['modules']} and
                                     (key in n.get('requires', []) or n.get('parent') == key)]
        result['checked_at'] = now()
        return result

    def document(self, name):
        relative = PurePosixPath(name)
        if (not name or relative.is_absolute() or ':' in name or '\\' in name or
                any(part.startswith('.') for part in relative.parts) or relative.suffix.lower() not in TEXT_EXTENSIONS):
            raise ValueError('Only public text files inside this skill may be read')
        target = (self.root / name).resolve()
        if not target.is_relative_to(self.root) or not target.is_file() or name not in snapshot(self.root):
            raise ValueError('The document is missing or outside this skill')
        data = target.read_bytes()
        if len(data) > 2 * 1024 * 1024:
            raise ValueError('The document is too large for this viewer')
        return {'path': name, 'content': data.decode('utf-8-sig'), 'sha256': hashlib.sha256(data).hexdigest(),
                'modified_at': datetime.fromtimestamp(target.stat().st_mtime, timezone.utc).isoformat()}

    def check_remote(self):
        if not self.remote_lock.acquire(blocking=False):
            raise ValueError('A remote content check is already running')
        try:
            try:
                source, _ = self.source()
                contents = snapshot(source)
                basis = fingerprint({p: hashlib.sha256(d).hexdigest() for p,d in contents.items()})
                repo = Path(git(source, 'rev-parse', '--show-toplevel').decode('utf-8').strip()).resolve()
                prefix = source.relative_to(repo).as_posix()
                prefix = '' if prefix == '.' else prefix + '/'
                url = git(source, 'remote', 'get-url', 'origin').decode('utf-8').strip()
                # Keep credentials and machine paths out of the UI and cache.
                parsed = urlsplit(url)
                repository = ('https://' + parsed.hostname + parsed.path.removesuffix('.git')) if parsed.scheme == 'https' and parsed.hostname else None
                git(source, 'fetch', '--no-tags', 'origin', 'refs/heads/main:refs/remotes/origin/main', timeout=35)
                commit = git(source, 'rev-parse', 'refs/remotes/origin/main').decode().strip()
                tree = git(source, 'ls-tree', '-r', '-z', '--full-tree', commit)
                remote = {}
                for item in tree.split(b'\0'):
                    if not item:
                        continue
                    meta, path = item.split(b'\t', 1)
                    path = path.decode('utf-8')
                    if not path.startswith(prefix):
                        continue
                    path = path[len(prefix):]
                    parts = PurePosixPath(path).parts
                    if any(p.startswith('.') and p != '.gitignore' for p in parts) or any(p in ('__pycache__','node_modules') for p in parts):
                        continue
                    remote[path] = meta.split()[2].decode()
                local = {p: hashlib.sha1(b'blob '+str(len(d)).encode()+b'\0'+d).hexdigest() for p,d in contents.items()}
                diff = differences(local, remote)
                result = {'status': 'different' if diff else 'verified', 'checked_at': now(), 'commit': commit,
                          'branch': 'main', 'repository': repository, 'differences': diff,
                          'files_checked': len(local.keys() | remote.keys()), 'basis_revision': basis, 'source_root': str(source)}
                if fingerprint(file_hashes(source)) != basis:
                    result.update(previous_status=result['status'], status='stale')
            except (OSError, ValueError, KeyError, TypeError, UnicodeError, subprocess.SubprocessError):
                result = {'status': 'error', 'checked_at': now(), 'error': '远端核验未完成。请检查注册来源、Git origin/main、网络及现有Git登录；本机查看仍可使用。'}
            self.remote = result
            try:
                self.cache_path.parent.mkdir(parents=True, exist_ok=True)
                temporary = self.cache_path.with_suffix('.tmp')
                temporary.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
                temporary.replace(self.cache_path)
            except OSError:
                result['cache_warning'] = '本次结果仅保留在服务内存中。'
            return result
        finally:
            self.remote_lock.release()


def make_server(store, port):
    class LocalServer(ThreadingHTTPServer):
        allow_reuse_address = False

        def server_bind(self):
            if hasattr(socket, 'SO_EXCLUSIVEADDRUSE'):
                self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            super().server_bind()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            pass

        def reply(self, data, status=200, content_type='application/json; charset=utf-8'):
            payload = data if isinstance(data, bytes) else json.dumps(data, ensure_ascii=False).encode('utf-8')
            self.send_response(status)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(payload)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
            self.end_headers()
            try:
                self.wfile.write(payload)
            except ConnectionError:
                # A navigation or closed tab may disconnect a valid read request.
                pass

        def allowed(self):
            expected = {f'127.0.0.1:{self.server.server_port}', f'localhost:{self.server.server_port}'}
            host = self.headers.get('Host', '')
            origin = self.headers.get('Origin')
            if host not in expected or (origin and origin not in {'http://' + h for h in expected}):
                self.reply({'error': 'Only same-origin local dashboard access is allowed'}, 403)
                return False
            return True

        def do_GET(self):
            if not self.allowed():
                return
            url = urlsplit(self.path)
            query = parse_qs(url.query)
            try:
                if url.path == '/api/identity':
                    self.reply({'app': 'cinematic-skill-dashboard', 'root': str(store.root)})
                elif url.path == '/api/state':
                    self.reply(store.state())
                elif url.path == '/api/route':
                    self.reply(store.route([m for m in query.get('modules', [''])[0].split(',') if m]))
                elif url.path == '/api/plan':
                    self.reply(store.plan(query.get('task', [''])[0], [c for c in query.get('triggers', [''])[0].split(',') if c]))
                elif url.path == '/api/document':
                    self.reply(store.document(query.get('path', [''])[0]))
                elif url.path in ('/', '/index.html', '/style.css', '/app.js'):
                    path = ASSETS / ('index.html' if url.path == '/' else url.path[1:])
                    self.reply(path.read_bytes(), content_type=(mimetypes.guess_type(path.name)[0] or 'application/octet-stream')+'; charset=utf-8')
                elif url.path == '/favicon.ico':
                    self.reply(b'', 204, 'image/x-icon')
                else:
                    self.reply({'error': 'Not found'}, 404)
            except (ValueError, KeyError, UnicodeError) as error:
                self.reply({'error': str(error)}, 400)
            except OSError:
                self.reply({'error': 'Unable to read the current skill files'}, 500)

        def do_POST(self):
            if not self.allowed():
                return
            if self.path != '/api/remote-check':
                self.reply({'error': 'Not found'}, 404)
                return
            try:
                self.reply(store.check_remote())
            except ValueError as error:
                self.reply({'error': str(error)}, 409)

    server = LocalServer(('127.0.0.1', port), Handler)
    server.daemon_threads = True
    return server


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--skill-dir', type=Path, default=Path(__file__).resolve().parent.parent)
    parser.add_argument('--port', type=int, default=8769)
    parser.add_argument('--open', action='store_true', help='Open the ready dashboard in the default browser')
    args = parser.parse_args(argv)
    if not 0 <= args.port <= 65535:
        parser.error('--port must be between 0 and 65535')
    store = DashboardStore(args.skill_dir)
    server_record = store.cache_path.with_name('server.json')
    candidates = [args.port] if args.port else []
    if args.port == 8769:
        try:
            record = json.loads(server_record.read_text(encoding='utf-8'))
            if (isinstance(record, dict) and record.get('root') == str(store.root) and
                    type(record.get('port')) is int and 0 < record['port'] <= 65535):
                candidates.insert(0, record['port'])
        except (OSError, ValueError):
            pass
    # Reuse only an identity-checked viewer for this exact skill, including a remembered fallback port.
    for port in dict.fromkeys(candidates):
        try:
            with urlopen(f'http://127.0.0.1:{port}/api/identity', timeout=2) as response:
                identity = json.load(response)
            if identity == {'app':'cinematic-skill-dashboard','root':str(store.root)}:
                url = f'http://127.0.0.1:{port}/'
                print(json.dumps({'status':'reused','url':url,'root':str(store.root)}), flush=True)
                if args.open:
                    webbrowser.open(url)
                return 0
        except (OSError, ValueError):
            pass
    try:
        server = make_server(store, args.port)
    except OSError:
        server = make_server(store, 0)
    url = f'http://127.0.0.1:{server.server_port}/'
    try:
        server_record.parent.mkdir(parents=True, exist_ok=True)
        server_record.write_text(json.dumps({'root':str(store.root),'port':server.server_port}), encoding='utf-8')
    except OSError:
        pass  # The running URL remains usable even if port reuse cannot be remembered.
    print(json.dumps({'status':'running','url':url,'root':str(store.root)}), flush=True)
    if args.open:
        threading.Timer(0.2, webbrowser.open, args=(url,)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
