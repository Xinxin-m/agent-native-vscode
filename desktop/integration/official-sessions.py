#!/usr/bin/env python3
"""Local Official-panel routing. No provider requests or credential mutations.

Installed beside claude-wrapper in the Official shared/bin folder. The installer
records the account-registry path in shared/config.json. Runtime does not import
or depend on the development checkout or the running Library server.
"""
import argparse
import calendar
import contextlib
import fcntl
import json
import math
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile
import time

PROFILE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_.-]{0,100}$')
SID = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$')
# Pure account selection, kept in parity with host_connector.resolve_account.
USAGE_FRESH_S = 600


def _model_family(model):
    value = (model or '').lower()
    for family in ('opus', 'sonnet', 'haiku', 'fable'):
        if family in value:
            return family
    return value


def _quota_status(record, model, now, max_age):
    """Unknown evidence stays unknown, including stale fallback observations."""
    if record.get('logged_in') is not True or record.get('error'):
        return None, 'usage_unavailable'
    timestamps = [record.get(k) for k in ('fetched_at', 'cached_at', 'rollout_at') if record.get(k) is not None]
    if not timestamps or any(isinstance(t, bool) or not isinstance(t, (int, float)) or
                             not math.isfinite(t) or t > now + 60 or now - t > max_age for t in timestamps):
        return None, 'usage_stale'
    values = []
    family = _model_family(model)
    for window in record.get('windows') or []:
        kind = str(window.get('kind') or '')
        scope = window.get('model') or (kind[3:] if kind.startswith('7d_') else '')
        if scope and family and _model_family(scope) != family:
            continue
        if scope and not family:
            return None, 'model_unknown'
        value = window.get('percent')
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0 or value > 100:
            return None, 'usage_unknown'
        reset = window.get('resets_at')
        if reset:
            try:
                reset_ts = float(reset) if isinstance(reset, (float, int)) else calendar.timegm(time.strptime(reset, '%Y-%m-%dT%H:%M:%SZ'))
            except (ValueError, TypeError):
                return None, 'usage_unknown'
            if reset_ts <= now:
                return None, 'usage_stale'
        values.append(value)
    return (max(values), 'known') if values else (None, 'usage_unknown')


def resolve_account(data, tool, model=None, account_id=None, session_account_id=None,
                    host_id='mac', auto=None, now=None, max_age=USAGE_FRESH_S):
    """Pure shared launch resolver. Never switches, writes credentials or retries work.

    Explicit and native session ownership is authoritative. Automatic selection
    requires fresh measured headroom and known eligibility; unknown snapshots
    retain the configured account instead of claiming spare quota.
    """
    if tool not in ('claude', 'codex'):
        raise ValueError('Unsupported chat tool')
    if account_id and session_account_id and account_id != session_account_id:
        raise ValueError('Native resume requires the original account')
    now = time.time() if now is None else now
    auto = auto if auto is not None else data.get('auto_switch', {})
    profiles = [a for a in data.get('accounts', []) if a.get('tool') == tool]
    active = data.get('state', {}).get('active', {}).get(tool) or tool + '-default'
    wanted = session_account_id or account_id or active
    if not isinstance(wanted, str) or not PROFILE.fullmatch(wanted):
        raise ValueError('Invalid account identifier')
    rows = []
    for profile in profiles:
        rec = data.get('cache', {}).get(profile['id']) or profile.get('usage') or {}
        models = profile.get('models')
        eligible = profile.get('eligible') is not False and not profile.get('disabled')
        if models is not None:
            eligible = eligible and bool(model) and any(_model_family(m) == _model_family(model) for m in models)
        percent, status = _quota_status(rec, model, now, max_age)
        rows.append({'account_id': profile['id'], 'eligible': bool(eligible),
                     'percent': percent if eligible else None,
                     'status': status if eligible else 'ineligible'})
    current = next((row for row in rows if row['account_id'] == wanted), None)
    if current is None:
        raise ValueError('Selected account is not configured on this host')
    if not current['eligible']:
        raise ValueError('Selected account is not eligible for the requested model')
    reason = 'session_binding' if session_account_id else 'explicit_account' if account_id else 'configured_account'
    selected = wanted
    if not account_id and not session_account_id and auto.get('enabled', True) and auto.get('mode') != 'off':
        try:
            threshold = float(auto.get('threshold', 90))
        except (TypeError, ValueError):
            threshold = 90
        if not math.isfinite(threshold) or not 0 < threshold <= 100:
            threshold = 90
        if current['percent'] is None:
            reason = current['status']
        elif current['percent'] >= threshold:
            alternatives = [r for r in rows if r['eligible'] and r['percent'] is not None and r['percent'] < threshold]
            if alternatives:
                selected = min(alternatives, key=lambda r: (r['percent'], r['account_id']))['account_id']
                reason = 'more_model_headroom'
            else:
                reason = 'no_known_headroom'
    return {'account_id': selected, 'previous_account_id': active, 'host_id': host_id,
            'provider': tool, 'model': model or None, 'reason': reason,
            'changed': selected != active, 'at': now, 'candidates': rows}


