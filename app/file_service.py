"""Scoped local files for the workbench. Remote paths never fall back to this Mac."""
import contextlib
import base64
import errno
import fcntl
import hashlib
import json
import logging
import mimetypes
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import threading
import time
import uuid
from urllib.parse import quote, unquote, urlsplit

DATA = Path(__file__).resolve().parent / 'data'
DEFAULT_ROOT = str(Path(__file__).resolve().parent)
MAX_TEXT = 5 * 1024 * 1024
MAX_READ = 64 * 1024 * 1024


class FileError(ValueError):
    def __init__(self, code, message, status=400, **details):
        super().__init__(message)
        self.code, self.status, self.details = code, status, details


def read_json(path, default):
    try:
        return json.loads(Path(path).read_text())
    except FileNotFoundError:
        return default


def configured_default_root(data=DATA):
    cfg=read_json(Path(data)/'config.json',{})
    for value in (cfg.get('workspace_root'),cfg.get('default_cwd')):
        if isinstance(value,str):
            path=os.path.realpath(os.path.expanduser(os.path.expandvars(value)))
            if os.path.isdir(path):return path
    return DEFAULT_ROOT


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    with os.fdopen(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'w') as handle:
        json.dump(value, handle, ensure_ascii=False)
    os.replace(tmp, path)


def local_host(host):
    if host not in ('mac', 'local', None, ''):
        raise FileError('remote_unsupported', 'Remote file access is not supported by this connector yet', 501)
    return 'mac'


FILE_MENTIONS = re.compile(
    r'\]\\?\((?P<link><[^>\n]+>|(?:\\[()]|\([^()\n]*\)|[^()\n])+?)\\?\)'
    r'|(?P<ticks>`+)(?P<code>[^`\n]+)(?P=ticks)'
    r'|(?<![\w:/])(?P<bare>(?:~/|/)[^\s<>"\'`]+)'
)


def explicit_file_links(text, cwd=''):
    """Recognize the local path forms the chat renderer exposes as links."""
    for match in FILE_MENTIONS.finditer(text):
        link, code, bare = match.group('link', 'code', 'bare')
        if code and not re.fullmatch(
            r'(?:/|\.\.?/|~/|file://).+|(?:[\w.@-]+/)*[\w.@-]+\.[\w]+(?::\d+(?::\d+)?)?(?:#.*)?',
            code,
        ):
            continue
        if bare:
            bare = bare.rstrip('.,;:!?)}]')
            if '/' not in bare[1:]:
                continue
        value = (link or code or bare).removeprefix('<').removesuffix('>')
        value = value.replace(r'\(', '(').replace(r'\)', ')')
        value = re.sub(r':\d+(?::\d+)?(?=[#?]|$)', '', value)
        try:
            parsed = urlsplit(value)
        except ValueError:
            continue
        if parsed.scheme not in ('', 'file') or parsed.netloc or not parsed.path:
            continue
        path = re.sub(r':\d+(?::\d+)?$', '', unquote(parsed.path))
        if path.startswith('~/'):
            home = re.match(r'^/(?:Users|home)/[^/]+', cwd)
            if not home:
                continue
            path = home[0] + path[1:]
        yield path


def within(path, folder):
    return bool(isinstance(folder, str) and folder.startswith('/') and
                (path == folder or path.startswith(folder.rstrip('/') + '/')))


def reference_host(host, path, roots=()):
    """Pick the host that holds an explicitly linked file.

    A remote chat keeps its own host. A Mac chat keeps the Mac when the file is
    here; otherwise the file belongs to the one configured remote host whose
    workspaces contain the path (from hosts config, never a hard-coded prefix).
    """
    if host not in ('mac', 'local', None, '') or os.path.exists(path):
        return host
    import hosts
    def covers(record):
        folders = list(record.get('workspaces') or []) + [record.get('default_cwd')]
        folders += [root['path'] for root in roots if root['host_id'] == record['id']]
        return any(within(path, folder) for folder in folders)
    owners = [record['id'] for record in hosts.host_records()
              if record.get('transport') not in (None, 'local') and record.get('configured')
              and record.get('read_alias') and covers(record)]
    if len(owners) == 1:
        return owners[0]
    if owners:
        raise FileError('remote_unsupported', 'Several configured hosts list this folder; open the file from a chat on that host.', 501)
    return host


def mac_file_identity(path):
    try:
        info = os.stat(path, follow_symlinks=False)
        if stat.S_ISREG(info.st_mode) and hasattr(info, 'st_birthtime'):
            return [info.st_dev, info.st_ino, info.st_birthtime]
    except OSError:
        pass
    return None


def mac_file_location(identity):
    """APFS can find a renamed file by volume and inode without a directory scan."""
    try:
        device, inode, _ = identity
        lookup = f'/.vol/{int(device)}/{int(inode)}'
        with open(lookup, 'rb', opener=lambda path, flags: os.open(path, flags | os.O_NONBLOCK)) as file:
            value = fcntl.fcntl(file, getattr(fcntl, 'F_GETPATH', 50), bytes(1024))
        path = os.fsdecode(value.partition(b'\0')[0])
        if mac_file_identity(path) == identity:
            return path
    except (OSError, ValueError, TypeError):
        pass
    return None


def parts(path):
    if not isinstance(path, str) or '\x00' in path or '\\' in path:
        raise FileError('invalid_path', 'Invalid file path')
    if path.startswith('/') or any(p == '..' for p in path.split('/')):
        raise FileError('outside_root', 'Path must remain within its selected root', 403)
    return [p for p in path.split('/') if p not in ('', '.')]


def mime(path):
    return mimetypes.guess_type(str(path))[0] or 'application/octet-stream'


def content_policy(ctype):
    if ctype == 'image/svg+xml':
        # SVG remains renderable in <img>. Direct navigation is an opaque,
        # scriptless sandbox; inline styles and embedded raster data can render.
        return "default-src 'none'; style-src 'unsafe-inline'; img-src data:; sandbox"
    return "default-src 'none'; sandbox"


def version(info, raw=None):
    prefix = f'{info.st_dev}:{info.st_ino}:{info.st_mtime_ns}:{info.st_size}:'
    return hashlib.sha256(prefix.encode() + (raw if raw is not None else b'')).hexdigest()


def tree_summary(path):
    """Names, sizes and hashes of a file or folder, used to verify a copy before deleting its source."""
    def entry(p):
        if os.path.islink(p):
            return ('link', os.readlink(p))
        if os.path.isdir(p):
            return ('dir',)
        digest = hashlib.sha256()
        with open(p, 'rb') as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b''):
                digest.update(chunk)
        return ('file', os.path.getsize(p), digest.hexdigest())
    result = [('', entry(path))]
    if os.path.isdir(path) and not os.path.islink(path):
        for folder, dirs, files in os.walk(path):
            for name in dirs + files:
                full = os.path.join(folder, name)
                result.append((os.path.relpath(full, path), entry(full)))
    return sorted(result)


def remove_path(path):
    if os.path.isdir(path) and not os.path.islink(path):
        shutil.rmtree(path)
    elif os.path.lexists(path):
        os.unlink(path)


