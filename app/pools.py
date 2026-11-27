"""Quota warnings for the local account pools: CCS CLIProxy (Claude, 127.0.0.1:8317) and codex-lb
(Codex, 127.0.0.1:2455). The pools pick an account per request, so the warnings come when the
serving account crosses the threshold and again when the pool starts serving from another account.

Reads only, and never sends a model request through a pool. CCS: scripts/ccs_pool_state.cjs runs
the functions `ccs cliproxy quota --provider claude` renders. codex-lb: its dashboard REST API
(GET /api/accounts, GET /api/request-logs) after a password login read from Keychain; the
password stays in this process. Notices go to notices.json through notices.announce.

route() keeps enabled Library launches on the configured local pool.
"""
import http.cookiejar
import json
import os
import shlex
import subprocess
import time
import urllib.error
import urllib.request

import notices

POLL_SECONDS = 300
CCS_HELPER = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'scripts', 'ccs_pool_state.cjs')
CCS_DIST = '/opt/homebrew/lib/node_modules/@kaitranntt/ccs/dist'
NODE = '/opt/homebrew/bin/node'
CODEX_LB = 'http://127.0.0.1:2455'
WINDOW_KINDS = {300: '5h', 10080: '7d'}
WINDOW_LABELS = {'5h': 'Session (5h)', '7d': 'Weekly'}
CODEX_LB_STATES = {'active': 'available', 'paused': 'paused', 'rate_limited': 'cooling', 'quota_exceeded': 'cooling'}

CCS_PROXY = 'http://127.0.0.1:8317'
# Codex provider block from docs/account-pools.md, passed per launch so ~/.codex/config.toml stays as it is.
CODEX_LB_ARGS = [arg for key, value in (
    ('model_provider', '"codex-lb"'), ('model_providers.codex-lb.name', '"openai"'),
    ('model_providers.codex-lb.base_url', f'"{CODEX_LB}/backend-api/codex"'),
    ('model_providers.codex-lb.wire_api', '"responses"'), ('model_providers.codex-lb.supports_websockets', 'true'),
    ('model_providers.codex-lb.requires_openai_auth', 'false')) for arg in ('-c', f'{key}={value}')]
POOL_OF = {'claude': 'ccs', 'codex': 'codex-lb'}

_serving = {}  # pool -> account the previous poll saw serving; the first poll after a start only learns
_codex_lb = {'opener': None}


# ---------------------------------------------------------------- load boundary: raw -> snapshot
def parse_ccs(raw):
    """Snapshot of the CCS Claude pool. Fill-first: the first available account in drain order serves."""
    if not isinstance(raw.get('accounts'), list) or not isinstance(raw.get('order'), list):
        raise ValueError('CCS pool state has no accounts or order list')
    accounts = {}
    for a in raw['accounts']:
        windows = [{'kind': w['type'], 'label': w.get('label') or w['type'], 'percent': float(w['used_percent']),
                    'reset': notices._epoch(w.get('reset_at'))}
                   for w in a.get('windows') or [] if w.get('used_percent') is not None]
        until = a.get('cooldown_until')
        accounts[a['id']] = {'name': a.get('email') or a.get('nickname') or a['id'], 'state': a.get('state') or 'unavailable',
                             'until': until / 1000 if until else None, 'windows': windows}
    order = [aid for aid in raw['order'] if aid in accounts]
    serving = next((aid for aid in order if accounts[aid]['state'] == 'available'), None)
    return {'pool': 'ccs', 'title': 'Claude pool (CCS)', 'order': order, 'serving': serving, 'accounts': accounts}


def parse_codex_lb(accounts_payload, logs_payload):
    """Snapshot of codex-lb. The serving account is the one that answered the latest request."""
    if not isinstance(accounts_payload.get('accounts'), list) or not isinstance(logs_payload.get('requests'), list):
        raise ValueError('codex-lb response has no accounts or requests list')
    accounts = {}
    for a in accounts_payload['accounts']:
        usage = a.get('usage') or {}
        windows = []
        for slot in ('Primary', 'Secondary'):
            remaining = usage.get(slot.lower() + 'RemainingPercent')
            if remaining is None:
                continue
            kind = WINDOW_KINDS.get(a.get('windowMinutes' + slot), slot.lower())
            windows.append({'kind': kind, 'label': WINDOW_LABELS.get(kind, slot), 'percent': 100 - float(remaining),
                            'reset': notices._epoch(a.get('resetAt' + slot))})
        accounts[a['accountId']] = {'name': a.get('displayName') or a.get('email') or a['accountId'],
                                    'state': CODEX_LB_STATES.get(a.get('status'), 'unavailable'),
                                    'until': None, 'windows': windows}
    latest = logs_payload['requests'][0].get('accountId') if logs_payload['requests'] else None
    order = sorted(accounts, key=lambda aid: (_worst(accounts[aid]) or {}).get('percent', 0))
    return {'pool': 'codex-lb', 'title': 'Codex pool (codex-lb)', 'order': order,
            'serving': latest if latest in accounts else None, 'accounts': accounts}


