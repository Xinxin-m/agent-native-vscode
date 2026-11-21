#!/usr/bin/env python3
"""Authenticated stdio connector and host-aware launch construction.

Install this standalone file on an enrolled execution host. Its private local
configuration lives at ~/.local/share/chat-library/connector.json, for example:
  {"host_id":"second-mac","kind":"mac","accounts":[
    {"id":"codex-default","tool":"codex","config_dir":"~/.codex"}]}
A Slurm compute node uses "kind":"slurm" and may add "node_pattern", a regular
expression its hostname must fully match (for example "node-\\d+").
No credentials, account switching, scheduler calls or TCP listeners are used.
Launched CLIs inherit CHAT_LIBRARY_MANAGED=1, so host hooks (for example a
cluster SessionStart auto-reap hook) can recognize managed sessions. Native CLI housekeeping is preserved.
The remote process execs the selected CLI, preserving transparent stdio.
"""
import base64
import calendar
import datetime
import glob
import hashlib
import json
import math
import os
import platform
import re
import shlex
import signal
import shutil
import subprocess
import sys
import time

PROTOCOL = 1
SSH_OPTIONS = ['-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10', '-o', 'ConnectionAttempts=1',
               '-o', 'ServerAliveInterval=5', '-o', 'ServerAliveCountMax=2',
               '-o', 'StrictHostKeyChecking=yes', '-o', 'SendEnv=-*', '-T']
SID = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$')
MODEL = re.compile(r'^[A-Za-z0-9._\-\[\]:]{1,160}$')
PROFILE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_.-]{0,100}$')


class AmbiguousSessionAccount(ValueError):
    """No single local profile owns the session; the user must choose one."""


class SessionAccountMismatch(ValueError):
    """The requested profile cannot resume the session; .candidates lists those that can."""


def _read(path, default):
    try:
        with open(os.path.expanduser(path)) as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return default


def connector_config():
    return _read('~/.local/share/chat-library/connector.json', {})


def validate_remote(host):
    alias = host.get('execution_alias') or ''
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,160}', alias):
        raise ValueError('Configure a named SSH execution alias first')
    if alias in (host.get('login_aliases') or ()) or alias.startswith('login.') or (host.get('kind') == 'slurm' and alias == host.get('read_alias')):
        raise ValueError('The selected SSH alias is not an execution host')
    path = host.get('connector_path') or ''
    if not (path.startswith('~/') or path.startswith('/')) or not re.fullmatch(r'[A-Za-z0-9_./~-]+', path) or '..' in path.split('/'):
        raise ValueError('Configure an absolute connector path on the execution host')
    return alias, path


def ssh_command(host, operation, request=None):
    alias, path = validate_remote(host)
    remote_path = '$HOME/' + shlex.quote(path[2:]) if path.startswith('~/') else shlex.quote(path)
    command = 'python3 ' + remote_path + ' ' + shlex.quote(operation)
    if request is not None:
        encoded = base64.urlsafe_b64encode(json.dumps(request, separators=(',', ':')).encode()).decode()
        command += ' ' + shlex.quote(encoded)
    return ['ssh', *SSH_OPTIONS, alias, command]


def _base_env():
    try:
        import clis
        env = clis.env()
    except ImportError:
        env = dict(os.environ)
        env['PATH'] = os.pathsep.join([os.path.expanduser('~/.local/bin'), '/opt/homebrew/bin', '/usr/local/bin', env.get('PATH', '/usr/bin:/bin')])
    return env


def _local_accounts():
    try:
        import accounts
        return accounts.load()  # read-only; never pick(), which can switch credentials
    except ImportError:
        cfg = connector_config()
        return {'accounts': cfg.get('accounts') or [
            {'id': 'claude-default', 'tool': 'claude', 'config_dir': '~/.claude'},
            {'id': 'codex-default', 'tool': 'codex', 'config_dir': '~/.codex'}],
            'state': {'active': cfg.get('active') or {}, 'global_active': {}}}


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
                if isinstance(reset, (float, int)) and not isinstance(reset, bool):
                    reset_ts = float(reset)
                else:
                    parsed = datetime.datetime.fromisoformat(reset.replace('Z', '+00:00'))
                    if parsed.tzinfo is None or parsed.utcoffset() is None:
                        return None, 'usage_unknown'
                    reset_ts = parsed.timestamp()
            except (ValueError, TypeError, AttributeError, OverflowError):
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