def transfer(src, dst, copy=False):
    """Move (or copy) src to dst without replacing anything. Same device: one atomic rename.
    Across devices: copy to a temporary name, verify, rename into place, then delete the source."""
    if os.path.lexists(dst):
        raise FileError('destination_exists', os.path.basename(dst) + ' already exists in the destination', 409)
    if not copy:
        try:
            os.rename(src, dst)
            return
        except OSError as exc:
            if exc.errno != errno.EXDEV:
                raise
    temp = os.path.join(os.path.dirname(dst), '.workbench-move-' + uuid.uuid4().hex)
    try:
        if os.path.isdir(src) and not os.path.islink(src):
            shutil.copytree(src, temp, symlinks=True)
        else:
            shutil.copy2(src, temp, follow_symlinks=False)
        if tree_summary(src) != tree_summary(temp):
            raise FileError('verify_failed', 'The copy did not match the original, so nothing was moved', 500)
        if os.path.lexists(dst):
            raise FileError('destination_exists', os.path.basename(dst) + ' already exists in the destination', 409)
        os.rename(temp, dst)
    except BaseException:
        remove_path(temp)
        raise
    if not copy:
        remove_path(src)


def free_name(folder, name, is_dir=False):
    """Keep both uses VS Code's naming: 'name copy.ext', then 'name copy 2.ext'."""
    stem, ext = (name, '') if is_dir else os.path.splitext(name)
    while os.path.lexists(os.path.join(folder, stem + ext)):
        copy = re.fullmatch(r'(.+ copy)( \d+)?', stem)
        stem = f'{copy.group(1)} {int(copy.group(2) or 1) + 1}' if copy else stem + ' copy'
    return stem + ext


def decode_text(raw):
    if len(raw) > MAX_TEXT:
        raise FileError('too_large', 'Text editing is limited to 5 MiB', 413)
    if b'\0' in raw:
        raise FileError('unsupported_encoding', 'Binary data is not editable text', 415)
    encoding = 'utf-8-sig' if raw.startswith(b'\xef\xbb\xbf') else 'utf-8'
    try:
        return raw.decode(encoding), encoding
    except UnicodeDecodeError:
        raise FileError('unsupported_encoding', 'Only UTF-8 text editing is supported', 415)



DOCUMENT_SUFFIXES = ('.md', '.markdown', '.mdown', '.html', '.htm')


def is_rendered_document(name):
    return name.lower().endswith(DOCUMENT_SUFFIXES)