# ---------------------------------------------------------------- decisions (pure)
def _worst(account, now=None):
    live = [w for w in account['windows'] if w['reset'] is None or now is None or w['reset'] > now]
    return max(live, key=lambda w: w['percent'], default=None)


def _successor(snap, exclude, now):
    for aid in snap['order']:
        account = snap['accounts'][aid]
        worst = _worst(account, now)
        if aid != exclude and account['state'] == 'available' and (worst is None or worst['percent'] < 100):
            return account['name']
    return None


def _usage(account, window, now):
    text = f"{account['name']} is at {window['percent']:g}% of its {window['label']} limit"
    return text + (f" (resets {notices.reset_text(window['reset'], now)})" if window['reset'] else '')


def events(snap, previous, threshold, now):
    """Notices for one poll: the serving account at or over the threshold, and a move away from an
    account that ran low, cooled down or was paused. Ordinary rotation between healthy accounts is quiet."""
    out = []
    pool, title, accounts, serving = snap['pool'], snap['title'], snap['accounts'], snap['serving']
    if serving:
        window = _worst(accounts[serving], now)
        if window and window['percent'] >= threshold:
            nxt = _successor(snap, serving, now)
            out.append({'kind': 'pool_usage', 'account_id': f'{pool}:{serving}', 'at': window['reset'],
                        'key': f"pool:{pool}:{serving}:{window['kind']}:{round((window['reset'] or now) / 3600)}",
                        'message': f"{title}: {_usage(accounts[serving], window, now)}; "
                                   + (f"new work goes to {nxt} when it runs out." if nxt else 'no other pool account has headroom.')})
    if previous and serving and previous != serving and previous in accounts:
        old = accounts[previous]
        window = _worst(old, now)
        if window and window['percent'] >= threshold:
            why, kind, at = _usage(old, window, now), window['kind'], window['reset']
        elif old['state'] == 'cooling':
            why, kind, at = old['name'] + ' is cooling down' + (
                f" until {notices.reset_text(old['until'], now)}" if old['until'] else ''), 'cooling', old['until']
        elif old['state'] == 'paused':
            why, kind, at = old['name'] + ' is paused', 'paused', None
        else:
            return out
        out.append({'kind': 'pool_switch', 'account_id': f'{pool}:{previous}', 'at': at,
                    'key': f"pool:{pool}:switch:{previous}:{kind}:{round((at or now) / 3600)}",
                    'message': f"{title}: {why}; new work goes to {accounts[serving]['name']}."})
    return out


# ---------------------------------------------------------------- readers (system boundary)
def read_ccs():
    result = subprocess.run([NODE, CCS_HELPER, CCS_DIST], capture_output=True, text=True, timeout=90)
    if result.returncode != 0:
        raise ValueError('CCS pool helper failed: ' + result.stderr.strip()[:200])
    return parse_ccs(json.loads(result.stdout))


def _codex_lb_login():
    found = subprocess.run(['security', 'find-generic-password', '-a', 'codex-lb', '-s', 'codex-lb-dashboard', '-w'],
                           capture_output=True, text=True, timeout=15)
    if found.returncode != 0:
        raise ValueError('codex-lb dashboard password is not in Keychain')
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
    request = urllib.request.Request(CODEX_LB + '/api/dashboard-auth/password/login', method='POST',
                                     data=json.dumps({'password': found.stdout.strip()}).encode(),
                                     headers={'Content-Type': 'application/json'})
    opener.open(request, timeout=15).close()
    return opener


def _codex_lb_get(path):
    for attempt in range(2):
        if _codex_lb['opener'] is None:
            _codex_lb['opener'] = _codex_lb_login()
        try:
            with _codex_lb['opener'].open(CODEX_LB + path, timeout=15) as response:
                return json.load(response)
        except urllib.error.HTTPError as error:
            if error.code != 401 or attempt:
                raise
            _codex_lb['opener'] = None  # the session expired; log in once more


def read_codex_lb():
    return parse_codex_lb(_codex_lb_get('/api/accounts'), _codex_lb_get('/api/request-logs?limit=1'))


# ---------------------------------------------------------------- launch routing
def ccs_client_env():
    """ANTHROPIC_BASE_URL and ANTHROPIC_AUTH_TOKEN from `ccs env claude`, read at each launch
    so the key is never stored by this service."""
    result = subprocess.run(['ccs', 'env', 'claude'], capture_output=True, text=True, timeout=20,
                            env=dict(os.environ, PATH='/opt/homebrew/bin:' + os.environ.get('PATH', '')))
    found = {}
    for line in result.stdout.splitlines():
        words = shlex.split(line) if line.startswith('export ') else []
        if len(words) == 2 and '=' in words[1]:
            name, _, value = words[1].partition('=')
            found[name] = value
    if result.returncode != 0 or not found.get('ANTHROPIC_BASE_URL') or not found.get('ANTHROPIC_AUTH_TOKEN'):
        raise ValueError('`ccs env claude` gave no base URL and key')
    return {k: found[k] for k in ('ANTHROPIC_BASE_URL', 'ANTHROPIC_AUTH_TOKEN')}


