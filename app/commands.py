"""Read-only local command discovery. Never runs skill code or starts a provider."""
import copy
import json
from pathlib import Path
import re
import select
import subprocess
import tempfile
import threading
import time

_NATIVE = {}
_NATIVE_LOCK = threading.Lock()
NATIVE_TTL_S = 600


def skill_roots(provider, cwd=''):
    home = Path.home()
    roots = ([home / '.claude/skills'] if provider == 'claude' else
             [home / '.codex/skills', home / '.agents/skills'])
    if cwd:
        path = Path(cwd).expanduser().resolve()
        if path.is_dir():
            # Closest project definition wins over its ancestors and user skills.
            project = []
            for directory in (path, *path.parents):
                if provider == 'claude':
                    project.append(directory / '.claude/skills')
                else:
                    project.extend([directory / '.agents/skills', directory / '.codex/skills'])
                if directory == home:
                    break
            roots = project + roots
    return roots


def _metadata(path):
    # Read only the frontmatter, with a firm bound. Never return instructions.
    try:
        with path.open() as handle:
            header = handle.read(16384)
    except (OSError, UnicodeError):
        return {}
    if not header.startswith('---\n'):
        return {}
    front, delimiter, _ = header[4:].partition('\n---')
    if not delimiter:
        return {}
    data = {}
    lines = front.splitlines()
    for i, line in enumerate(lines):
        match = re.match(r'^(name|description|user-invocable):\s*(.*)$', line)
        if not match:
            continue
        key, value = match.groups()
        if value in ('|', '>', '|-', '>-') or not value:
            continuation = []
            for next_line in lines[i+1:]:
                if next_line and not next_line[0].isspace():
                    break
                continuation.append(next_line.strip())
            value = ' '.join(continuation)
        if len(value) > 1 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        data[key] = value
    return data


def command_catalog(provider='claude', cwd='', host_id='mac'):
    if provider not in ('claude', 'codex'):
        raise ValueError('unknown provider')
    entries = [{'name': 'compact', 'description': 'Summarize this session to free context',
                'kind': 'command', 'insert': '/compact', 'operation': 'compact',
                'requires_session': True}]
    result = {'ok': True, 'provider': provider, 'scope': 'local', 'commands': entries}
    if host_id not in ('mac', 'local', ''):
        result.update(scope='remote', notice='Remote skills are not inventoried here. Only the native compact command is listed.')
        return result
    seen = set()
    for root in skill_roots(provider, cwd):
        # One skill directory per name; Codex builtins also use .system/name.
        files = sorted(root.glob('*/SKILL.md'))
        if provider == 'codex':
            files += sorted((root / '.system').glob('*/SKILL.md'))
        for path in files:
            meta = _metadata(path)
            name = meta.get('name') or path.parent.name
            if not meta or meta.get('user-invocable', '').lower() == 'false':
                continue
            if not re.fullmatch(r'[\w.:-]{1,120}', name) or name in seen:
                continue
            seen.add(name)
            entries.append({'name': name, 'description': meta.get('description', '')[:600],
                            'kind': 'skill', 'insert': ('/' if provider == 'claude' else '$') + name})
    entries[1:] = sorted(entries[1:], key=lambda item: item['name'].lower())
    return result


def native_claude_commands(cwd=''):
    """Commands the installed Claude CLI advertises on initialize, the same source the
    Library Code app uses. No prompt is sent and no session is persisted."""
    key = cwd if cwd and Path(cwd).is_dir() else tempfile.gettempdir()
    with _NATIVE_LOCK:
        cached = _NATIVE.get(key)
        if cached and time.time() - cached[0] < NATIVE_TTL_S:
            return cached[1]
    import clis
    entries = []
    try:
        child = subprocess.Popen([clis.cli('claude'), '--bare', '-p', '--input-format', 'stream-json',
                                  '--output-format', 'stream-json', '--verbose', '--no-session-persistence'],
                                 cwd=key, env=clis.env(CHAT_LIBRARY_MANAGED='1'), text=True,
                                 stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    except OSError:
        return entries
    try:
        child.stdin.write(json.dumps({'type': 'control_request', 'request_id': 'library-commands',
                                      'request': {'subtype': 'initialize'}}) + '\n')
        child.stdin.flush()
        deadline = time.time() + 12
        while time.time() < deadline and select.select([child.stdout], [], [], deadline - time.time())[0]:
            line = child.stdout.readline()
            if not line:
                break
            try:
                item = json.loads(line)
            except ValueError:
                continue
            if item.get('type') == 'control_response' and (item.get('response') or {}).get('request_id') == 'library-commands':
                for c in (item['response'].get('response') or {}).get('commands') or []:
                    name = c.get('name') if isinstance(c, dict) else None
                    if isinstance(name, str) and re.fullmatch(r'[A-Za-z0-9][\w.:-]{0,119}', name) and not name.startswith(('workflow-launch-', '__')):
                        entries.append({'name': name, 'description': str(c.get('description') or '')[:600],
                                        'argumentHint': str(c.get('argumentHint') or '')[:200],
                                        'kind': 'command' if c.get('builtin') else 'skill',
                                        'insert': '/' + name, 'native': True})
                break
    except (OSError, ValueError):
        pass
    finally:
        child.kill()
        child.wait()
    with _NATIVE_LOCK:
        _NATIVE[key] = (time.time(), entries)
    return entries


_CATALOGS = {}
CATALOG_TTL_S = 30


def cached_catalog(provider, cwd, host_id):
    """Skill scans read every SKILL.md; under a busy server that took seconds per
    keystroke of the slash menu. Reuse a scan for 30 seconds."""
    key = (provider, cwd, host_id)
    hit = _CATALOGS.get(key)
    if not hit or time.time() - hit[0] > CATALOG_TTL_S:
        hit = _CATALOGS[key] = (time.time(), command_catalog(provider, cwd, host_id))
    return copy.deepcopy(hit[1])


def list_commands(req, query, body):
    query = query or {}
    try:
        provider = query.get('provider') or 'claude'
        host_id = query.get('host_id') or query.get('host') or 'mac'
        result = cached_catalog(provider, query.get('cwd') or '', host_id)
        if host_id not in ('mac', 'local', ''):
            from hosts import get_host
            host = get_host(host_id)
            entries = (host.get('commands') or {}).get(provider)
            if entries and host.get('state') == 'online':
                result.update(commands=entries, scope='remote')
                result.pop('notice', None)
            else:
                result['notice'] = 'Connect this host to discover its installed skills.'
        elif provider == 'claude':
            names = {c['name'] for c in result['commands']}
            result['commands'] += [c for c in native_claude_commands(query.get('cwd') or '') if c['name'] not in names]
        return result
    except ValueError as exc:
        return {'ok': False, 'error': str(exc)}, 400


ROUTES = {('GET', '/api/commands'): list_commands}
BACKGROUND = []