class FileService:
    def __init__(self, data=DATA, default_root=DEFAULT_ROOT):
        self.data = Path(data)
        self.lock = threading.RLock()
        self.document_locks = [threading.RLock() for _ in range(32)]
        self.observed_documents = {}
        self.local_watches = {}
        self.refs = {}
        self.moves = {}
        self.default_root = default_root
        self.roots = read_json(self.data / 'workbench-roots.json', {})
        self.relocation_file = (self.data.parent / '.file-relocations.json'
                                if self.data == DATA else self.data / 'file-relocations.json')
        if default_root and os.path.isdir(default_root):
            root = self._root(default_root, 'write')
            self.roots.setdefault(root['id'], root)

    def _root(self, path, access):
        if not isinstance(path, str) or not os.path.isabs(path) or '\x00' in path:
            raise FileError('invalid_path', 'Choose an absolute workspace directory')
        canonical = os.path.realpath(path)
        if not os.path.isdir(canonical):
            raise FileError('not_found', 'Workspace directory does not exist', 404)
        rid = hashlib.sha256(('mac\0' + canonical + '\0' + access).encode()).hexdigest()[:32]
        return {'id': rid, 'root_id': rid, 'host_id': 'mac', 'path': canonical,
                'access': access, 'label': os.path.basename(canonical) or canonical}

    def register(self, host_id, path, access='read'):
        if access not in ('read', 'write'):
            raise FileError('invalid_access', 'Access must be read or write')
        if host_id not in ('mac', 'local', None, ''):
            if access != 'read':
                raise FileError('read_only', 'Remote file roots are read-only', 403)
            import remote_files
            canonical = remote_files.request(host_id, 'root', {'root':path})['path']
            rid = hashlib.sha256((host_id+'\0'+canonical+'\0read').encode()).hexdigest()[:32]
            root = {'id':rid,'root_id':rid,'host_id':host_id,'path':canonical,'access':'read','label':os.path.basename(canonical) or canonical}
        else:
            root = self._root(path, access)
        with self.lock:
            self.roots[root['id']] = root
            atomic_json(self.data / 'workbench-roots.json', self.roots)
        return root

    def get_root(self, rid):
        with self.lock:
            root = self.roots.get(str(rid))
        if not root:
            raise FileError('outside_root', 'Unknown workspace root', 403)
        return root

    @contextlib.contextmanager
    def directory(self, root, components):
        if root.get('host_id', 'mac') != 'mac':
            raise FileError('remote_unsupported', 'Remote paths cannot use the local filesystem reader', 501)
        descriptors = []
        try:
            # Walk from / so a replaced ancestor cannot redirect the root either.
            fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
            descriptors.append(fd)
            for component in parts(root['path'].lstrip('/')) + components:
                fd = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                descriptors.append(fd)
            yield fd
        except FileNotFoundError:
            raise FileError('not_found', 'File or directory no longer exists', 404)
        except (NotADirectoryError, PermissionError) as exc:
            raise FileError('outside_root', 'Directory is unreadable or contains a symlink', 403) from exc
        except OSError as exc:
            raise FileError('outside_root', 'File access was refused: ' + str(exc), 403) from exc
        finally:
            for descriptor in reversed(descriptors):
                os.close(descriptor)

    @contextlib.contextmanager
    def opened(self, rid=None, path='', file_id=None):
        if file_id:
            ref = self.refs.get(str(file_id))
            if not ref:
                raise FileError('not_found', 'File reference expired; reopen its conversation link', 404)
            root = {'path': os.path.dirname(ref['path']), 'host_id': 'mac', 'access': 'read', 'id': ''}
            components = [os.path.basename(ref['path'])]
        else:
            root, components = self.get_root(rid), parts(path)
        if not components:
            raise FileError('invalid_path', 'Choose a file within the root')
        with self.directory(root, components[:-1]) as parent:
            descriptor = None
            try:
                descriptor = os.open(components[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
                info = os.fstat(descriptor)
                if not stat.S_ISREG(info.st_mode):
                    raise FileError('invalid_path', 'Only regular files can be read', 415)
                yield descriptor, info, root, components, parent
            except FileNotFoundError:
                raise FileError('not_found', 'File no longer exists', 404)
            except OSError as exc:
                raise FileError('outside_root', 'File is unreadable or a symlink', 403) from exc
            finally:
                if descriptor is not None:
                    os.close(descriptor)

    def _raw(self, descriptor, info, limit):
        if info.st_size > limit:
            raise FileError('too_large', f'File exceeds the {limit // (1024 * 1024)} MiB read limit', 413)
        raw = os.read(descriptor, limit + 1)
        if len(raw) > limit:
            raise FileError('too_large', 'File grew beyond the read limit', 413)
        return raw

    def remote_target(self, rid=None, path='', file_id=None):
        if file_id:
            ref = self.refs.get(str(file_id))
            if not ref:
                raise FileError('not_found', 'File reference expired; reopen its conversation link', 404)
            if ref.get('host_id', 'mac') != 'mac':
                if path:
                    raise FileError('outside_root', 'A file reference does not authorize other paths', 403)
                return {'host_id':ref['host_id'], 'path':os.path.dirname(ref['path']), 'id':None}, os.path.basename(ref['path'])
            return None, path
        root = self.get_root(rid)
        return (root, path) if root.get('host_id', 'mac') != 'mac' else (None, path)

    def read(self, rid=None, path='', file_id=None):
        root, path = self.remote_target(rid, path, file_id)
        if root and root.get('host_id') != 'mac':
            import remote_files
            parts(path)
            return base64.b64decode(remote_files.request(root['host_id'],'read',{'root':root['path'],'path':path})['data_base64'],validate=True)
        with self.opened(rid, path, file_id) as (descriptor, info, *_):
            return self._raw(descriptor, info, MAX_READ)

    def stat(self, rid=None, path='', file_id=None, *, include_identity=False):
        remote, relative = self.remote_target(rid, path, file_id)
        if file_id:
            canonical = self.refs[str(file_id)]['path']
        else:
            root = remote or self.get_root(rid)
            canonical = os.path.join(root['path'], *parts(relative))
        identity = (remote['host_id'] if remote else 'mac') + ':' + canonical
        lock = self._document_lock(identity) if is_rendered_document(canonical) else contextlib.nullcontext()
        with lock:
            return self._stat(rid, relative, file_id, include_identity, remote, identity)

    def _stat(self, rid, path, file_id, include_identity, root, identity):
        if root and root.get('host_id') != 'mac':
            import remote_files
            parts(path)
            payload = {'root':root['path'], 'path':path}
            if include_identity:
                payload['identity'] = True
            if is_rendered_document(path):
                payload['observed_version'] = self.observed_documents.get(identity, (None, False))[0]
            result = remote_files.request(root['host_id'], 'stat', payload)
            resource = result['resource']
            if 'document_base64' in result:
                raw = base64.b64decode(result['document_base64'], validate=True)
                self._observe(identity, resource['version'], raw)
            resource['has_previous'] = bool(resource.get('encoding') and self.observed_documents.get(identity, (None, False))[1])
            return dict(resource, host_id=root['host_id'], resource_id=root['host_id']+':'+resource['path'],root_id=rid if not file_id else None,file_id=file_id,editable=False, evidence_path=self.refs.get(str(file_id), {}).get('evidence_path') if file_id else None)
        with self.opened(rid, path, file_id) as (descriptor, info, root, components, _):
            raw = self._raw(descriptor, info, MAX_TEXT) if info.st_size <= MAX_TEXT else None
            editable = root['access'] == 'write' and raw is not None
            encoding = None
            if raw is not None:
                try:
                    _, encoding = decode_text(raw)
                except FileError:
                    editable = False
            canonical = os.path.join(root['path'], *components)
            current = version(info, raw)
            previous = False
            if encoding and is_rendered_document(canonical):
                end = os.fstat(descriptor)
                if info.st_mtime_ns != end.st_mtime_ns or len(raw) != end.st_size:
                    raise FileError('version_conflict', 'Document changed while reading; renew to try again', 409)
                previous = self._observe(identity, current, raw)
                with self.lock:
                    self.local_watches[canonical] = ((info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns), rid, path, file_id)
            return {'resource_id': 'mac:' + canonical, 'host_id': 'mac', 'path': canonical,
                    'name': components[-1], 'mime': mime(canonical), 'size': info.st_size,
                    'version': current, 'editable': editable, 'has_previous': previous,
                    'document_editable': bool(encoding and is_rendered_document(canonical)
                                              and (root['access'] == 'write' or file_id)
                                              and os.access(canonical, os.W_OK)
                                              and os.access(os.path.dirname(canonical), os.W_OK)),
                    'root_id': root.get('id') or None, 'file_id': file_id,
                    'evidence_path': self.refs.get(str(file_id), {}).get('evidence_path') if file_id else None,
                    'relative_path': '/'.join(components),
                    'encoding': encoding,
                    'newline': 'crlf' if raw and b'\r\n' in raw else 'lf'}

    def list(self, rid, path='', cursor=0, q=''):
        root, components = self.get_root(rid), parts(path)
        try:
            offset = int(cursor or 0)
        except (TypeError, ValueError):
            raise FileError('invalid_cursor', 'Cursor must be a nonnegative integer')
        if offset < 0 or len(str(q)) > 200:
            raise FileError('invalid_request', 'Invalid cursor or search query')
        entries = []
        if root.get('host_id') != 'mac':
            import remote_files
            value = remote_files.request(root['host_id'],'list',{'root':root['path'],'path':'/'.join(components)})
            entries = value['entries']
            entries = [entry for entry in entries if not q or str(q).casefold() in entry['name'].casefold()]
            entries.sort(key=lambda e:(e['type'] != 'directory',e['name'].casefold()))
            limit = 100 if q else 200
            return {'ok':True,'entries':entries[offset:offset+limit],
                    'next_cursor':str(offset+limit) if len(entries)>offset+limit else None,'truncated':value.get('truncated',False)}
        with self.directory(root, components) as descriptor:
            with os.scandir(descriptor) as iterator:
                for item in iterator:
                    if len(entries) >= 10000:
                        break
                    if q and str(q).casefold() not in item.name.casefold():
                        continue
                    info = item.stat(follow_symlinks=False)
                    kind = 'directory' if stat.S_ISDIR(info.st_mode) else 'file' if stat.S_ISREG(info.st_mode) else 'symlink' if stat.S_ISLNK(info.st_mode) else 'unsupported'
                    entries.append({'name':item.name,'path':'/'.join(components + [item.name]),
                                    'type':kind,'size':info.st_size,'mime':mime(item.name)})
        entries.sort(key=lambda e:(e['type'] != 'directory', e['name'].casefold()))
        limit = 100 if q else 200
        return {'ok':True,'entries':entries[offset:offset+limit],
                'next_cursor':str(offset+limit) if len(entries) > offset+limit else None,
                'truncated':len(entries) >= 10000}

    def reference(self, key, path, latest_reply=None):
        if not isinstance(key, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,160}', key):
            raise FileError('invalid_path', 'Invalid conversation key')
        if not isinstance(path, str) or not path or '\x00' in path:
            raise FileError('invalid_path', 'Invalid file path')
        chat = read_json(self.data / 'chats' / (key + '.json'), {})
        def resolve(evidence):
            if not evidence:
                return None
            cwd = evidence.get('cwd') or ''
            candidate = os.path.abspath(os.path.join(cwd, path))
            changed = evidence.get('files_changed') or []
            paths = [row.get('path') or row.get('file') if isinstance(row, dict) else row for row in changed]
            for turn in evidence.get('turns') or []:
                paths.extend(explicit_file_links(turn.get('text') or '', cwd))
            if any(candidate == os.path.abspath(os.path.join(cwd, p)) for p in paths if isinstance(p, str) and p):
                host = evidence.get('execution_host') or evidence.get('host_id') or evidence.get('node') or 'mac'
                return candidate, 'mac' if host == 'local' else host, cwd

        match = resolve(chat)
        if not match and latest_reply:
            live = latest_reply(key)
            match = resolve(live)
            chat = chat or live
        if not match:
            if not chat:
                raise FileError('not_found', 'Conversation was not found', 404)
            raise FileError('outside_root', 'This conversation does not include an openable link to that file.', 403)
        candidate, chat_host, cwd = match
        with self.lock:
            roots = tuple(self.roots.values())
        host = reference_host(chat_host, candidate, roots)
        if chat_host == host and os.path.isabs(cwd):
            roots += ({'host_id':host, 'path':os.path.realpath(cwd) if host == 'mac' else cwd},)
        original = os.path.realpath(candidate) if host == 'mac' else candidate
        try:
            result = self._open_reference(host, original, candidate)
        except FileError as error:
            if error.code != 'not_found':
                raise
            recovered = self._recover_reference(host, original, roots)
            if recovered is None:
                raise
            found_host, found_path = recovered
            result = self._open_reference(found_host, found_path, candidate)
            result['relocated_from_host'] = host
        identity = result.pop('disk_identity', None)
        if result['host_id'] == host:
            if host == 'mac' and any(root['host_id'] == host and within(original, root['path'])
                                     and within(result['path'], root['path']) for root in roots):
                identity = mac_file_identity(result['path'])
            try:
                if identity:
                    self._remember_file(host, original, identity)
            except (OSError, ValueError):
                logging.exception('Unable to remember a linked file location')
        return result

    def _open_reference(self, host, path, evidence_path):
        fid = uuid.uuid4().hex
        if host != 'mac':
            import remote_files
            parent = remote_files.request(host, 'root', {'root':os.path.dirname(path)})['path']
            path = os.path.join(parent, os.path.basename(path))
        else:
            path = os.path.realpath(path)
        self.refs[fid] = {'path':path, 'host_id':host, 'evidence_path':evidence_path}
        try:
            return dict(self.stat(file_id=fid, include_identity=host != 'mac'), file_id=fid)
        except Exception:
            self.refs.pop(fid, None)
            raise

    def _remember_file(self, host, original, identity):
        with self.lock:
            state = read_json(self.relocation_file, {})
            files = state.setdefault('files', {}).setdefault(host, {})
            if files.get(original) != identity:
                files[original] = identity
                atomic_json(self.relocation_file, state)

    def _record_moves(self, moves):
        moves = [(host, src, target_host, dst) for host, src, target_host, dst in moves
                 if (host, src) != (target_host, dst)]
        if not moves:
            return
        try:
            with self.lock:
                state = read_json(self.relocation_file, {})
                records = state.setdefault('moves', {})
                for src_host, src, dst_host, dst in moves:
                    records.setdefault(src_host, {})[src] = [dst_host, dst]
                atomic_json(self.relocation_file, state)
        except (OSError, ValueError):
            logging.exception('Unable to record file moves')

    def _recover_reference(self, host, path, roots):
        state = read_json(self.relocation_file, {})
        location, seen = (host, path), {(host, path)}
        for _ in range(30):
            choices = state.get('moves', {}).get(location[0], {})
            source = max((source for source in choices if within(location[1], source)),
                         key=len, default=None)
            if source is None:
                break
            dest_host, dest = choices[source]
            location = (dest_host, dest + location[1][len(source):])
            if location in seen:
                break
            seen.add(location)
        if location != (host, path):
            return location

        scope = [root['path'] for root in roots if root['host_id'] == host]
        if host != 'mac':
            import remote_files
            remote = remote_files.host_config(host)
            scope += list(remote.get('workspaces') or []) + [remote.get('default_cwd')]
        folders = [folder for folder in scope if folder != '/' and within(path, folder)]
        if not folders:
            return None
        identity = state.get('files', {}).get(host, {}).get(path)
        if host == 'mac' and identity:
            moved = mac_file_location(identity)
            if moved:
                return (host, moved) if any(within(moved, folder) for folder in folders) else None
        name = os.path.basename(path)
        if not Path(name).suffix or len(name) > 200:
            return None
        if host == 'mac':
            folder = max((folder for folder in folders if os.path.isdir(folder)), key=len, default=None)
            if folder is None:
                return None
            result = self._local_search({'host_id':'mac', 'path':folder}, name, exact=True)
        else:
            for folder in sorted(set(folders), key=len, reverse=True):
                try:
                    folder = remote_files.request(host, 'root', {'root':folder})['path']
                    break
                except FileError as error:
                    if error.code != 'not_found':
                        raise
            else:
                return None
            payload = {'root':folder, 'query':name, 'relocation':True}
            if identity:
                payload['identity'] = identity
            result = remote_files.request(host, 'search', payload)
            verified = {entry['path'] for entry in result['entries'] if entry.get('identity_match')}
            if len(verified) == 1 and not result.get('truncated'):
                relative = '/'.join(parts(next(iter(verified))))
                return host, os.path.join(folder, relative)
        paths = {entry['path'] for entry in result['entries']
                 if os.path.basename(entry['path']) == name}
        if len(paths) > 1:
            raise FileError('ambiguous_file', 'Several files in this workspace have the linked name.', 409)
        if len(paths) == 1 and not result.get('truncated'):
            relative = '/'.join(parts(next(iter(paths))))
            return host, os.path.join(folder, relative)
        return None

    def search(self, rid, query):
        if not isinstance(query, str) or not query.strip() or len(query) > 200:
            raise FileError('invalid_query', 'Enter a filename query of 1 to 200 characters')
        root = self.get_root(rid)
        if root.get('host_id') != 'mac':
            import remote_files
            return remote_files.request(root['host_id'], 'search', {'root':root['path'], 'query':query})
        return self._local_search(root, query)

    def _local_search(self, root, query, exact=False):
        pending, entries, scanned = [[]], [], 0
        deadline = time.monotonic() + 2
        truncated = False
        while pending:
            components = pending.pop()
            if len(components) > 32 or scanned >= 20000 or time.monotonic() > deadline or len(entries) >= 100:
                truncated = True
                break
            try:
                with self.directory(root, components) as descriptor:
                    with os.scandir(descriptor) as iterator:
                        for item in iterator:
                            scanned += 1
                            if scanned >= 20000 or time.monotonic() > deadline:
                                truncated = True
                                break
                            if item.is_dir(follow_symlinks=False):
                                if exact and (item.name.startswith('.') or item.name in ('node_modules', '__pycache__')):
                                    continue
                                pending.append(components + [item.name])
                            elif (item.name == query if exact else query.casefold() in item.name.casefold()) and item.is_file(follow_symlinks=False):
                                info = item.stat(follow_symlinks=False)
                                entries.append({'name':item.name, 'path':'/'.join(components+[item.name]),
                                                'type':'file', 'size':info.st_size, 'mime':mime(item.name)})
                                if len(entries) >= 100:
                                    truncated = True
                                    break
            except FileError:
                truncated = True
        return {'ok':True, 'entries':entries, 'next_cursor':None, 'truncated':truncated}

    def search_native(self, rid, path, mode, query, options):
        """Native registered-root search; exact references never authorize it."""
        root = self.get_root(rid)
        components = parts(path)
        if root.get('host_id') == 'mac':
            raise FileError('remote_unsupported', 'Native remote search requires a registered remote root', 501)
        if mode not in ('files', 'text') or not isinstance(query, str) or len(query) > 200 or not isinstance(options, dict):
            raise FileError('invalid_query', 'Invalid native remote search request')
        if len(json.dumps(options)) > 12000:
            raise FileError('invalid_query', 'Remote search options exceed the request limit')
        import remote_files
        result = remote_files.request(root['host_id'], 'search2', {'root':root['path'], 'path':'/'.join(components), 'mode':mode, 'query':query, 'options':options})
        return dict(result, root_id=root['id'], host_id=root['host_id'])

    def _encode(self, original, text):
        if not isinstance(text, str):
            raise FileError('invalid_text', 'Text must be a string')
        prior, encoding = decode_text(original)
        if '\r\n' in prior and prior.count('\n') == prior.count('\r\n'):
            text = text.replace('\r\n', '\n').replace('\n', '\r\n')
        result = text.encode(encoding)
        if len(result) > MAX_TEXT:
            raise FileError('too_large', 'Text exceeds the 5 MiB save limit', 413)
        return result

    def document_review(self, rid, path, file_id, comment=None):
        file = self.stat(rid, path, file_id)
        if not is_rendered_document(file['name']):
            raise FileError('unsupported_document', 'This viewer supports HTML and Markdown documents', 415)
        identity = file['resource_id']
        key = hashlib.sha256(identity.encode()).hexdigest()
        with self.document_locks[int(key[:2], 16) % len(self.document_locks)]:
            checkpoint = read_json(self.data / 'file-backups' / 'previous' / (key + '.json'), {})
            previous = decode_text(base64.b64decode(checkpoint['content']))[0] if checkpoint.get('content') is not None else None
            location = self.data / 'document-comments' / (key + '.json')
            comments = read_json(location, [])
            if comment is not None:
                text, quote = comment.get('text'), comment.get('quote', '')
                if not isinstance(text, str) or not text.strip() or len(text) > 12000 or not isinstance(quote, str) or len(quote) > 2000:
                    raise FileError('invalid_comment', 'Enter a comment of 1 to 12,000 characters')
                comment_id = comment.get('id')
                if not isinstance(comment_id, str) or not re.fullmatch(r'[a-f0-9-]{36}', comment_id):
                    raise FileError('invalid_comment', 'Invalid comment identifier')
                if not any(item['id'] == comment_id for item in comments):
                    comments.append({'id':comment_id,'text':text.strip(),'quote':quote,'created':time.time()})
                    atomic_json(location, comments)
            return {'previous':previous, 'previous_at':checkpoint.get('at'), 'comments':comments}

    def _document_lock(self, identity):
        key = hashlib.sha256(identity.encode()).digest()[0]
        return self.document_locks[key % len(self.document_locks)]

    def _history_path(self, identity):
        key = hashlib.sha256(identity.encode()).hexdigest()
        return self.data / 'file-backups' / 'previous' / (key + '.json')

    def _observe(self, identity, current_version, raw):
        cached = self.observed_documents.get(identity)
        if cached and cached[0] == current_version:
            return cached[1]
        location = self._history_path(identity)
        history = read_json(location, {})
        encoded = base64.b64encode(raw).decode('ascii')
        if history.get('current') != encoded:
            if 'current' in history:
                history.update(content=history['current'], at=history['current_at'])
            elif history.get('content') == encoded:
                history.pop('content')
                history.pop('at', None)
            history.update(current=encoded, current_at=time.time())
            atomic_json(location, history)
        has_previous = 'content' in history
        self.observed_documents[identity] = (current_version, has_previous)
        return has_previous

    def observe_session_document(self, cwd, path):
        if not isinstance(path, str) or not is_rendered_document(path) or not os.path.isabs(cwd):
            return
        absolute = os.path.normpath(os.path.join(os.path.realpath(cwd), path))
        absolute = os.path.join(os.path.realpath(os.path.dirname(absolute)), os.path.basename(absolute))
        with self.lock:
            roots = list(self.roots.values())
        root = max((root for root in roots if root['host_id'] == 'mac' and within(absolute, root['path'])),
                   key=lambda item:len(item['path']), default=None)
        if root is not None:
            self.stat(root['id'], os.path.relpath(absolute, root['path']))

    def observe_local_changes(self):
        # Keep previously opened local documents current, even after the tab closes.
        with self.lock:
            watches = list(self.local_watches.items())
        for canonical, (fingerprint, rid, path, file_id) in watches:
            try:
                info = os.stat(canonical, follow_symlinks=False)
                if fingerprint != (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns):
                    self.stat(rid, path, file_id)
            except (OSError, FileError):
                continue

    def watch_local_documents(self):
        while True:
            time.sleep(1)
            self.observe_local_changes()

    def rewind_document(self, rid, path, file_id, base_version):
        file = self.stat(rid, path, file_id)
        if not file.get('document_editable'):
            raise FileError('read_only', 'This HTML or Markdown file is read-only', 403)
        identity = file['resource_id']
        with self._document_lock(identity):
            if self.stat(rid, path, file_id)['version'] != base_version:
                raise FileError('version_conflict', 'File changed on disk; renew before rewinding', 409)
            history = read_json(self._history_path(identity), {})
            if 'content' not in history:
                raise FileError('no_previous', 'No previous version is available', 409)
            original = base64.b64decode(history['content'], validate=True)
            decode_text(original)
            if file['host_id'] == 'mac':
                return self._save_local(rid, path, base_version, None, file_id=file_id,
                                        document_identity=identity, exact_bytes=original)
            root, relative = self.remote_target(rid, path, file_id)
            import remote_files
            remote_files.request(root['host_id'], 'save_document', {
                'root':root['path'], 'path':relative, 'base_version':base_version,
                'exact_base64':history['content']
            })
            return self.stat(rid, path, file_id)

    def save_document(self, rid, path, file_id, base_version, text):
        if not isinstance(text, str) or '\x00' in text:
            raise FileError('invalid_text', 'Enter UTF-8 document text')
        file = self.stat(rid, path, file_id)
        if not file.get('document_editable'):
            raise FileError('read_only', 'This HTML or Markdown file is read-only', 403)
        identity = file['resource_id']
        key = hashlib.sha256(identity.encode()).hexdigest()
        with self.document_locks[int(key[:2], 16) % len(self.document_locks)]:
            if file['host_id'] == 'mac':
                return self._save_local(rid, path, base_version, text, file_id=file_id, document_identity=identity)
            root, relative = self.remote_target(rid, path, file_id)
            if len(text.encode('utf-8')) > MAX_TEXT:
                raise FileError('too_large', 'Text exceeds the 5 MiB save limit', 413)
            if file['version'] != base_version:
                raise FileError('version_conflict', 'File changed on disk; compare or reload before saving', 409)
            import remote_files
            remote_files.request(root['host_id'], 'save_document', {
                'root':root['path'], 'path':relative, 'base_version':base_version, 'text':text
            })
            return self.stat(rid, path, file_id)

    def save(self, rid, path, base_version, text):
        if self.get_root(rid).get('host_id') != 'mac':
            raise FileError('read_only', 'Remote files are read-only outside rendered document editing', 403)
        if is_rendered_document(path):
            return self.save_document(rid, path, None, base_version, text)
        return self._save_local(rid, path, base_version, text)

    def _save_local(self, rid, path, base_version, text, *, file_id=None, document_identity=None, exact_bytes=None):
        with self.lock, self.opened(rid, path, file_id) as (descriptor, info, root, components, parent):
            if root['access'] != 'write' and not (document_identity and file_id):
                raise FileError('read_only', 'This root is read-only', 403)
            raw = self._raw(descriptor, info, MAX_TEXT)
            if version(info, raw) != base_version:
                raise FileError('version_conflict', 'File changed on disk; compare or reload before saving', 409, current_version=version(info, raw))
            output = exact_bytes if exact_bytes is not None else self._encode(raw, text)
            if output == raw:
                return self.stat(rid, path, file_id)
            if not document_identity:
                backup = self.data / 'file-backups' / (str(time.time_ns()) + '-' + uuid.uuid4().hex)
                backup.parent.mkdir(parents=True, exist_ok=True)
                with backup.open('xb') as handle:
                    handle.write(raw)
                os.chmod(backup, 0o600)
            temporary = '.workbench-' + uuid.uuid4().hex
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, stat.S_IMODE(info.st_mode), dir_fd=parent)
            try:
                with os.fdopen(fd, 'wb') as handle:
                    handle.write(output)
                    handle.flush()
                    os.fsync(handle.fileno())
                current = os.stat(components[-1], dir_fd=parent, follow_symlinks=False)
                if version(current, raw) != base_version or not stat.S_ISREG(current.st_mode):
                    raise FileError('version_conflict', 'File changed while saving; the buffer is retained', 409)
                os.replace(temporary, components[-1], src_dir_fd=parent, dst_dir_fd=parent)
            finally:
                try:
                    os.unlink(temporary, dir_fd=parent)
                except FileNotFoundError:
                    pass
        return self.stat(rid, path, file_id)

    def copy(self, rid, path, base_version, destination, text):
        with self.lock:
            root = self.get_root(rid)
            if root['access'] != 'write':
                raise FileError('read_only', 'This root is read-only', 403)
            with self.opened(rid, path) as (descriptor, info, *_):
                raw = self._raw(descriptor, info, MAX_TEXT)
                # Saving a copy intentionally permits a stale original version.
                output = self._encode(raw, text)
            components = parts(destination)
            if not components:
                raise FileError('invalid_path', 'Choose a new filename')
            with self.directory(root, components[:-1]) as parent:
                try:
                    descriptor = os.open(components[-1], os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent)
                except FileExistsError:
                    raise FileError('destination_exists', 'Save a copy never replaces an existing file', 409)
                with os.fdopen(descriptor, 'wb') as handle:
                    handle.write(output)
        return self.stat(rid, destination)

    def create(self, rid, path, kind='file'):
        if kind not in ('file', 'directory'):
            raise FileError('invalid_type', 'Choose a file or folder')
        with self.lock:
            root = self.get_root(rid)
            if root.get('host_id', 'mac') != 'mac' or root['access'] != 'write':
                raise FileError('read_only', 'This workspace is read-only', 403)
            components = parts(path)
            if not components:
                raise FileError('invalid_path', 'Choose a new name')
            with self.directory(root, components[:-1]) as parent:
                try:
                    if kind == 'directory':
                        os.mkdir(components[-1], mode=0o755, dir_fd=parent)
                    else:
                        fd = os.open(components[-1], os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent)
                        os.close(fd)
                except FileExistsError:
                    raise FileError('destination_exists', 'A file or folder with this name already exists', 409)
        return {'ok':True, 'root_id':rid, 'path':'/'.join(components), 'type':kind}

    def _writable(self, rid, path):
        root = self.get_root(rid)
        if root.get('host_id', 'mac') != 'mac' or root['access'] != 'write':
            raise FileError('read_only', 'This workspace is read-only', 403)
        components = parts(path)
        if not components:
            raise FileError('invalid_path', 'A workspace root cannot be changed here')
        return root, components

    def rename(self, rid, path, name):
        if not isinstance(name, str) or name in ('', '.', '..') or '/' in name or '\0' in name:
            raise FileError('invalid_name', 'Enter a name without slashes')
        with self.lock:
            root, components = self._writable(rid, path)
            with self.directory(root, components[:-1]) as parent:
                os.stat(components[-1], dir_fd=parent, follow_symlinks=False)
                # A case-only rename is allowed on a case-insensitive disk.
                if name.lower() != components[-1].lower() and name in os.listdir(parent):
                    raise FileError('destination_exists', name + ' already exists here', 409)
                os.rename(components[-1], name, src_dir_fd=parent, dst_dir_fd=parent)
            source = os.path.join(root['path'], *components)
            self._record_moves([('mac', source, 'mac', os.path.join(os.path.dirname(source), name))])
        return {'ok':True, 'root_id':rid, 'path':'/'.join(components[:-1] + [name])}

    def trash(self, rid, paths):
        """Move items to the macOS Trash (recoverable); never unlink."""
        if not isinstance(paths, list) or not 0 < len(paths) <= 500:
            raise FileError('invalid_request', 'Choose files or folders to delete')
        trash = os.path.join(os.path.expanduser('~'), '.Trash')
        moved = []
        with self.lock:
            for path in paths:
                root, components = self._writable(rid, path)
                with self.directory(root, components[:-1]) as parent:
                    os.stat(components[-1], dir_fd=parent, follow_symlinks=False)
                    name = free_name(trash, components[-1], os.path.isdir(os.path.join(root['path'], *components)))
                    # A rename into ~/.Trash works without the Full Disk Access that listing it needs.
                    os.rename(components[-1], os.path.join(trash, name), src_dir_fd=parent)
                moved.append({'path':'/'.join(components), 'trash':os.path.join(trash, name)})
        return {'ok':True, 'root_id':rid, 'trashed':moved}

    def upload(self, rid, directory, name, data):
        """Write a dropped file (base64) into a folder, keeping both on a name clash."""
        if not isinstance(name, str) or name in ('', '.', '..') or '/' in name or '\0' in name:
            raise FileError('invalid_name', 'The dropped file has no usable name')
        raw = base64.b64decode(data or '', validate=True)
        with self.lock:
            root = self.get_root(rid)
            if root.get('host_id', 'mac') != 'mac' or root['access'] != 'write':
                raise FileError('read_only', 'This workspace is read-only', 403)
            components = parts(directory)
            with self.directory(root, components) as parent:
                name = free_name(os.path.join(root['path'], *components), name)
                fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644, dir_fd=parent)
                with os.fdopen(fd, 'wb') as handle:
                    handle.write(raw)
        return {'ok':True, 'root_id':rid, 'path':'/'.join(components + [name])}

    def unregister(self, rid):
        """Remove a workspace from the list. Its files are untouched."""
        with self.lock:
            root = self.get_root(rid)
            if self.default_root and root['path'] == os.path.realpath(self.default_root) and root.get('host_id') == 'mac':
                raise FileError('default_root', "The Library's own folder is always listed", 409)
            self.roots.pop(root['id'])
            atomic_json(self.data / 'workbench-roots.json', self.roots)
        return {'ok':True, 'removed':root['id']}

    def _local_path(self, root, components, write=True):
        if root.get('host_id', 'mac') != 'mac':
            raise FileError('read_only', 'Remote files are read-only', 403)
        if write and root['access'] != 'write':
            raise FileError('read_only', root['label'] + ' is read-only', 403)
        path = os.path.join(root['path'], *components)
        # Roots are canonical, so a symlinked ancestor shows up as a realpath mismatch.
        if os.path.realpath(os.path.dirname(path)) != os.path.dirname(path) and components:
            raise FileError('outside_root', 'The path contains a symlink', 403)
        return path

    def move(self, items, dest_rid, dest_dir='', copy=False, conflict='ask', confirmed_move=False):
        """Move or copy items into dest_dir. conflict: ask (409 with names), replace, keep_both."""
        if conflict not in ('ask', 'replace', 'keep_both') or not isinstance(items, list) or not 0 < len(items) <= 500:
            raise FileError('invalid_request', 'Invalid move request')
        dest = self.get_root(dest_rid)
        sources = [(self.get_root(item.get('root_id')), parts(item.get('path'))) for item in items]
        hosts = {root.get('host_id', 'mac') for root, _ in sources} | {dest.get('host_id', 'mac')}
        if hosts != {'mac'}:
            import cross_host_transfer
            receipt = cross_host_transfer.transfer(self,items,dest,dest_dir,copy,conflict,confirmed_move)
            if not copy:
                self._record_moves(
                    (d['host_id'], os.path.join(self.get_root(d['root_id'])['path'], d['path']),
                     d['dest_host_id'], os.path.join(dest['path'], d['dest_path']))
                    for d in receipt['moved']
                )
            return receipt
        with self.lock:
            folder = self._local_path(dest, parts(dest_dir))
            if not os.path.isdir(folder) or os.path.islink(folder):
                raise FileError('not_found', 'Destination folder no longer exists', 404)
            plan, names = [], set()
            for root, components in sources:
                if not components:
                    raise FileError('invalid_path', 'A workspace root cannot be moved')
                src = self._local_path(root, components, write=not copy)
                if not os.path.lexists(src):
                    raise FileError('not_found', components[-1] + ' no longer exists', 404)
                if folder == src or folder.startswith(src + '/'):
                    raise FileError('into_itself', f"'{components[-1]}' cannot be moved into itself", 409)
                same = os.path.dirname(src) == folder
                if same and not copy:
                    continue
                if components[-1] in names:
                    raise FileError('duplicate_names', 'Two selected items have the same name: ' + components[-1], 409)
                names.add(components[-1])
                plan.append((root, components, src, same))
            clashes = [c[-1] for _, c, _, same in plan if not same and os.path.lexists(os.path.join(folder, c[-1]))]
            if clashes and conflict == 'ask':
                raise FileError('destination_exists', 'An item with the same name already exists in the destination', 409, names=clashes)
            done = []
            try:
                for root, components, src, same in plan:
                    name, backup = components[-1], None
                    target = os.path.join(folder, name)
                    if os.path.lexists(target):
                        if src.startswith(target + '/'):
                            raise FileError('into_itself', f"'{name}' cannot replace the folder that contains it", 409)
                        if conflict == 'replace' and not same:
                            backup = self._park(target)
                        else:
                            target = os.path.join(folder, free_name(folder, name, os.path.isdir(src)))
                    try:
                        transfer(src, target, copy)
                    except BaseException:
                        if backup:
                            transfer(backup, target)
                        raise
                    done.append({'src': src, 'dst': target, 'backup': backup, 'copy': bool(copy),
                                 'root_id': root['id'], 'path': '/'.join(components),
                                 'dest_root_id': dest['id'], 'dest_path': os.path.relpath(target, dest['path'])})
            except BaseException:
                self._reverse(done)
                raise
            token = uuid.uuid4().hex
            self.moves[token] = done
            self._record_moves(('mac', d['src'], 'mac', d['dst']) for d in done if not d['copy'])
            while len(self.moves) > 20:
                self.moves.pop(next(iter(self.moves)))
        return {'ok': True, 'token': token if done else None,
                'moved': [{k: d[k] for k in ('root_id', 'path', 'dest_root_id', 'dest_path')} for d in done]}

    def _park(self, path):
        """Set aside a replaced or undone-copy item in file-backups so it stays recoverable."""
        folder = self.data / 'file-backups' / 'moves' / (str(time.time_ns()) + '-' + uuid.uuid4().hex[:8])
        folder.mkdir(parents=True)
        target = str(folder / os.path.basename(path))
        transfer(path, target)
        return target

    def _reverse(self, done):
        for d in reversed(done):
            if d['copy']:
                self._park(d['dst'])
            else:
                transfer(d['dst'], d['src'])
            if d['backup']:
                transfer(d['backup'], d['dst'])

    def undo_move(self, token):
        with self.lock:
            done = self.moves.get(str(token))
            if not done:
                raise FileError('not_found', 'Nothing to undo', 404)
            for d in done:
                if not os.path.lexists(d['dst']) or (not d['copy'] and os.path.lexists(d['src'])):
                    raise FileError('undo_conflict', 'The moved items changed since the move, so Undo was not applied', 409)
            self.moves.pop(str(token))
            self._reverse(done)
            self._record_moves(('mac', d['dst'], 'mac', d['src']) for d in done if not d['copy'])
        return {'ok': True, 'restored': len(done)}

    def diff(self, rid, path):
        if self.get_root(rid).get('host_id') != 'mac':
            raise FileError('remote_unsupported', 'Git HEAD comparison is currently local only', 501)
        resource = self.stat(rid, path)
        raw = self.read(rid, path)
        modified, _ = decode_text(raw)
        environment = {k:v for k,v in os.environ.items() if not k.startswith('GIT_')}
        environment.update(GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull,
                           GIT_NO_LAZY_FETCH='1', GIT_TERMINAL_PROMPT='0')
        command = ['git', '--no-pager', '--no-optional-locks', '-c', 'core.fsmonitor=false',
                   '-c', 'core.hooksPath=/dev/null', '-c', 'protocol.allow=never',
                   '-C', os.path.dirname(resource['path'])]
        def git(*args):
            try:
                return subprocess.run(command + list(args), capture_output=True, timeout=5, env=environment, check=True).stdout
            except (OSError, subprocess.SubprocessError):
                raise FileError('no_git', 'A readable local Git HEAD version is unavailable', 422)
        repository = git('rev-parse', '--show-toplevel').decode('utf-8').strip()
        commit = git('rev-parse', '--verify', 'HEAD').decode('ascii').strip()
        if not re.fullmatch(r'[0-9a-fA-F]{40,64}', commit):
            raise FileError('no_git', 'Git HEAD is unavailable', 422)
        relative = os.path.relpath(resource['path'], repository)
        if relative.startswith('../'):
            raise FileError('no_git', 'The file is outside this Git worktree', 422)
        object_name = commit + ':' + relative
        size = int(git('cat-file', '-s', object_name))
        if size > MAX_TEXT:
            raise FileError('too_large', 'Git HEAD content exceeds the 5 MiB comparison limit', 413)
        original, _ = decode_text(git('show', object_name))
        return {'ok':True, 'original':original, 'modified':modified,
                'version':resource['version'], 'label':'Git HEAD'}