def check_up(tool, env):
    """Raise OSError unless the pool answers: CCS lists models for its key, codex-lb reports health."""
    if tool == 'claude':
        request = urllib.request.Request(env['ANTHROPIC_BASE_URL'] + '/v1/models',
                                         headers={'x-api-key': env['ANTHROPIC_AUTH_TOKEN']})
    else:
        request = urllib.request.Request(CODEX_LB + '/health')
    urllib.request.urlopen(request, timeout=3).close()


def codex_thread_provider(env, session_id):
    """model_provider recorded in the first line (session_meta) of a Codex thread's rollout."""
    import stream
    path = stream._codex_rollout(env, session_id)
    if not path:
        return None
    with open(path, 'rb') as handle:
        first = json.loads(handle.readline(16 * 1024 * 1024))
    return (first.get('payload') or {}).get('model_provider')


def codex_model_catalog(destination):
    from stream import _jsave

    try:
        # The Codex CLI does not fetch model metadata for this custom provider
        # with ephemeral credentials. Load the pool's native catalog at startup
        # so resumed turns retain the actual model's protocol and tool support.
        with urllib.request.urlopen(CODEX_LB + '/backend-api/codex/models', timeout=3) as response:
            raw = response.read(8 * 1024 * 1024 + 1)
        if len(raw) > 8 * 1024 * 1024:
            raise ValueError('catalog too large')
        catalog = json.loads(raw)
        models = catalog.get('models') if isinstance(catalog, dict) else None
        if not isinstance(models, list) or not models or any(
                not isinstance(model, dict) or not isinstance(model.get('slug'), str) for model in models):
            raise ValueError('invalid catalog')
        _jsave(destination, catalog)
    except (OSError, ValueError) as error:
        raise ValueError('The codex-lb model catalog is unavailable. Retry when it is back.') from error


def route(launch, tool, catalog_path, enabled=True, session_id=None):
    """Route through the loopback proxy while preserving the transcript provider ID."""
    pool = POOL_OF[tool]
    if not enabled:
        return dict(launch, account_pool={'pool': pool, 'status': 'direct',
                    'reason': 'Account pools are off in settings; using the chat account directly.'})
    try:
        extra = ccs_client_env() if tool == 'claude' else {}
        check_up(tool, extra)
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        raise ValueError(f'The {pool} account pool is unavailable. Retry when the service is back.') from error
    args = []
    env = dict(launch['env'], **extra)
    if tool == 'claude':
        env.pop('ANTHROPIC_API_KEY', None)
    else:
        provider = codex_thread_provider(env, session_id) if session_id else 'codex-lb'
        if provider == 'codex-lb':
            args = CODEX_LB_ARGS
        elif provider == 'openai':
            # Codex enforces the provider ID on resume. Its built-in provider
            # accepts an endpoint override and codex-lb serves the same protocol.
            args = ['-c', 'model_provider="openai"', '-c', f'openai_base_url="{CODEX_LB}/backend-api/codex"']
        else:
            raise ValueError('This Codex thread has a different or unrecorded provider. Turn off account pools to resume it on its original provider.')
        codex_model_catalog(catalog_path)
        args = [*args, '-c', 'cli_auth_credentials_store="ephemeral"',
                '-c', 'model_catalog_json=' + json.dumps(str(catalog_path))]
        for key in ('OPENAI_API_KEY', 'CODEX_API_KEY', 'CODEX_ACCESS_TOKEN', 'OPENAI_BASE_URL'):
            env.pop(key, None)
    argv = launch['argv'][:1] + args + launch['argv'][1:]
    return dict(launch, env=env, argv=argv, account_pool={'pool': pool, 'status': 'pooled',
                'reason': f'Sent through the {pool} account pool.'})


# ---------------------------------------------------------------- loop
def poll(path, threshold, now=None, readers=(read_ccs, read_codex_lb)):
    """One pass over both pools. A pool that is down or answers malformed data is skipped with a log line."""
    now = time.time() if now is None else now
    for read in readers:
        try:
            snap = read()
        except (OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
            print(f"pools: {read.__name__}: {type(error).__name__}: {error}")
            continue
        for event in events(snap, _serving.get(snap['pool']), threshold, now):
            notices.announce(path, event['key'], event['kind'], event['message'], at=event['at'],
                             account_id=event['account_id'], now=now)
        if snap['serving']:
            _serving[snap['pool']] = snap['serving']
