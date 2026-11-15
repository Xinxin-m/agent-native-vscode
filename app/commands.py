"""Read-only local command discovery. Never runs skill code or starts a provider."""
from pathlib import Path
import re


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


def list_commands(req, query, body):
    query = query or {}
    try:
        provider = query.get('provider') or 'claude'
        host_id = query.get('host_id') or query.get('host') or 'mac'
        result = command_catalog(provider, query.get('cwd') or '', host_id)
        if host_id not in ('mac', 'local', ''):
            from hosts import get_host
            host = get_host(host_id)
            entries = (host.get('commands') or {}).get(provider)
            if entries and host.get('state') == 'online':
                result.update(commands=entries, scope='remote')
                result.pop('notice', None)
            else:
                result['notice'] = 'Connect this host to discover its installed skills.'
        return result
    except ValueError as exc:
        return {'ok': False, 'error': str(exc)}, 400


ROUTES = {('GET', '/api/commands'): list_commands}
BACKGROUND = []