_SERVICE = None
_SERVICE_LOCK = threading.Lock()


def instance():
    global _SERVICE
    with _SERVICE_LOCK:
        if _SERVICE is None:
            _SERVICE = FileService(default_root=configured_default_root())
            threading.Thread(target=_SERVICE.watch_local_documents, name='document-history', daemon=True).start()
        return _SERVICE


def guarded(fn):
    def route(req, query, body):
        try:
            return fn(req, query or {}, body or {})
        except FileError as exc:
            return {'ok':False,'code':exc.code,'error':str(exc),**exc.details}, exc.status
        except (ValueError, TypeError, KeyError) as exc:
            return {'ok':False,'code':'invalid_request','error':str(exc)}, 400
        except OSError as exc:
            return {'ok':False,'code':'file_unavailable','error':str(exc)}, 403
    return route


@guarded
def roots(req, query, body):
    service = instance()
    if req.command == 'POST':
        return {'ok':True,'root':service.register(body.get('host_id'),body.get('path'),body.get('access','read'))}
    host = query.get('host_id')
    if host not in ('mac', 'local', None, ''):
        import remote_files
        remote_files.host_config(host)
    values = [root for root in service.roots.values() if not host or root['host_id'] == ('mac' if host == 'local' else host)]
    return {'ok':True,'roots':values,'capabilities':{'local_files':True,'remote_files':True,'remote_write':False}}