def read_json(path, default=None):
    try:
        return json.loads(Path(path).read_text())
    except FileNotFoundError:
        return {} if default is None else default


def atomic_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=path.name + '.', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as out:
            json.dump(data, out, indent=2)
            out.write('\n')
            out.flush()
            os.fsync(out.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


@contextlib.contextmanager
def locked(shared):
    shared.mkdir(parents=True, exist_ok=True)
    fd = os.open(shared / 'routes.lock', os.O_CREAT | os.O_RDWR, 0o600)
    with os.fdopen(fd, 'a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def registry(shared):
    config = read_json(shared / 'config.json')
    account_file = os.environ.get('LIBRARY_ACCOUNTS_FILE') or config.get('accounts_file')
    if account_file and not Path(account_file).is_file():
        raise ValueError('Configured Library account registry is missing')
    data = read_json(account_file) if account_file else {}
    data.setdefault('accounts', [])
    for tool in ('claude', 'codex'):
        if not any(a.get('id') == tool + '-default' for a in data['accounts']):
            data['accounts'].append({'id': tool + '-default', 'tool': tool,
                                     'config_dir': str(Path.home() / ('.' + tool))})
    state = data.setdefault('state', {})
    state.setdefault('active', {'claude': 'claude-default', 'codex': 'codex-default'})
    state.setdefault('global_active', dict(state['active']))
    if config.get('library_config_file'):
        data['auto_switch'] = read_json(config['library_config_file']).get('auto_switch', {})
    return data


def profile(data, tool, account):
    if not isinstance(account, str) or not PROFILE.fullmatch(account):
        raise ValueError('Invalid account identifier')
    row = next((a for a in data['accounts'] if a.get('id') == account and a.get('tool') == tool), None)
    if not row:
        raise ValueError('Selected account is not configured on this host')
    active = data.get('state', {}).get('global_active', {}).get(tool)
    stashed = account.endswith('-default') and active and active != account
    if stashed and not row.get('login_config_dir'):
        raise ValueError('Default account is stashed; restore it explicitly before launching')
    directory = row.get('login_config_dir') if stashed else str(Path.home() / ('.' + tool)) if active == account else row.get('config_dir')
    if not directory:
        raise ValueError('Selected account has no local configuration directory')
    directory = Path(directory).expanduser().resolve()
    if not directory.is_dir():
        raise ValueError('Selected account configuration directory is missing on this host')
    return directory


def transcript_cwd(transcript):
    cwd = None
    with Path(transcript).open(errors='replace') as lines:
        for line in lines:
            try:
                item = json.loads(line)
            except ValueError:
                continue
            candidate = item.get('cwd')
            if isinstance(candidate, str) and candidate:
                cwd = candidate
    return cwd


def route(shared, args):
    if not SID.fullmatch(args.session):
        raise ValueError('Invalid session identifier')
    if args.host != 'mac':
        raise ValueError('Remote Official sessions are disabled; use a Library pane')
    transcript = Path(args.transcript).expanduser().resolve(strict=True)
    if not transcript.is_file() or transcript.name != args.session + '.jsonl':
        raise ValueError('Missing or mismatched Claude transcript')
    cwd = Path(transcript_cwd(transcript) or args.cwd).expanduser().resolve(strict=True)
    if not cwd.is_dir():
        raise ValueError('Session folder is missing')
    data = registry(shared)
    account = args.account
    if getattr(args, 'prefer_saved_account', False):
        with locked(shared):
            saved = read_json(shared / 'session-routes.json').get(args.session, {})
        account = saved.get('account_id') or account
    selection = resolve_account(data, 'claude', model=args.model, account_id=account)
    directory = profile(data, 'claude', selection['account_id'])
    dry_run = getattr(args, 'dry_run', False)
    links = profile_links(shared, selection['account_id'], dry_run=dry_run)
    if str(directory / 'projects') in links['conflicts']:
        raise ValueError('Selected profile has an existing projects directory; preserve it before sharing: ' + str(directory / 'projects'))
    result = {'cwd': str(cwd), 'account_id': selection['account_id'], 'host': 'mac',
              'config_dir': str(directory), 'default_account': directory == (Path.home() / '.claude').resolve(),
              'transcript': str(transcript), 'workspace': str(Path(args.workspace or shared / 'workspace').resolve()),
              'selection': selection, 'profile_conflicts': links['conflicts']}
    if not dry_run:
        with locked(shared):
            routes = read_json(shared / 'session-routes.json')
            routes[args.session] = result
            atomic_json(shared / 'session-routes.json', routes)
    return result


def profile_links(shared, account, dry_run=False):
    with locked(shared):
        return _profile_links(shared, account, dry_run)


def _profile_links(shared, account, dry_run=False):
    data = registry(shared)
    target = profile(data, 'claude', account)
    return link_profile_directory(target, account, dry_run)


def link_profile_directory(target, account, dry_run=False):
    source = (Path.home() / '.claude').resolve()
    if target == source:
        return {'account_id': account, 'created': [], 'conflicts': []}
    names = ('projects', 'settings.json', 'CLAUDE.md', 'skills', 'agents', 'hooks', 'plugins')
    conflicts, created = [], []
    for name in names:
        link, original = target / name, source / name
        if link.is_symlink() and link.resolve() == original.resolve():
            continue
        if os.path.lexists(link):
            conflicts.append(str(link))
    # Existing per-profile resources stay intact; missing entries can be shared.
    for name in names:
        link = target / name
        if not dry_run and not os.path.lexists(link):
            link.symlink_to(source / name, target_is_directory=name not in ('settings.json', 'CLAUDE.md'))
            created.append(str(link))
    return {'account_id': account, 'created': created, 'conflicts': conflicts}


def profile_links_all(shared):
    data = registry(shared)
    targets, results = {}, []
    for item in data['accounts']:
        if item.get('tool') == 'claude':
            try:
                targets[profile(data, 'claude', item['id'])] = item['id']
            except ValueError as error:
                results.append({'account_id': item['id'], 'created': [], 'conflicts': [], 'error': str(error)})
    root = Path.home() / '.claude-profiles'
    for folder in sorted(root.iterdir()) if root.is_dir() else []:
        if folder.is_dir():
            targets.setdefault(folder.resolve(), folder.name)
    with locked(shared):
        results.extend(link_profile_directory(folder, account) for folder, account in targets.items())
    return {'profiles': results, 'created': [p for r in results for p in r['created']],
            'conflicts': [p for r in results for p in r['conflicts']]}


def account_list(shared, provider):
    data = registry(shared)
    rows = []
    for item in data['accounts']:
        if item.get('tool') != provider or item.get('disabled') or item.get('eligible') is False:
            continue
        usage = data.get('cache', {}).get(item['id']) or item.get('usage') or {}
        rows.append({'id': item['id'], 'label': item.get('label') or item['id'],
                     'email': usage.get('email') or item.get('email'),
                     'logged_in': usage.get('logged_in'), 'config_dir': item.get('config_dir')})
    return {'accounts': rows, 'active': data['state']['active'].get(provider)}


def codex_window(shared, args):
    data = registry(shared)
    selection = resolve_account(data, 'codex', account_id=args.account)
    directory = profile(data, 'codex', selection['account_id'])
    config = read_json(shared / 'config.json')
    app = args.app or config.get('app')
    source_profile = args.user_data_dir or config.get('user_data_dir')
    extensions = args.extensions_dir or config.get('extensions_dir')
    if not app or not source_profile or not extensions:
        raise ValueError('Official app, user-data directory and extensions directory must be configured')
    session = getattr(args, 'session', None)
    request_id = None
    if session:
        if not re.fullmatch(r'[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}', session):
            raise ValueError('Invalid Codex session identifier')
        source_sessions = Path(os.environ.get('CODEX_HOME') or Path.home() / '.codex') / 'sessions'
        if source_sessions.resolve() != (directory / 'sessions').resolve():
            raise ValueError('Account continuation requires the same shared Codex session store')
        if not any(file.is_file() for file in (directory / 'sessions').rglob('*-' + session + '.jsonl')):
            raise ValueError('The selected account cannot read this Codex session from its shared store')
        request_id = os.urandom(16).hex()
    target = shared / 'profiles' / ('codex-' + args.account)
    if request_id:
        # An existing Electron profile forwards CLI arguments to its running process,
        # losing new environment variables. This explicit session request needs its own process.
        target = target / 'requests' / request_id
    settings = target / 'User/settings.json'
    with locked(shared):
        if not settings.exists():
            source = Path(source_profile).expanduser() / 'User/settings.json'
            values = read_json(source)
            label = next((a.get('label') or a['id'] for a in data['accounts'] if a['id'] == args.account), args.account)
            values.update({'library.chatEngine': 'official', 'window.restoreWindows': 'none',
                           'window.title': '${activeEditorShort}${separator}Codex ' + label + '${separator}${appName}'})
            atomic_json(settings, values)
    workspace = shared / 'workspace'
    workspace.mkdir(parents=True, exist_ok=True)
    argv = ['open', '-g', '-n', '-a', app, '--env', 'CODEX_HOME=' + str(directory)]
    if request_id:
        argv += ['--env', 'LIBRARY_OFFICIAL_OPEN_SESSION=codex-' + session, '--env', 'LIBRARY_OFFICIAL_OPEN_REQUEST=' + request_id]
    for name in ('HOME', 'CLAUDE_CONFIG_DIR', 'LIBRARY_OFFICIAL_SHARED', 'LIBRARY_ACCOUNTS_FILE'):
        if os.environ.get(name):
            argv += ['--env', name + '=' + os.environ[name]]
    argv += ['--args', '--user-data-dir', str(target), '--extensions-dir', extensions,
             '--force-disable-user-env', '--skip-welcome', '--skip-release-notes', str(workspace)]
    return {'argv': argv, 'account_id': args.account, 'config_dir': str(directory), 'user_data_dir': str(target)}


def wrapper(shared, executable, arguments):
    session = None
    resuming = False
    for index, arg in enumerate(arguments):
        if arg.startswith('--resume='):
            resuming = True
            session = arg.split('=', 1)[1]
        elif arg == '--resume':
            resuming = True
            session = arguments[index + 1] if index + 1 < len(arguments) else None
    env = dict(os.environ)
    current = {'cwd': os.getcwd(), 'account_id': 'inherited-default', 'host': 'mac'}
    if resuming:
        if not session or not SID.fullmatch(session):
            raise ValueError('Invalid resume session identifier')
        current = read_json(shared / 'session-routes.json').get(session)
        if current is None:
            root = Path(env.get('CLAUDE_CONFIG_DIR') or Path.home() / '.claude') / 'projects'
            candidates = [p for p in root.glob('*/' + session + '.jsonl') if p.is_file() and not p.is_symlink()]
            if len(candidates) != 1:
                raise ValueError('No unambiguous route or transcript for session ' + session)
            current = {'cwd': transcript_cwd(candidates[0]), 'account_id': 'inherited-default', 'host': 'mac'}
        if current.get('host') != 'mac':
            raise ValueError('Remote Official sessions are disabled; use a Library pane')
        # A global account switch can move credentials between ~/.claude and a
        # stash after the route was saved. Resolve the logical account again;
        # a stale physical config_dir must never select another identity.
        if current.get('account_id') != 'inherited-default':
            directory = profile(registry(shared), 'claude', current['account_id'])
            current = {**current, 'config_dir': str(directory),
                       'default_account': directory == (Path.home() / '.claude').resolve()}
        cwd = current.get('cwd')
        if not cwd or not Path(cwd).is_dir():
            raise ValueError('Session folder is missing: ' + str(cwd))
        if current.get('config_dir'):
            if not Path(current['config_dir']).is_dir():
                raise ValueError('Selected account configuration directory is missing')
            preserve = read_json(shared / 'config.json').get('preserve_default_env', False)
            if current.get('default_account') and not preserve:
                env.pop('CLAUDE_CONFIG_DIR', None)
            else:
                env['CLAUDE_CONFIG_DIR'] = current['config_dir']
        os.chdir(cwd)
    elif (('--output-format' in arguments and arguments[arguments.index('--output-format') + 1:arguments.index('--output-format') + 2] == ['stream-json']) or '--output-format=stream-json' in arguments) and ('--input-format' in arguments or '--input-format=stream-json' in arguments):
        # Only a new chat adopts the chosen default. Probes/login and existing
        # provider processes keep their environment and account unchanged.
        data = registry(shared)
        account = data.get('state', {}).get('active', {}).get('claude') or 'claude-default'
        directory = profile(data, 'claude', account)
        current['account_id'] = account
        if directory == (Path.home() / '.claude').resolve() and not read_json(shared / 'config.json').get('preserve_default_env', False):
            env.pop('CLAUDE_CONFIG_DIR', None)
        else:
            env['CLAUDE_CONFIG_DIR'] = str(directory)
    entry = {'at': time.time(), 'pid': os.getpid(), 'args': arguments, 'cwd': os.getcwd(),
             'account_id': current['account_id'], 'host': current['host'], 'session_id': session}
    with locked(shared):
        fd = os.open(shared / 'wrapper-launches.jsonl', os.O_CREAT | os.O_APPEND | os.O_WRONLY, 0o600)
        with os.fdopen(fd, 'a') as out:
            out.write(json.dumps(entry) + '\n')
    os.execvpe(executable, [executable, *arguments], env)


def classify_transcript(shared, args):
    # Packaged beside this helper; the same pure module runs in the broker/indexer.
    try:
        import auth_failure
    except ImportError:
        sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'chat-dashboard'))
        import auth_failure
    transcript = Path(args.transcript).expanduser().resolve(strict=True)
    result = auth_failure.transcript_failure(args.provider, transcript, args.account)
    sid = result.get('session_id') or (transcript.stem if args.provider == 'claude' else None)
    data = registry(shared)
    route = read_json(shared / 'session-routes.json').get(sid) or {}
    account = args.account or route.get('account_id')
    if not account:
        home = Path(os.environ.get('CLAUDE_CONFIG_DIR' if args.provider == 'claude' else 'CODEX_HOME') or Path.home() / ('.' + args.provider)).resolve()
        default = (Path.home() / ('.' + args.provider)).resolve()
        if home == default:
            account = data.get('state', {}).get('global_active', {}).get(args.provider)
        else:
            candidates = [row['id'] for row in data['accounts'] if row.get('tool') == args.provider and
                          any(value and Path(value).expanduser().resolve() == home for value in (row.get('config_dir'), row.get('login_config_dir')))]
            if len(candidates) == 1:
                account = candidates[0]
    row = next((a for a in data['accounts'] if a.get('id') == account and a.get('tool') == args.provider), None)
    if result.get('failure'):
        result['failure']['account_id'] = row['id'] if row else ''
        result['failure']['account_label'] = (row.get('label') or row['id']) if row else 'Unknown account'
    result['session_id'] = sid
    return result