def select_quota_failover(tool, model, source_account_id, attempted_account_ids=()):
    """Select a distinct verified profile after an independently confirmed quota limit."""
    import accounts
    auto = accounts._auto_cfg()
    if not auto.get('enabled') or auto.get('mode') == 'off' or not model:
        return None
    data = _local_accounts()
    profiles = [p for p in data.get('accounts', []) if p.get('tool') == tool]
    cache = data.get('cache', {})
    attempted = set(attempted_account_ids) | {source_account_id}
    source = cache.get(source_account_id) or {}
    source_email = str(source.get('email') or '').strip().lower()
    if source.get('logged_in') is not True or not source_email or not any(p.get('id') == source_account_id for p in profiles):
        return None
    excluded_emails = {str((cache.get(aid) or {}).get('email') or '').strip().lower() for aid in attempted}
    choices = []
    for profile in profiles:
        aid = profile.get('id')
        rec = cache.get(aid) or {}
        email = str(rec.get('email') or '').strip().lower()
        if aid in attempted or not email or email in excluded_emails or profile.get('disabled') or profile.get('eligible') is False:
            continue
        models = profile.get('models')
        if models is not None and not any(_model_family(m) == _model_family(model) for m in models):
            continue
        try:
            _profile_directory(data, profile)
        except (ValueError, OSError):
            continue
        percent, status = _quota_status(rec, model, time.time(), USAGE_FRESH_S)
        if status == 'known' and percent < 100:
            choices.append((percent, aid))
    if not choices:
        return None
    percent, aid = min(choices)
    return {'account_id': aid, 'reason': 'confirmed_quota_limit', 'percent': percent}


def _profile_directory(data, profile):
    wanted, tool = profile['id'], profile['tool']
    global_active = data.get('state', {}).get('global_active', {}).get(tool)
    stashed = wanted.endswith('-default') and global_active and wanted != global_active
    if stashed and not profile.get('login_config_dir'):
        raise ValueError('Default account is stashed; restore it explicitly before launching')
    directory = profile.get('login_config_dir') if stashed else ('~/.claude' if tool == 'claude' else '~/.codex') if global_active == wanted else profile.get('config_dir')
    if not directory:
        raise ValueError('Selected account has no local configuration directory')
    directory = os.path.realpath(os.path.expanduser(directory))
    if not os.path.isdir(directory):
        raise ValueError('Selected account configuration directory is missing on this host')
    return directory


def _login_identity(tool, directory):
    """Non-secret login identity the provider CLI itself reads from this home, or None."""
    if tool == 'codex':
        tokens = (_read(os.path.join(directory, 'auth.json'), {}) or {}).get('tokens') or {}
        if not tokens.get('account_id'):
            return None
        try:
            part = (tokens.get('id_token') or '').split('.')[1]
            claims = json.loads(base64.urlsafe_b64decode(part + '=' * (-len(part) % 4)))
            user = (claims.get('https://api.openai.com/auth') or {}).get('chatgpt_user_id')
        except (IndexError, ValueError, TypeError, AttributeError):
            user = None
        return ('codex', tokens['account_id'], user or '')
    # Claude keeps the default home's login in ~/.claude.json, others in <home>/.claude.json.
    default = os.path.realpath(os.path.expanduser('~/.claude'))
    path = '~/.claude.json' if os.path.realpath(directory) == default else os.path.join(directory, '.claude.json')
    login = (_read(path, {}) or {}).get('oauthAccount') or {}
    return ('claude', login['accountUuid'], login.get('organizationUuid') or '') if login.get('accountUuid') else None


def _one_login(data, tool, matches, directories):
    """Profiles that see a session and share one signed-in login act as one account.

    Signed-out profiles cannot run the resume and are not candidates once a
    signed-in one matches. Returns the active profile for new chats, else the
    default, else the first by id; None when the logins differ or none is known.
    """
    identities = {pid: _login_identity(tool, directories[pid]) for pid in matches}
    signed_in = sorted(pid for pid, login in identities.items() if login)
    if not signed_in or len({identities[pid] for pid in signed_in}) != 1:
        return None
    active = data.get('state', {}).get('active', {}).get(tool)
    return next((pid for pid in (active, tool + '-default') if pid in signed_in), signed_in[0])


def shared_session_store(tool, source_account, target_account):
    """An explicit recovery may change account only for the same physical store."""
    source, source_home, _ = local_profile(tool, source_account)
    target, target_home, _ = local_profile(tool, target_account)
    name = 'projects' if tool == 'claude' else 'sessions'
    left, right = os.path.join(source_home, name), os.path.join(target_home, name)
    if not os.path.isdir(left) or not os.path.isdir(right) or os.path.realpath(left) != os.path.realpath(right):
        raise ValueError('These accounts do not share the native session store; use a provider handoff')
    return os.path.realpath(left)