@guarded
def listing(req, query, body):
    return instance().list(query.get('root_id'),query.get('path',''),query.get('cursor',0),query.get('q',''))


@guarded
def search(req, query, body):
    if query.get('mode'):
        raw_options = query.get('options', '{}')
        if not isinstance(raw_options, str) or len(raw_options) > 16000:
            raise FileError('invalid_query', 'Remote search options exceed the request limit')
        return instance().search_native(query.get('root_id'), query.get('path', ''), query.get('mode'), query.get('q', ''), json.loads(raw_options))
    return instance().search(query.get('root_id'), query.get('q'))


@guarded
def diff(req, query, body):
    return instance().diff(query.get('root_id'), query.get('path', ''))


@guarded
def metadata(req, query, body):
    return {'ok':True,'resource':instance().stat(query.get('root_id'),query.get('path',''),query.get('file_id'))}


@guarded
def reference(req, query, body):
    return {'ok':True,'resource':instance().reference(body.get('chat_key'),body.get('path'),latest_reply=latest_broker_reply)}


def latest_broker_reply(key):
    """Use the same streamed assistant text the UI can show before indexing."""
    import broker
    store = broker.instance().store
    run = next((row for row in store.list(key=key) if row.get('key') == key), None)
    if not run:
        return {}
    deltas = (event['data'].get('text', '') for event in store.events(run['id'], limit=512)
              if event['event'] == 'delta')
    return {'cwd': run.get('cwd'), 'host_id': run.get('host_id'),
            'turns': [{'text': run.get('text') or ''}, {'text': ''.join(deltas)}]}