def _alias_stat(file):
    try:
        value = Path(file).lstat()
    except FileNotFoundError:
        return None
    import stat
    return value if stat.S_ISREG(value.st_mode) else False


def _alias_identity(value):
    return (value.st_dev, value.st_ino) if value else None


def _alias_manifest(shared):
    data = read_json(shared / 'session-aliases.json', {'version': 1, 'aliases': {}})
    if data.get('version') != 1 or not isinstance(data.get('aliases'), dict):
        raise ValueError('Official transcript alias ownership is unreadable')
    return data


def _alias_live_owners(owners):
    result = {}
    for token, owner in owners.items():
        try:
            pid = int(owner['pid'])
            if pid <= 1:
                continue
            os.kill(pid, 0)  # Liveness only, never a signal or process termination.
        except ProcessLookupError:
            continue
        except (PermissionError, KeyError, ValueError, TypeError):
            result[token] = owner  # Unknown owner remains protected.
        else:
            result[token] = owner
    return result


def alias_acquire(shared, args):
    """A same-inode lookup alias accepted by vendor O_NOFOLLOW readers.

    Refuse inode drift, unowned paths and cross-device copying. Persist ownership
    under the route lock before returning a link to the editor integration.
    """
    if not SID.fullmatch(args.session) or not SID.fullmatch(args.owner) or args.pid <= 1:
        raise ValueError('Invalid transcript alias owner')
    source = Path(args.transcript).resolve(strict=True)
    requested = Path(args.file).absolute()
    requested.parent.mkdir(parents=True, exist_ok=True)
    file = requested.parent.resolve() / requested.name
    if file.name != args.session + '.jsonl':
        raise ValueError('Transcript alias does not match its session')
    with locked(shared):
        data = _alias_manifest(shared)
        aliases = data['aliases']
        row = aliases.get(str(file))
        original, current = _alias_stat(source), _alias_stat(file)
        if not original:
            raise ValueError('Canonical transcript is missing or not a regular file')
        if file == source:
            return {'file': str(file), 'target': str(source), 'owned': False}
        if row:
            expected = (row.get('dev'), row.get('ino'))
            if row.get('source') != str(source) or _alias_identity(original) != expected or _alias_identity(current) != expected:
                raise ValueError('Official transcript alias or canonical source changed; both paths were preserved')
            row['owners'] = _alias_live_owners(row.get('owners', {}))
        elif current is not None:
            if current and _alias_identity(current) == _alias_identity(original):
                return {'file': str(file), 'target': str(source), 'owned': False}
            raise ValueError('A different transcript already occupies the official lookup path')
        else:
            try:
                os.link(source, file, follow_symlinks=False)
            except OSError as error:
                if error.errno == 18:
                    raise ValueError('Official transcript lookup requires the same filesystem; no transcript was copied') from error
                raise
            actual = _alias_stat(file)
            if _alias_identity(actual) != _alias_identity(original) or _alias_identity(_alias_stat(source)) != _alias_identity(original):
                raise ValueError('Canonical transcript changed while linking; all paths were preserved')
            row = {'source': str(source), 'dev': actual.st_dev, 'ino': actual.st_ino,
                   'session_id': args.session, 'kind': 'hardlink', 'owners': {}}
            aliases[str(file)] = row
        row['owners'][args.owner] = {'pid': args.pid}
        atomic_json(shared / 'session-aliases.json', data)
        return {'file': str(file), 'target': str(source), 'owned': True, 'owner': args.owner,
                'dev': row['dev'], 'ino': row['ino']}