def resolve_local_account(tool, account_id=None, model=None, session_id=None, cwd=None):
    data = _local_accounts()
    bound, pinned, ownership = None, False, ''
    if session_id:
        matches, directories = [], {}
        for profile in data.get('accounts', []):
            if profile.get('tool') != tool:
                continue
            try:
                directory = _profile_directory(data, profile)
            except ValueError:
                continue
            if _session_exists(tool, directory, session_id, cwd):
                matches.append(profile['id'])
                directories[profile['id']] = directory
        if tool == 'codex' and matches:
            # Shared session-directory symlinks establish presence, not ownership.
            # The native transcript creator must match this profile's auth identity.
            creators=set()
            profile_identities={}
            for profile in data.get('accounts', []):
                if profile.get('id') not in matches:continue
                directory=_profile_directory(data,profile)
                for path in glob.glob(os.path.join(directory,'sessions','*','*','*','rollout-*-'+session_id+'.jsonl')):
                    try:
                        with open(path) as handle:meta=json.loads(handle.readline()).get('payload',{})
                        if (meta.get('id') or meta.get('session_id'))==session_id and os.path.realpath(meta.get('cwd') or '')==cwd and meta.get('creator_account_id'):
                            creators.add(meta['creator_account_id'])
                    except (OSError,ValueError,TypeError):continue
                auth=_read(os.path.join(directory,'auth.json'),{})
                tokens=auth.get('tokens') or {}
                profile_identities[profile['id']]=tokens.get('account_id')
            if len(creators)>1:raise ValueError('Native session ownership conflicts across profile transcripts')
            owners=[profile_id for profile_id in matches if profile_identities.get(profile_id) in creators]
            if owners:
                matches,pinned=owners,True
            elif creators:
                # The creating login is signed in nowhere (re-login or removed profile).
                # Codex resumes any rollout its CODEX_HOME can read, so presence decides.
                ownership='creator_not_signed_in'
        active = data.get('state', {}).get('active', {}).get(tool)
        if account_id:
            if account_id not in matches:
                error = SessionAccountMismatch('Session does not exist in the selected host, account and workspace')
                error.candidates = matches
                raise error
            bound = account_id
        elif len(matches) == 1:
            bound = matches[0]
        elif len(matches) > 1 and (shared := _one_login(data, tool, matches, directories)):
            bound = shared
        elif len(matches) > 1 and not pinned and active in matches and _login_identity(tool, directories[active]):
            # No transcript names an owner among these logins: continue on the active account.
            bound, ownership = active, ownership or 'unattributed_active'
        else:
            error = AmbiguousSessionAccount('Session account is missing or ambiguous; choose its original account')
            error.candidates = matches
            raise error
    try:
        import accounts
        auto = accounts._auto_cfg()
    except ImportError:
        auto = connector_config().get('auto_switch', {})
    # Profiles without a usable home cannot be automatic alternatives.
    data = dict(data, accounts=[dict(p) for p in data.get('accounts', [])])
    for profile in data['accounts']:
        try:
            _profile_directory(data, profile)
        except ValueError:
            profile['eligible'] = False
    selection = resolve_account(data, tool, model, account_id, bound, auto=auto)
    if ownership:
        selection['ownership'] = ownership
    return selection


def local_profile(tool, account_id=None):
    data = _local_accounts()
    wanted = account_id or data.get('state', {}).get('active', {}).get(tool) or tool + '-default'
    if not isinstance(wanted, str) or not PROFILE.fullmatch(wanted):
        raise ValueError('Invalid account identifier')
    profile = next((a for a in data.get('accounts', []) if a.get('id') == wanted and a.get('tool') == tool), None)
    if not profile:
        raise ValueError('Selected account is not configured on this host')
    config_dir = _profile_directory(data, profile)
    env = _base_env()
    env.pop('CLAUDE_CONFIG_DIR', None)
    env.pop('CODEX_HOME', None)
    env['CHAT_LIBRARY_MANAGED'] = '1'
    # Claude's native Keychain service depends on whether this variable is set,
    # even when its explicit value is the default directory. Preserve the
    # default invocation for the host-wide account. Custom homes stay explicit.
    if tool != 'claude' or config_dir != os.path.realpath(os.path.expanduser('~/.claude')):
        env['CLAUDE_CONFIG_DIR' if tool == 'claude' else 'CODEX_HOME'] = config_dir
    return wanted, config_dir, env