@guarded
def save(req, query, body):
    return {'ok':True,'resource':instance().save(body.get('root_id'),body.get('path'),body.get('base_version'),body.get('text'))}



@guarded
def document_save(req, query, body):
    return {'ok':True,'resource':instance().save_document(body.get('root_id'), body.get('path',''),
                                                          body.get('file_id'), body.get('base_version'),
                                                          body.get('text'))}


@guarded
def document_rewind(req, query, body):
    resource = instance().rewind_document(body.get('root_id'), body.get('path', ''),
                                         body.get('file_id'), body.get('base_version'))
    return {'ok':True, 'resource':resource}


@guarded
def document_review(req, query, body):
    values = body if req.command == 'POST' else query
    return {'ok':True,**instance().document_review(values.get('root_id'), values.get('path',''),
                                                   values.get('file_id'), body if req.command == 'POST' else None)}

@guarded
def copy(req, query, body):
    return {'ok':True,'resource':instance().copy(body.get('root_id'),body.get('path'),body.get('base_version'),body.get('destination'),body.get('text'))}


@guarded
def create(req, query, body):
    return instance().create(body.get('root_id'), body.get('path'), body.get('type', 'file'))


@guarded
def move(req, query, body):
    return instance().move(body.get('items'), body.get('dest_root_id'), body.get('dest_dir', ''), bool(body.get('copy')), body.get('conflict', 'ask'), body.get('confirmed_move') is True)


