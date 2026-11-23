"""One-click Debug for a notification item (ledger 35).

POST /api/notifications/act {id, action: "debug"} starts one cheap agent as a visible Library chat
through the broker. The agent follows the runtime monitor's protocol (orchestrator-dashboard
monitor/findings.md sections 20 and 25; approvals go through ~/.claude/hooks/approval_policy.py, the one
rule file the broker already uses) and writes its progress to a small JSON file. The feed reads that file
on every poll, so progress shows without a second store. Contract: docs/redesign-2026-11-22/API.md.
"""
import hashlib
import json
import os
import re
import time
from pathlib import Path

STATES = ('queued', 'running', 'fixed', 'needs_review', 'needs_signin', 'needs_approval', 'failed')
ACTIVE = ('queued', 'running')
FAILED_KINDS = ('limit_reached', 'rate_limited', 'auth_expired', 'not_logged_in')
WAITING_KINDS = ('approval', 'question', 'decision', 'input', 'waiting', 'signed_out', 'blocked', 'stalled', 'dead')
SIGNIN_KINDS = ('auth_expired', 'not_logged_in', 'signed_out')
RULES = os.environ.get('LIBRARY_MONITOR_RULES', str(Path.home() / '.library-monitor' / 'monitor' / 'findings.md'))
POLICY = str(Path.home() / '.claude' / 'hooks' / 'approval_policy.py')
ROOT = Path(__file__).resolve().parent

BRIEF = """{caller}: parent_session_id=none target_provider=Claude
Debug one Library notification. You are a cheap helper; keep every step small and cheap.

Item: {title} (kind {kind}, id {id})
Detail: {body}
Chat: {chat}   Run: {run}   Account: {account}   Monitor thread: {thread}

Do:
1. Find the cause from the evidence above (the run record, the chat transcript, the code) and fix it if you can. Edit code in place.
2. If the item needs her review (a decision, a result, a design choice), start one reviewer agent with the Agent tool, give it the
   evidence and the question, and write its chat key (or its name) as "review_key" below.
3. If a sign-in is needed, start it in the background (never open a window in front): POST http://127.0.0.1:8744/api/accounts/login
   with {{"account_id": "<account>"}}. Then stop and report needs_signin.
4. Permission requests that are not safety-critical are allowed per the monitor's accepted rules ({rules}, sections 20 and 25;
   the rule file is {policy}). Scratch and cache deletions need no approval. Raw data, finished results and anything hard to rebuild
   need her yes: do not delete, report needs_approval and name the file.
5. Never stop, resume or message another running chat; send nothing outward.

Progress: write the JSON file {progress} (create it, then overwrite it at each step):
  {{"state": "running" | "fixed" | "needs_review" | "needs_signin" | "needs_approval" | "failed",
   "text": "one plain line of what you are doing or found", "review_key": "<optional>"}}
Your last step is a file with a final state (not running) and one line of text; then end with: DEBUG: <state>: <text>
"""


# ---------------------------------------------------------------- seams (patched in tests and in the synthetic server)
def _models():
    import server
    return (server.compose_options().get('claude') or {}).get('models') or []


def start_agent(brief, model, progress_path, request_id):
    """Start the agent as a Library chat. Returns {run_id, agent_key}. The synthetic server replaces this."""
    import broker
    result = broker.create_run(None, {}, {'provider': 'claude', 'new': {'tool': 'claude', 'cwd': str(ROOT), 'host_id': 'mac'},
                                          'message': brief, 'model': model, 'request_id': request_id})
    if isinstance(result, tuple):
        raise RuntimeError((result[0] or {}).get('error') or 'the agent did not start')
    run = result.get('run') or {}
    return {'run_id': run.get('id'), 'agent_key': run.get('key') or None}


def _run(run_id):
    import broker
    return broker.instance().store.get(run_id)


def _login(account_id):
    import accounts
    return accounts.start_login(account_id)


# ---------------------------------------------------------------- which items, which model
def debuggable(item):
    """True for open failed items and open items waiting for her (kinds as the feed builds them)."""
    if item.get('state') != 'open':
        return False
    kind = item.get('kind')
    if kind in FAILED_KINDS or kind == 'other' and item.get('run_id'):
        return True
    return kind in WAITING_KINDS or bool(item.get('needs_answer'))