def validate_request(tool, cwd, session_id, model, effort, protocol):
    if tool not in ('claude', 'codex'):
        raise ValueError('Unsupported chat tool')
    if not isinstance(cwd, str) or not cwd.startswith('/') or '\x00' in cwd:
        raise ValueError('Choose an absolute workspace path on the execution host')
    if session_id and (not isinstance(session_id, str) or not SID.fullmatch(session_id)):
        raise ValueError('Invalid session identity')
    if model and (not isinstance(model, str) or not MODEL.fullmatch(model)):
        raise ValueError('Invalid model identifier')
    efforts = {'low', 'medium', 'high', 'xhigh', 'max'} if tool == 'claude' else {'none', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max'}
    if effort and effort not in efforts:
        raise ValueError('Unsupported effort for the selected tool')
    if protocol not in ('exec', 'app-server', 'claude-stream') or (protocol == 'app-server' and tool != 'codex') or (protocol == 'claude-stream' and tool != 'claude'):
        raise ValueError('Protocol does not match the selected chat tool')


def claude_project_dir(cwd):
    import unicodedata
    value = os.path.realpath(cwd)
    if sys.platform == 'darwin':
        value = unicodedata.normalize('NFC', value)
    encoded = re.sub(r'[^A-Za-z0-9]', '-', value)
    # JavaScript sanitizes UTF-16 code units, including surrogate pairs.
    units = value.encode('utf-16-le', 'surrogatepass')
    encoded = ''.join(chr(int.from_bytes(units[i:i+2], 'little')) if 48 <= int.from_bytes(units[i:i+2], 'little') <= 57 or 65 <= int.from_bytes(units[i:i+2], 'little') <= 90 or 97 <= int.from_bytes(units[i:i+2], 'little') <= 122 else '-' for i in range(0, len(units), 2))
    if len(encoded) <= 200:
        return encoded
    hashed = 0
    for i in range(0, len(units), 2):
        hashed = (31 * hashed + int.from_bytes(units[i:i+2], 'little')) & 0xffffffff
    if hashed >= 0x80000000:
        hashed -= 0x100000000
    number = abs(hashed)
    suffix = ''
    while number:
        number, digit = divmod(number, 36)
        suffix = '0123456789abcdefghijklmnopqrstuvwxyz'[digit] + suffix
    return encoded[:200] + '-' + (suffix or '0')


def _session_exists(tool, config_dir, session_id, cwd):
    if not session_id:
        return True
    if tool == 'claude':
        slug = claude_project_dir(cwd)
        return os.path.isfile(os.path.join(config_dir, 'projects', slug, session_id + '.jsonl'))
    for path in glob.glob(os.path.join(config_dir, 'sessions', '*', '*', '*', 'rollout-*-' + session_id + '.jsonl')):
        try:
            with open(path) as fh:
                meta = json.loads(fh.readline()).get('payload', {})
            if (meta.get('id') or meta.get('session_id')) == session_id and os.path.realpath(meta.get('cwd') or '') == cwd:
                return True
        except (OSError, ValueError, TypeError):
            continue
    return False


def _running_processes():
    """Bounded own-UID process roster on this host; never scans a shared registry."""
    try:
        result = subprocess.run(['ps', '-axo', 'pid=,uid=,lstart=,comm='], capture_output=True,
                                text=True, timeout=5, env=dict(os.environ, LC_ALL='C'))
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ValueError('Cannot verify native session activity on this host') from exc
    if result.returncode:
        raise ValueError('Cannot verify native session activity on this host')
    pids = {}
    for line in result.stdout.splitlines():
        parts = line.split(None, 7)
        if len(parts) != 8 or not parts[0].isdigit() or not parts[1].isdigit() or int(parts[1]) != os.getuid():
            continue
        try:
            started = time.mktime(time.strptime(' '.join(parts[2:7]), '%a %b %d %H:%M:%S %Y'))
        except ValueError:
            continue
        pids[int(parts[0])] = (os.path.basename(parts[7]), started)
    return pids


def _record_start(record):
    try:
        return calendar.timegm(time.strptime(' '.join(record['procStart'].split()), '%a %b %d %H:%M:%S %Y'))
    except (KeyError, TypeError, ValueError, AttributeError):
        return None


def _record_on_this_host(record):
    domain = record.get('pidDomain') or ''
    if sys.platform.startswith('linux') and ':' in domain:
        try:
            with open('/etc/machine-id') as fh:
                machine = fh.read().strip()
            if machine and machine not in domain:
                return False
        except OSError:
            pass
    return True


def assert_session_not_running(config_dir, session_id):
    """Refuse an exact busy/ambiguous Claude registration; permit explicit idle.

    This is a resume guard, never a process-control operation. It only opens
    registry files corresponding to this host's currently running Claude PIDs.
    Missing process-start evidence cannot establish a safe concurrent resume.
    """
    for pid, (command, started) in _running_processes().items():
        if command != 'claude':
            continue
        record = _read(os.path.join(config_dir, 'sessions', str(pid) + '.json'), {})
        if record.get('pid') != pid or record.get('sessionId') != session_id or not _record_on_this_host(record):
            continue
        record_start = _record_start(record)
        if record_start is None:
            raise ValueError('Native session activity is ambiguous; return to its original client first')
        if record_start != started:
            continue  # stale PID registration, not a match to the current process
        if record.get('status') != 'idle':
            raise ValueError('This conversation is busy in its original client; finish or interrupt it there first')


def local_launch(tool, cwd, account_id=None, session_id=None, model=None, effort=None, protocol='exec', images=None, shared_session_from=None):
    validate_request(tool, cwd, session_id, model, effort, protocol)
    cwd = os.path.realpath(cwd)
    if not os.path.isdir(cwd):
        raise ValueError('Workspace does not exist on the selected host')
    if shared_session_from:
        shared_session_store(tool, shared_session_from, account_id)
        if tool == 'claude':
            _, source_directory, _ = local_profile(tool, shared_session_from)
            assert_session_not_running(source_directory, session_id)
        _, directory, _ = local_profile(tool, account_id)
        if not session_id or not _session_exists(tool, directory, session_id, cwd):
            raise ValueError('Recovery session is missing from the shared store')
    selection = resolve_local_account(tool, account_id, model, None if shared_session_from else session_id, cwd)
    account_id, config_dir, env = local_profile(tool, selection['account_id'])
    if not _session_exists(tool, config_dir, session_id, cwd):
        raise ValueError('Session does not exist in the selected host, account and workspace')
    if tool == 'claude' and session_id:
        assert_session_not_running(config_dir, session_id)
    try:
        import clis
        executable = clis.cli(tool)
    except ImportError:
        executable = shutil.which(tool, path=env['PATH']) or ''
    if not executable or not os.path.isfile(executable) or not os.access(executable, os.X_OK):
        raise ValueError('Selected CLI is not installed on this host')
    if protocol == 'app-server':
        argv = [executable, 'app-server', '--listen', 'stdio://']
    elif tool == 'claude':
        argv = [executable, '-p', '--output-format', 'stream-json', '--verbose', '--include-partial-messages']
        if protocol == 'claude-stream':
            argv += ['--input-format', 'stream-json', '--permission-prompt-tool', 'stdio', '--permission-prompts', 'host',
                     '--replay-user-messages']  # the replay is the steering receipt
        if session_id:
            argv += ['--resume', session_id]
        if model:
            argv += ['--model', model]
        if effort:
            argv += ['--effort', effort]
    else:
        argv = [executable, 'exec'] + (['resume', session_id] if session_id else []) + ['--json', '--skip-git-repo-check']
        if model:
            argv += ['-m', model]
        if effort:
            argv += ['-c', 'model_reasoning_effort=' + json.dumps(effort)]
        for path in images or []:
            if not os.path.isfile(path):
                raise ValueError('Attachment is missing on the selected host')
            argv += ['--image', path]
        argv += ['-']
    return {'argv': argv, 'cwd': cwd, 'env': env, 'host_id': 'mac', 'execution_cwd': cwd,
            'transport': 'local', 'account_id': account_id, 'account_selection': selection}


def build_cli_launch(host_id, tool, cwd, account_id=None, session_id=None, model=None, effort=None, protocol='exec', images=None, shared_session_from=None):
    """Build a Popen-compatible launch; never execute, SSH, or export credentials.

    Remote availability is granted only by a fresh explicit hosts.probe_host().
    Remote cwd/session/profile validation repeats inside the authenticated helper
    before exec; failures arrive on stderr with exit status 2, without launching.
    """
    import hosts
    host = hosts.get_host(host_id)
    validate_request(tool, cwd, session_id, model, effort, protocol)
    if host.get('transport') == 'local':
        return local_launch(tool, cwd, account_id, session_id, model, effort, protocol, images, **({'shared_session_from': shared_session_from} if shared_session_from else {}))
    if shared_session_from:
        raise ValueError('Account recovery on remote hosts is not available')
    if not host.get('can_execute') or not host.get('machine_id'):
        raise ValueError('Execution host is offline, unconfigured or needs an explicit probe')
    if images:
        raise ValueError('Remote attachments require staging; choose a local chat or remove the attachment')
    if session_id and not account_id:
        raise ValueError('Remote resume requires its original account binding')
    routing = {'accounts': host.get('accounts', []),
               'state': {'active': host.get('active_accounts') or {}}, 'cache': {}}
    selection = resolve_account(routing, tool, model, account_id,
                                account_id if session_id else None, host_id=host_id,
                                auto=host.get('auto_switch'))
    wanted = selection['account_id']
    request = dict(host_id=host_id, machine_id=host['machine_id'], job_id=host.get('job_id'),
                   tool=tool, cwd=cwd, account_id=wanted, session_id=session_id, model=model,
                   effort=effort, protocol=protocol)
    return {'argv': ssh_command(host, 'run', request), 'cwd': None, 'env': None,
            'host_id': host_id, 'execution_cwd': cwd, 'transport': 'ssh', 'account_id': wanted, 'account_selection': selection}


def machine_identity():
    machine = ''
    if sys.platform.startswith('linux'):
        try:
            with open('/etc/machine-id') as fh:
                machine = fh.read().strip()
        except OSError:
            pass
    if not machine:
        machine = platform.node()
    return hashlib.sha256((machine + ':' + str(os.getuid())).encode()).hexdigest()


def execution_identity(cfg):
    hostname = platform.node()
    job_id = None
    if cfg.get('kind') == 'slurm':
        if cfg.get('node_pattern') and not re.fullmatch(cfg['node_pattern'], hostname):
            raise ValueError('Cluster connector must execute on an allocated compute node')
        try:
            with open('/proc/self/cgroup') as fh:
                match = re.search(r'(?:^|/)job_(\d+)(?:/|\s|$)', fh.read())
            job_id = match.group(1) if match else None
        except OSError:
            pass
        if not job_id:
            raise ValueError('Cluster connector is outside a Slurm job cgroup')
    return {'host_id': cfg.get('host_id'), 'machine_id': machine_identity(), 'hostname': hostname, 'job_id': job_id}


def stop_process_identity(pid):
    """Kernel identity for Linux pidfd controls; unsupported hosts fail closed."""
    if not sys.platform.startswith('linux') or not hasattr(os, 'pidfd_open') or not hasattr(signal, 'pidfd_send_signal'):
        raise ValueError('This host needs Linux pidfd support for remote stopping')
    if type(pid) is not int or pid <= 1:
        raise ValueError('Invalid agent PID')
    root = '/proc/' + str(pid)
    try:
        owner = os.stat(root).st_uid
        with open(root + '/stat') as fh:
            stat = fh.read().rsplit(')', 1)[1].split()
        with open('/proc/sys/kernel/random/boot_id') as fh:
            boot = fh.read().strip()
        executable = os.readlink(root + '/exe')
        with open(root + '/cgroup') as fh:
            cgroup = fh.read()
    except FileNotFoundError as exc:
        raise ProcessLookupError('Agent process is gone') from exc
    if owner != os.getuid() or not boot:
        raise ValueError('Agent process owner or boot identity does not match')
    return {'pid': pid, 'uid': owner, 'start_ticks': stat[19], 'boot_id': boot,
            'executable': executable, 'cgroup': cgroup}


def validate_stop_request(request):
    cfg = connector_config()
    current_host = execution_identity(cfg)
    if any(request.get(k) != current_host.get(k) for k in ('host_id', 'machine_id', 'job_id')):
        raise ValueError('Execution host or allocation changed; refresh sessions')
    row = request.get('session') or {}
    pid = row.get('pid')
    current = stop_process_identity(pid)
    if current != row.get('identity'):
        raise ValueError('Agent process identity changed')
    if cfg.get('kind') == 'slurm' and not re.search(r'(?:^|/)job_' + re.escape(str(current_host['job_id'])) + r'(?:/|\s|$)', current['cgroup']):
        raise ValueError('Agent is outside the verified allocation')
    roster = _running_processes()
    command, started = roster.get(pid, ('', None))
    record = _read(os.path.expanduser('~/.claude/sessions/' + str(pid) + '.json'), {})
    if command != 'claude' or record.get('pid') != pid or not _record_on_this_host(record) or _record_start(record) != started:
        raise ValueError('Claude process registration cannot be verified')
    if not row.get('session_id') or record.get('sessionId') != row['session_id'] or not row.get('cwd') or os.path.realpath(record.get('cwd') or '') != os.path.realpath(row['cwd']):
        raise ValueError('Claude session or workspace changed')
    policy = request.get('policy')
    if policy is not None:
        if policy.get('basis') == 'idle':
            since = (record.get('updatedAt') or 0) / 1000
            if record.get('status') != 'idle' or not since:
                raise ValueError('Agent is no longer idle')
        elif policy.get('basis') == 'age':
            since = started
        else:
            raise ValueError('Invalid automatic stop basis')
        hours = policy.get('hours')
        if isinstance(hours, bool) or not isinstance(hours, (int, float)) or not 1 <= hours <= 8760 or time.time() - since <= hours * 3600:
            raise ValueError('Agent no longer meets the automatic stop threshold')
    return row, current


def stop_remote_session(request):
    row, identity = validate_stop_request(request)
    fd = os.pidfd_open(row['pid'])
    try:
        # pidfd pins the actual process, so PID reuse cannot redirect SIGTERM.
        validate_stop_request(request)
        signal.pidfd_send_signal(fd, signal.SIGTERM)
    finally:
        os.close(fd)
    return {'ok': True, 'id': row['id'], 'state': 'stop_requested'}


def remote_stop_status(request):
    current_host = execution_identity(connector_config())
    if any(request.get(k) != current_host.get(k) for k in ('host_id', 'machine_id', 'job_id')):
        raise ValueError('Execution host or allocation changed; cannot verify exit')
    row = request.get('session') or {}
    try:
        identity = stop_process_identity(row.get('pid'))
    except ProcessLookupError:
        identity = None
    return {'state': 'stopped' if identity != row.get('identity') else 'stop_requested'}


def inventory():
    cfg = connector_config()
    if not cfg.get('host_id'):
        raise ValueError('Enroll this connector with a host_id first')
    identity = execution_identity(cfg)
    accounts = []
    for profile in _local_accounts().get('accounts', []):
        if profile.get('tool') in ('claude', 'codex'):
            try:
                local_profile(profile['tool'], profile.get('id'))
                row = {'id': profile['id'], 'tool': profile['tool'], 'label': profile.get('label') or profile['id']}
                for key in ('eligible', 'disabled', 'models'):
                    if key in profile:
                        row[key] = profile[key]
                cache = _local_accounts().get('cache', {}).get(profile['id']) or profile.get('usage') or {}
                row.update({key:cache[key] for key in ('email','plan','logged_in') if key in cache})
                row['usage'] = {k: cache[k] for k in ('logged_in', 'windows', 'fetched_at', 'cached_at', 'rollout_at', 'error') if k in cache}
                accounts.append(row)
            except ValueError:
                continue
    sessions = []
    # Bounded, local-only roster. Never invokes squeue/srun or scans other nodes.
    pids = _running_processes()
    for pid, (command, started) in pids.items():
        if command != 'claude':
            continue
        record = _read(os.path.expanduser('~/.claude/sessions/' + str(pid) + '.json'), {})
        if record.get('pid') != pid or not record.get('sessionId') or not _record_on_this_host(record):
            continue
        if _record_start(record) != started:
            continue
        process = None
        reason = ''
        try:
            process = stop_process_identity(pid)
            validate_stop_request(dict(identity, session={'pid': pid, 'identity': process, 'session_id': record['sessionId'], 'cwd': record.get('cwd')}))
        except (ValueError, OSError) as exc:
            process, reason = None, str(exc)
        sessions.append({'id': cfg['host_id'] + ':' + str(pid), 'pid': pid, 'tool': 'claude',
                         'session_id': record['sessionId'], 'cwd': record.get('cwd') or '',
                         'status': record.get('status') or '?', 'name': record.get('name') or 'claude',
                         'host': identity['hostname'], 'node': cfg['host_id'], 'alive': True,
                         'observed_at': time.time(), 'last_act_ts': (record.get('updatedAt') or 0) / 1000 or None,
                         'identity': process, 'execution_identity': identity, 'started_at': started,
                         'stop_allowed': process is not None, 'stop_reason': reason})
    for pid, (command, started) in pids.items():
        if command == 'codex':
            sessions.append({'id': cfg['host_id'] + ':' + str(pid), 'pid': pid, 'tool': 'codex',
                             'session_id': '', 'cwd': '', 'status': '?', 'name': 'codex',
                             'host': identity['hostname'], 'node': cfg['host_id'], 'alive': True,
                             'observed_at': time.time(), 'last_act_ts': None, 'stop_allowed': False,
                             'stop_reason': 'Remote Codex session association is ambiguous'})
    try:
        from commands import command_catalog
        catalogs = {provider: command_catalog(provider)['commands'] for provider in ('claude', 'codex')}
    except ImportError:
        catalogs = {}
    return dict(identity, protocol=PROTOCOL, capabilities=['run', 'inventory', 'stop', 'models'], accounts=accounts, sessions=sessions, commands=catalogs,
                active_accounts=_local_accounts().get('state', {}).get('active', {}))



def discover_models(request, identity):
    """Query only provider initialization metadata on the selected execution host."""
    import selectors
    tool, cwd, account = request.get('tool'), request.get('cwd'), request.get('account_id')
    validate_request(tool, cwd, None, None, None, 'exec')
    if not account:
        raise ValueError('Choose an account for model discovery')
    cwd = os.path.realpath(cwd)
    if not os.path.isdir(cwd):
        raise ValueError('Workspace does not exist on the selected host')
    account, _, env = local_profile(tool, account)
    try:
        import clis
        executable = clis.cli(tool)
    except ImportError:
        executable = shutil.which(tool, path=env['PATH']) or ''
    if not executable or not os.path.isfile(executable) or not os.access(executable, os.X_OK):
        raise ValueError('Selected CLI is not installed on this host')
    argv = ([executable, '--bare', '-p', '--input-format', 'stream-json', '--output-format', 'stream-json', '--verbose', '--no-session-persistence'] if tool == 'claude' else [executable, 'app-server', '--listen', 'stdio://'])
    child = subprocess.Popen(argv, cwd=cwd, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=0)
    selector = selectors.DefaultSelector()
    try:
        selector.register(child.stdout, selectors.EVENT_READ)
        def send(obj):
            child.stdin.write((json.dumps(obj, separators=(',', ':')) + '\n').encode())
        if tool == 'claude':
            send({'type':'control_request','request_id':'library-models','request':{'subtype':'initialize'}})
        else:
            send({'id':1,'method':'initialize','params':{'clientInfo':{'name':'chat_library','title':'Chat Library','version':'1.0'},'capabilities':{'experimentalApi':False}}})
        deadline, buffer, total, rows, commands = time.monotonic() + 12, b'', 0, [], None
        while time.monotonic() < deadline:
            if not selector.select(max(0, deadline-time.monotonic())):
                break
            chunk = os.read(child.stdout.fileno(), 65536)
            if not chunk:
                raise ValueError('Provider closed before advertising models')
            total += len(chunk)
            if total > 2000000:
                raise ValueError('Provider metadata exceeds size limit')
            buffer += chunk
            while b'\n' in buffer:
                line, buffer = buffer.split(b'\n', 1)
                try:
                    msg = json.loads(line)
                except (ValueError, UnicodeDecodeError):
                    continue
                if not isinstance(msg, dict):
                    continue
                if tool == 'codex' and msg.get('id') == 1:
                    if 'error' in msg or 'result' not in msg:
                        raise ValueError('Provider initialization failed')
                    send({'method':'initialized','params':{}})
                    send({'id':2,'method':'model/list','params':{'limit':100}})
                elif tool == 'codex' and msg.get('id') == 2:
                    if 'error' in msg or not isinstance(msg.get('result'), dict):
                        raise ValueError('Provider model catalog unavailable')
                    result = msg['result']
                    if result.get('nextCursor'):
                        raise ValueError('Provider model catalog exceeds one page')
                    rows = result.get('data') or []
                    break
                elif tool == 'claude' and msg.get('type') == 'control_response' and isinstance(msg.get('response'), dict) and msg['response'].get('request_id') == 'library-models':
                    result = msg['response'].get('response') or {}
                    if not isinstance(result, dict):
                        raise ValueError('Provider model catalog unavailable')
                    rows, commands = result.get('models') or [], result.get('commands')
                    break
            else:
                continue
            break
        else:
            raise ValueError('Provider model discovery timed out')
        if not isinstance(rows, list) or not rows:
            raise ValueError('Provider did not advertise a model catalog')
        if len(rows) > 100:
            raise ValueError('Provider model catalog exceeds the 100 model limit')
        models = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            model = row.get('value') if tool == 'claude' else row.get('id') or row.get('model')
            if not isinstance(model, str) or not MODEL.fullmatch(model):
                continue
            efforts = (row.get('supportedEffortLevels') if row.get('supportsEffort') is not False else []) if tool == 'claude' else row.get('supportedReasoningEfforts')
            efforts = [e if isinstance(e, str) else e.get('reasoningEffort') for e in (efforts or []) if isinstance(e, (str, dict))]
            entry = {'id': model, 'name': str(row.get('displayName') or model)[:200], 'efforts': [e for e in efforts if isinstance(e, str) and len(e) <= 30]}
            default = row.get('defaultReasoningEffort')
            if isinstance(default, str):
                entry['default_effort'] = default[:30]
            models.append(entry)
        if not models:
            raise ValueError('Provider did not advertise a usable model catalog')
        response = dict(identity, ok=True, provider=tool, account_id=account, cwd=request['cwd'], resolved_cwd=cwd, source='host-cli', observed_at=time.time(), models=models)
        if isinstance(commands, list):
            if len(commands) > 500:
                raise ValueError('Provider command catalog exceeds limit')
            normalized = []
            for command in commands:
                if not isinstance(command, dict):
                    continue
                name = command.get('name')
                if not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,119}', name) or name.startswith('workflow-launch-'):
                    continue
                aliases = command.get('aliases') if isinstance(command.get('aliases'), list) else []
                normalized.append({'name': name, 'description': str(command.get('description') or '')[:600], 'argumentHint': str(command.get('argumentHint') or '')[:200], 'aliases': [a for a in aliases[:100] if isinstance(a, str) and re.fullmatch(r'[A-Za-z0-9_-]{1,120}', a)], 'kind': 'command' if command.get('builtin') else 'skill', 'insert': '/' + name, 'native': True})
            response['commands'] = normalized
        return response
    finally:
        selector.close()
        for stream in (child.stdin, child.stdout):
            try:
                stream.close()
            except OSError:
                pass
        if child.poll() is None:
            child.terminate()
        try:
            child.wait(timeout=2)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait()