@guarded
def rename(req, query, body):
    return instance().rename(body.get('root_id'), body.get('path'), body.get('name'))


@guarded
def trash(req, query, body):
    return instance().trash(body.get('root_id'), body.get('paths'))


@guarded
def upload(req, query, body):
    return instance().upload(body.get('root_id'), body.get('dir', ''), body.get('name'), body.get('data'))


@guarded
def remove_root(req, query, body):
    return instance().unregister(body.get('root_id'))


@guarded
def undo_move(req, query, body):
    return instance().undo_move(body.get('token'))


@guarded
def content(req, query, body):
    service = instance()
    root, remote_path = service.remote_target(query.get('root_id'),query.get('path',''),query.get('file_id'))
    if root:
        return remote_content(req, query, root, remote_path)
    with service.opened(query.get('root_id'),query.get('path',''),query.get('file_id')) as (fd, info, root, components, _):
        size = info.st_size
        start, end, status = 0, size-1, 200
        requested = req.headers.get('Range')
        if requested:
            match = re.fullmatch(r'bytes=(\d*)-(\d*)', requested)
            if not match or not any(match.groups()) or size == 0:
                raise FileError('invalid_range', 'Unsupported byte range', 416, size=size)
            a, b = match.groups()
            start = int(a) if a else max(0,size-int(b))
            end = min(int(b),size-1) if b and a else size-1
            if start >= size or end < start or (not a and int(b) == 0):
                raise FileError('invalid_range','Range is outside this file',416,size=size)
            status = 206
        length = max(0,end-start+1)
        ctype = mime(components[-1])
        executable = ctype in ('text/html','application/xhtml+xml')
        req.send_response(status)
        req.send_header('Content-Type','text/plain; charset=utf-8' if executable else ctype)
        req.send_header('X-Content-Type-Options','nosniff')
        req.send_header('Content-Security-Policy',content_policy(ctype))
        req.send_header('Cache-Control','no-store')
        req.send_header('Accept-Ranges','bytes')
        req.send_header('Content-Length',str(length))
        req.send_header('Content-Disposition',"inline; filename*=UTF-8''" + quote(components[-1]))
        if status == 206:
            req.send_header('Content-Range',f'bytes {start}-{end}/{size}')
        req.end_headers()
        if req.command != 'HEAD':
            os.lseek(fd,start,os.SEEK_SET)
            while length:
                chunk = os.read(fd,min(length,256*1024))
                if not chunk:
                    break
                req.wfile.write(chunk)
                length -= len(chunk)


