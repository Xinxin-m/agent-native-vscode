"""Explicit, host-bound provider model discovery; no credential or catalog fallback."""
import copy
from collections import OrderedDict
import json
import threading
import os
import selectors
import subprocess
import time
import host_connector
import hosts


def validate_response(response, request):
    if not isinstance(response, dict) or response.get('ok') is not True:
        raise ValueError('Invalid connector model response')
    for key in ('host_id', 'machine_id', 'job_id', 'account_id'):
        if response.get(key) != request.get(key):
            raise ValueError('Connector identity or account changed; probe again')
    if response.get('cwd') != request['cwd'] or response.get('provider') != request['tool'] or not isinstance(response.get('cwd'), str) or not response['cwd'].startswith('/'):
        raise ValueError('Connector provider or workspace mismatch')
    models = response.get('models')
    if not isinstance(models, list) or not models or len(models) > 100:
        raise ValueError('Invalid provider model catalog')
    for model in models:
        if not isinstance(model, dict) or not isinstance(model.get('id'), str) or not host_connector.MODEL.fullmatch(model['id']) or not isinstance(model.get('name'), str) or not isinstance(model.get('efforts'), list):
            raise ValueError('Invalid provider model catalog')
    return response


def _read_connector(argv, *, timeout=25):
    child = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=0)
    selector = selectors.DefaultSelector()
    data = bytearray()
    try:
        selector.register(child.stdout, selectors.EVENT_READ)
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not selector.select(remaining):
                raise ValueError('Remote model discovery timed out')
            chunk = os.read(child.stdout.fileno(), 65536)
            if not chunk:
                break
            data.extend(chunk)
            if len(data) > 2000000:
                raise ValueError('Connector model response exceeds size limit')
        try:
            status = child.wait(timeout=max(0.01, deadline-time.monotonic()))
        except subprocess.TimeoutExpired:
            raise ValueError('Remote model discovery timed out')
        if status:
            raise ValueError('Remote connector model discovery failed; verify connector and selected account')
        try:
            return json.loads(data)
        except (ValueError, UnicodeDecodeError):
            raise ValueError('Invalid connector model response')
    finally:
        selector.close()
        try:
            child.stdout.close()
        except OSError:
            pass
        if child.poll() is None:
            child.terminate()
        try:
            child.wait(timeout=2)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait()


_CACHE = OrderedDict()
_LOCKS = {}
_GUARD = threading.Lock()
_REMOTE_SLOTS = threading.BoundedSemaphore(2)
_CACHE_SECONDS = 60

def _binding(host, provider, account_id):
    if host.get('transport') != 'ssh':
        raise ValueError('This discovery operation requires a remote execution host')
    if not host.get('can_execute') or not host.get('machine_id'):
        raise ValueError('Execution host needs a fresh probe')
    if 'models' not in (host.get('capabilities') or []):
        raise ValueError('Update and probe the remote connector for model discovery')
    account = next((a for a in host.get('accounts', []) if a.get('id') == account_id and a.get('tool') == provider), None)
    if not account:
        raise ValueError('Selected account is not configured on this host')
    usage = account.get('usage') if isinstance(account.get('usage'), dict) else {}
    if account.get('disabled') is True or account.get('eligible') is False or account.get('logged_in') is False or usage.get('logged_in') is False:
        raise ValueError('Selected account is disabled, ineligible or signed out')
    public = {k: account.get(k) for k in ('id','tool','disabled','eligible','logged_in','email','plan','models')}
    public['usage_logged_in'] = usage.get('logged_in')
    return (host.get('machine_id'), host.get('job_id'), host.get('execution_alias'), host.get('connector_path'), json.dumps(public, sort_keys=True, separators=(',', ':')))


def discover(host_id, provider, account_id, cwd):
    host_connector.validate_request(provider, cwd, None, None, None, 'exec')
    if not isinstance(account_id, str) or not host_connector.PROFILE.fullmatch(account_id):
        raise ValueError('Choose a valid account for model discovery')
    host = hosts.get_host(host_id)
    binding = _binding(host, provider, account_id)
    request = dict(host_id=host_id, machine_id=binding[0], job_id=binding[1], tool=provider, account_id=account_id, cwd=cwd)
    key = (host_id, provider, account_id, cwd, binding)
    with _GUARD:
        entry = _LOCKS.get(key)
        if entry is None:
            if len(_LOCKS) >= 128:
                raise ValueError('Too many model discovery requests currently active; try again later')
            entry = _LOCKS[key] = [threading.Lock(), 0]
        entry[1] += 1
    try:
        with entry[0]:
            current = hosts.get_host(host_id)
            if _binding(current, provider, account_id) != binding:
                raise ValueError('Execution host or account changed; probe again')
            with _GUARD:
                cached = _CACHE.get(key)
                if cached and time.monotonic() - cached[0] < _CACHE_SECONDS:
                    _CACHE.move_to_end(key)
                    return copy.deepcopy(cached[1])
            if not _REMOTE_SLOTS.acquire(blocking=False):
                raise ValueError('Remote model discovery is busy; try again shortly')
            try:
                argv = host_connector.ssh_command(current, 'models', request)
                result = validate_response(_read_connector(argv, timeout=host_connector.ssh_timeout(current, 25)), request)
            finally:
                _REMOTE_SLOTS.release()
            if _binding(hosts.get_host(host_id), provider, account_id) != binding:
                raise ValueError('Execution host or account changed; probe again')
            with _GUARD:
                _CACHE[key] = (time.monotonic(), copy.deepcopy(result))
                _CACHE.move_to_end(key)
                while len(_CACHE) > 128:
                    _CACHE.popitem(last=False)
            return result
    finally:
        with _GUARD:
            entry[1] -= 1
            if not entry[1]:
                del _LOCKS[key]


def post_discover(req, query, body):
    origin = req.headers.get('Origin', '') if req is not None else ''
    host = req.headers.get('Host', '') if req is not None else ''
    if origin and origin not in ('http://' + host, 'https://' + host):
        return {'error': 'Cross-origin model discovery rejected'}, 403
    try:
        if not isinstance(body, dict):
            raise ValueError('Expected a JSON object')
        return discover(body.get('host_id'), body.get('provider'), body.get('account_id'), body.get('cwd'))
    except (ValueError, OSError, TypeError) as exc:
        return {'error': str(exc)}, 400


ROUTES = {('POST', '/api/models/discover'): post_discover}
BACKGROUND = []