def main():
    try:
        operation = sys.argv[1] if len(sys.argv) > 1 else ''
        if operation == 'inventory':
            signal.alarm(15)  # remote-side bound, including a dropped SSH client
            print(json.dumps(inventory()))
            signal.alarm(0)
            return
        if operation not in ('run', 'stop', 'stop-status', 'models') or len(sys.argv) != 3 or len(sys.argv[2]) > 32768:
            raise ValueError('Expected inventory or run request')
        request = json.loads(base64.urlsafe_b64decode(sys.argv[2]))
        if operation in ('stop', 'stop-status'):
            signal.alarm(15)
            print(json.dumps(stop_remote_session(request) if operation == 'stop' else remote_stop_status(request)))
            signal.alarm(0)
            return
        current = execution_identity(connector_config())
        if not current.get('host_id') or any(request.get(k) != current.get(k) for k in ('host_id', 'machine_id', 'job_id')):
            raise ValueError('Execution host or allocation changed; probe it again')
        if operation == 'models':
            print(json.dumps(discover_models(request, current)))
            return
        launch = local_launch(**{k: request.get(k) for k in ('tool', 'cwd', 'account_id', 'session_id', 'model', 'effort', 'protocol')})
        os.chdir(launch['cwd'])
        os.execve(launch['argv'][0], launch['argv'], launch['env'])
    except (ValueError, OSError, TypeError, KeyError) as exc:
        print('Connector: ' + str(exc), file=sys.stderr)
        raise SystemExit(2)


if __name__ == '__main__':
    main()