def remote_content(req, query, root, remote_path):
    import remote_files
    resource = instance().stat(query.get('root_id'), query.get('path',''), query.get('file_id'))
    size = resource['size']
    start, end, status = 0, size-1, 200
    requested = req.headers.get('Range')
    if requested:
        match = re.fullmatch(r'bytes=(\d*)-(\d*)', requested)
        if not match or not any(match.groups()) or size == 0:
            raise FileError('invalid_range', 'Unsupported byte range', 416, size=size)
        a, b = match.groups()
        start = int(a) if a else max(0, size-int(b))
        end = min(int(b),size-1) if b and a else size-1
        if start >= size or end < start or (not a and int(b) == 0):
            raise FileError('invalid_range', 'Range is outside this file', 416, size=size)
        status = 206
    length = max(0,end-start+1)
    if length > MAX_READ and req.command != 'HEAD':
        raise FileError('too_large', 'Remote file reads are limited to 64 MiB per response; use byte ranges', 413)
    raw = b''
    if req.command != 'HEAD':
        value = remote_files.request(root['host_id'],'read',{'root':root['path'],'path':remote_path,'offset':start,'length':length})
        raw = base64.b64decode(value['data_base64'], validate=True)
        if len(raw) != length:
            raise FileError('version_conflict', 'Remote file changed during transfer; reload it', 409)
    executable = resource['mime'] in ('text/html','application/xhtml+xml')
    req.send_response(status)
    req.send_header('Content-Type', 'text/plain; charset=utf-8' if executable else resource['mime'])
    req.send_header('Content-Length', str(length))
    req.send_header('X-Content-Type-Options','nosniff')
    req.send_header('Content-Security-Policy',content_policy(resource['mime']))
    req.send_header('Cache-Control','no-store')
    req.send_header('Accept-Ranges','bytes')
    req.send_header('Content-Disposition', "inline; filename*=UTF-8''"+quote(resource['name']))
    if status == 206:
        req.send_header('Content-Range',f'bytes {start}-{end}/{size}')
    req.end_headers()
    if req.command != 'HEAD':
        req.wfile.write(raw)


ROUTES = {('GET','/api/files/roots'):roots,('POST','/api/files/roots'):roots,
          ('GET','/api/files/list'):listing,('GET','/api/files/search'):search,('GET','/api/files/stat'):metadata,
          ('GET','/api/files/diff'):diff,
          ('POST','/api/files/document-save'):document_save,('POST','/api/files/document-rewind'):document_rewind,
          ('GET','/api/files/document-review'):document_review,('POST','/api/files/document-review'):document_review,
          ('GET','/api/files/content'):content,('HEAD','/api/files/content'):content,
          ('POST','/api/files/reference'):reference,('POST','/api/files/save'):save,
          ('POST','/api/files/create'):create,('POST','/api/files/copy'):copy,('POST','/api/files/move'):move,('POST','/api/files/move/undo'):undo_move,
          ('POST','/api/files/rename'):rename,('POST','/api/files/trash'):trash,('POST','/api/files/upload'):upload,
          ('POST','/api/files/roots/remove'):remove_root}
BACKGROUND = []