def alias_release(shared, args):
    requested = Path(args.file).absolute()
    file = requested.parent.resolve() / requested.name
    with locked(shared):
        data = _alias_manifest(shared)
        row = data['aliases'].get(str(file))
        if not row or args.owner not in row.get('owners', {}):
            return {'released': False}
        current = _alias_stat(file)
        original = _alias_stat(row['source'])
        if not original:
            raise ValueError('Canonical transcript is missing; the remaining alias was preserved')
        expected = (row.get('dev'), row.get('ino'))
        if _alias_identity(original) != expected or _alias_identity(current) != expected:
            raise ValueError('Official transcript alias or canonical source changed; both paths were preserved')
        owners = _alias_live_owners(row.get('owners', {}))
        owners.pop(args.owner, None)
        if owners:
            row['owners'] = owners
        else:
            # Exact inode verified above and canonical path still points to it.
            file.unlink()
            del data['aliases'][str(file)]
        atomic_json(shared / 'session-aliases.json', data)
        return {'released': True}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--shared', type=Path, default=Path(os.environ.get('LIBRARY_OFFICIAL_SHARED') or Path(__file__).resolve().parent.parent))
    sub = parser.add_subparsers(dest='command', required=True)
    p = sub.add_parser('route')
    for name in ('session', 'transcript', 'cwd'):
        p.add_argument('--' + name, required=True)
    p.add_argument('--host', default='mac')
    p.add_argument('--account')
    p.add_argument('--prefer-saved-account', action='store_true')
    p.add_argument('--dry-run', action='store_true')
    p.add_argument('--workspace')
    p.add_argument('--model')
    p = sub.add_parser('profile-links')
    p.add_argument('--account', required=True)
    sub.add_parser('profile-links-all')
    p = sub.add_parser('accounts')
    p.add_argument('--provider', choices=('claude', 'codex'), required=True)
    p = sub.add_parser('codex-window')
    p.add_argument('--account', required=True)
    p.add_argument('--session')
    for name in ('app', 'user-data-dir', 'extensions-dir'):
        p.add_argument('--' + name)
    p = sub.add_parser('classify')
    p.add_argument('--provider', choices=('claude', 'codex'), required=True)
    p.add_argument('--transcript', required=True)
    p.add_argument('--account')
    for command in ('alias-acquire', 'alias-release'):
        p = sub.add_parser(command)
        p.add_argument('--file', required=True)
        p.add_argument('--owner', required=True)
        p.add_argument('--pid', type=int, default=os.getpid())
        p.add_argument('--transcript')
        p.add_argument('--session')
    p = sub.add_parser('exec')
    p.add_argument('executable')
    p.add_argument('arguments', nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    try:
        if args.command == 'exec':
            wrapper(args.shared, args.executable, args.arguments)
            return 0
        if args.command == 'alias-acquire':
            result = alias_acquire(args.shared, args)
        elif args.command == 'alias-release':
            result = alias_release(args.shared, args)
        elif args.command == 'classify':
            result = classify_transcript(args.shared, args)
        elif args.command == 'route':
            result = route(args.shared, args)
        elif args.command == 'profile-links':
            result = profile_links(args.shared, args.account)
        elif args.command == 'profile-links-all':
            result = profile_links_all(args.shared)
        elif args.command == 'accounts':
            result = account_list(args.shared, args.provider)
        else:
            result = codex_window(args.shared, args)
        print(json.dumps(result))
        return 1 if args.command == 'profile-links' and result.get('conflicts') else 0
    except (OSError, ValueError, KeyError, TypeError) as error:
        print('Library Code Official: ' + str(error), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())