def pick_model(models):
    """A cheap model id from the live list: the newest Haiku, else the newest Sonnet (the list is newest first)."""
    for family in ('haiku', 'sonnet'):
        for m in models:
            mid = str(m.get('id') or '')
            if family in mid.lower() and not mid.lower().endswith('[1m]'):
                return mid
    return ''


def progress_path(data, id):
    return os.path.join(data, 'debug', hashlib.sha1(id.encode()).hexdigest()[:16] + '.json')


def _read(path):
    try:
        with open(path) as fh:
            value = json.load(fh)
        return value if isinstance(value, dict) else None
    except (OSError, ValueError):
        return None


def write_progress(path, state, text, **extra):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + '.tmp'
    with open(tmp, 'w') as fh:
        json.dump(dict({'state': state, 'text': text, 'at': time.time()}, **extra), fh)
    os.replace(tmp, path)


# ---------------------------------------------------------------- status shown on the feed item
def status(entry, path, now=None):
    """The `debug` object of a feed item from its stored entry, the progress file and the agent's run."""
    if not isinstance(entry, dict):
        return None
    now = time.time() if now is None else now
    file = _read(path) or {}
    state = file.get('state') if file.get('state') in STATES else (entry.get('state') or 'queued')
    text = str(file.get('text') or entry.get('text') or '')[:300]
    at = file.get('at') if isinstance(file.get('at'), (int, float)) else entry.get('at') or now
    agent_key, run_id = entry.get('agent_key'), entry.get('run_id')
    if run_id and (state in ACTIVE or not agent_key):
        try:
            run = _run(run_id) or {}
        except Exception:
            run = {}
        agent_key = agent_key or run.get('key') or None
        if state in ACTIVE:
            run_status = run.get('status')
            if run_status in ('failed', 'interrupted'):
                state, text = 'failed', 'The debug agent stopped: ' + str(run.get('error') or run_status)[:200]
            elif run_status == 'completed':
                state, text = 'needs_review', text or 'The agent finished without reporting a result.'
            elif run_status and run_status != 'queued' and state == 'queued':
                state, text = 'running', text or 'The agent is working.'
    return {'state': state, 'text': text or 'Waiting for the agent to start.', 'at': at, 'model': entry.get('model'),
            'agent_key': agent_key, 'review_key': file.get('review_key') or entry.get('review_key') or None}


# ---------------------------------------------------------------- dispatch
def _brief(item, route, data, path):
    caller = 'Claude'
    chat = item.get('chat_key') or 'none'
    return BRIEF.format(caller=caller, title=item.get('title'), kind=item.get('kind'), id=item['id'], body=(item.get('body') or '')[:1500],
                        chat=chat, run=item.get('run_id') or 'none', account=item.get('account_id') or 'none',
                        thread=item.get('thread') or 'none', rules=RULES, policy=POLICY, progress=path)


def dispatch(item, route, data, now):
    """Start the debug agent for one item. Returns (entry, None) or (None, (message, code, status))."""
    path = progress_path(data, item['id'])
    if item.get('kind') in SIGNIN_KINDS and item.get('account_id'):
        # Nothing to diagnose: open the sign-in in the background and say so. No agent, no model call.
        try:
            _login(item['account_id'])
        except (ValueError, RuntimeError) as error:
            return None, (str(error) or 'The sign-in did not start.', 'login_failed', 502)
        write_progress(path, 'needs_signin', 'Sign-in started in the background for ' + item['account_id'] + '. Finish it in the browser.')
        return {'at': now, 'state': 'needs_signin', 'model': None, 'agent_key': None}, None
    model = pick_model(_models())
    if not model:
        return None, ('No cheap model in the live model list.', 'no_model', 409)
    write_progress(path, 'queued', 'Waiting for the agent to start.')
    request_id = 'debug-' + hashlib.sha1((item['id'] + str(now)).encode()).hexdigest()[:16]
    try:
        started = start_agent(_brief(item, route, data, path), model, path, request_id)
    except Exception as error:
        write_progress(path, 'failed', 'The agent did not start: ' + str(error)[:200])
        return {'at': now, 'state': 'failed', 'model': model, 'agent_key': None}, None
    return dict({'at': now, 'state': 'queued', 'model': model, 'agent_key': None}, **started), None
