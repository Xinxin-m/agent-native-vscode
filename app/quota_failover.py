"""Bounded continuation of a saved local Claude turn after confirmed quota exhaustion."""
import hashlib
import json
import os
from pathlib import Path

import auth_failure
import host_connector
import stream

MAX_CONTEXT = 180000
MAX_ACCOUNTS = 8
CONTINUE = ('[Automatic account continuation] Continue the unfinished objective in the saved conversation after its account exhausted its quota. '
            'Do not repeat completed actions or resubmit earlier tool calls. Inspect current files and external '
            'state first, use recorded tool results as evidence, and perform only the remaining work. '
            'If an action has an uncertain outcome, stop and report that uncertainty instead of repeating it.')


def eligible(run, ctrl):
    return (ctrl.get('quota_failure_confirmed') is True and not ctrl.get('stop')
            and run.get('provider') == 'claude' and run.get('host_id', 'mac') in ('mac', 'local')
            and (run.get('operation') or 'message') == 'message'
            and (run.get('failure') or {}).get('kind') == 'limit_reached')


def _snapshot(run, launch):
    """Require a whole saved transcript, including every completed tool pair."""
    path = Path(stream._claude_transcript(launch.get('env') or {}, run['cwd'], run['session_id']))
    # Read through one descriptor with a hard limit, even if another writer grows it.
    with path.open('rb') as handle:
        before = os.fstat(handle.fileno())
        data = handle.read(8 * 1024 * 1024 + 1)
        after = os.fstat(handle.fileno())
    if len(data) > 8 * 1024 * 1024:
        raise ValueError('Saved conversation is too large for a complete automatic handoff.')
    raw = data.decode('utf-8')
    if path.stat().st_ino != after.st_ino:
        raise ValueError('Saved conversation changed while checking the handoff.')
    if (before.st_ino, before.st_size, before.st_mtime_ns) != (after.st_ino, after.st_size, after.st_mtime_ns):
        raise ValueError('Saved conversation changed while checking the handoff.')
    if not raw.endswith('\n'):
        raise ValueError('Saved conversation has an incomplete transcript tail.')
    turns, records, open_tools, seen_tools = [], [], set(), set()
    last_failure = None
    last_user = ''
    for line in raw.splitlines():
        event = json.loads(line)
        if not isinstance(event, dict):
            raise ValueError('Saved conversation contains an invalid native record.')
        sid = event.get('sessionId') or event.get('session_id')
        if sid and sid != run['session_id']:
            raise ValueError('Saved conversation session identity does not match.')
        if event.get('cwd') and os.path.realpath(event['cwd']) != os.path.realpath(run['cwd']):
            raise ValueError('Saved conversation workspace identity does not match.')
        if event.get('isSidechain') or event.get('parent_tool_use_id'):
            continue
        failure = auth_failure.from_event('claude', event)
        if failure:
            last_failure = failure
            continue
        if event.get('type') not in ('user', 'assistant'):
            continue
        if last_failure:
            raise ValueError('The saved quota failure is followed by newer message or tool activity.')
        if event.get('isMeta') or event.get('isCompactSummary'):
            raise ValueError('Saved context includes internal or compacted messages requiring manual review.')
        content = (event.get('message') or {}).get('content') or []
        if isinstance(content, str):
            content = [{'type': 'text', 'text': content}]
        if not isinstance(content, list):
            raise ValueError('Saved conversation has unsupported message content.')
        texts, blocks = [], []
        for block in content:
            if not isinstance(block, dict):
                raise ValueError('Saved conversation has an invalid content block.')
            kind = block.get('type')
            if kind == 'text':
                texts.append(block.get('text') or '')
                blocks.append(block)
            elif kind == 'tool_use':
                tool_id = block.get('id')
                if not tool_id or tool_id in seen_tools:
                    raise ValueError('Saved tool call has a missing or duplicate identity.')
                if (block.get('input') or {}).get('run_in_background'):
                    raise ValueError('Saved conversation includes detached tool work requiring manual review.')
                open_tools.add(tool_id)
                seen_tools.add(tool_id)
                blocks.append(block)
            elif kind == 'tool_result':
                if block.get('tool_use_id') not in open_tools:
                    raise ValueError('Saved tool result has no matching open tool call.')
                open_tools.remove(block['tool_use_id'])
                blocks.append(block)
            elif kind not in ('thinking', 'redacted_thinking'):
                raise ValueError('Saved conversation contains attachments or content that cannot be transferred completely.')
        if blocks:
            records.append({'role': event['type'], 'content': blocks})
        if texts:
            last_failure = None
            text = '\n'.join(texts)
            turns.append({'role': event['type'], 'text': text})
            if event['type'] == 'user':
                last_user = text
    if open_tools:
        raise ValueError('A saved tool call has no recorded result; its outcome needs manual review.')
    if not last_failure or last_failure['kind'] != 'limit_reached' or last_failure['raw'] != run['failure']['raw']:
        raise ValueError('The latest saved turn does not confirm this quota failure.')
    expected = run.get('submitted_message') or run.get('prompt') or run.get('message')
    refs = [f"{a['name']}: {a['path']}" for a in run.get('attachment_records', []) if not a['mime'].startswith('image/')]
    if refs:
        expected += '\n\nAttached local files (read these paths using file tools):\n' + '\n'.join(refs)
    if not turns or not expected or last_user.strip() != expected.strip():
        raise ValueError('The saved user turn does not match the interrupted request.')
    evidence = 'Ordered native messages and completed tools:\n' + '\n'.join(json.dumps(record, ensure_ascii=False) for record in records)
    if sum(len(t['text']) for t in turns) + len(evidence) > MAX_CONTEXT:
        raise ValueError('Saved context would be truncated during automatic handoff.')
    import indexer
    parsed = indexer.parse_claude(str(path))
    if not parsed:
        raise ValueError('The saved native conversation could not be parsed completely.')
    return {'turns': turns, 'native_source_turn': len(parsed.get('turns') or []) - 1}, evidence


def _native_checkpoint(run, launch):
    """Check provider-owned history without copying or truncating its context."""
    path = Path(stream._claude_transcript(launch.get('env') or {}, run['cwd'], run['session_id']))
    last_failure, last_user = None, ''
    open_tools, seen_tools, background = set(), set(), set()
    with path.open('rb') as handle:
        before = os.fstat(handle.fileno())
        while True:
            # Images can make a native record large; bound a record, not the history.
            line = handle.readline(64 * 1024 * 1024 + 1)
            if not line:
                break
            if len(line) > 64 * 1024 * 1024 or not line.endswith(b'\n'):
                raise ValueError('Saved native checkpoint has an oversized record or incomplete tail.')
            event = json.loads(line)
            if not isinstance(event, dict):
                raise ValueError('Saved native checkpoint contains an invalid record.')
            sid = event.get('sessionId') or event.get('session_id')
            if sid and sid != run['session_id']:
                raise ValueError('Saved native checkpoint session identity does not match.')
            if event.get('cwd') and os.path.realpath(event['cwd']) != os.path.realpath(run['cwd']):
                raise ValueError('Saved native checkpoint workspace identity does not match.')
            if event.get('isSidechain') or event.get('parent_tool_use_id'):
                continue
            failure = auth_failure.from_event('claude', event)
            if failure:
                last_failure = failure
                continue
            if event.get('type') == 'system':
                subtype = event.get('subtype')
                if subtype == 'background_tasks_changed':
                    background = {t['task_id'] for t in event.get('tasks') or [] if isinstance(t, dict) and t.get('task_id')}
                elif subtype == 'task_started' and event.get('is_backgrounded'):
                    background.add(event.get('task_id') or 'unknown')
                elif subtype == 'task_notification':
                    background.discard(event.get('task_id'))
            if event.get('type') not in ('user', 'assistant'):
                continue
            internal = event.get('isMeta') or event.get('isCompactSummary')
            content = (event.get('message') or {}).get('content') or []
            if isinstance(content, str):
                content = [{'type': 'text', 'text': content}]
            if not isinstance(content, list):
                raise ValueError('Saved native checkpoint has unsupported message content.')
            if last_failure and not internal and content:
                raise ValueError('The saved quota failure is followed by newer conversation activity.')
            texts = []
            for block in content:
                if not isinstance(block, dict):
                    raise ValueError('Saved native checkpoint has an invalid content block.')
                kind = block.get('type')
                if kind in ('tool_use', 'tool_result'):
                    if last_failure:
                        raise ValueError('The saved quota failure is followed by newer tool activity.')
                    if kind == 'tool_use':
                        tool_id = block.get('id')
                        if not tool_id or tool_id in seen_tools or len(seen_tools) >= 100000:
                            raise ValueError('Saved native tool identities are missing, duplicated or excessive.')
                        if (block.get('input') or {}).get('run_in_background'):
                            raise ValueError('Saved conversation includes detached tool work requiring manual review.')
                        seen_tools.add(tool_id)
                        open_tools.add(tool_id)
                    else:
                        if block.get('tool_use_id') not in open_tools:
                            raise ValueError('Saved native tool result has no matching open tool call.')
                        open_tools.remove(block['tool_use_id'])
                elif kind == 'text' and not internal:
                    texts.append(block.get('text') or '')
            if texts:
                if last_failure:
                    raise ValueError('The saved quota failure is followed by a newer conversation turn.')
                if event['type'] == 'user':
                    last_user = '\n'.join(texts)
        after = os.fstat(handle.fileno())
    current = path.stat()
    if ((before.st_ino, before.st_size, before.st_mtime_ns) != (after.st_ino, after.st_size, after.st_mtime_ns)
            or (current.st_ino, current.st_size, current.st_mtime_ns) != (after.st_ino, after.st_size, after.st_mtime_ns)):
        raise ValueError('Saved native checkpoint changed during verification.')
    if background:
        raise ValueError('Saved native background work has no recorded completion.')
    if open_tools:
        raise ValueError('A saved native tool call has no recorded result; its outcome needs manual review.')
    if not last_failure or last_failure['kind'] != 'limit_reached' or last_failure['raw'] != run['failure']['raw']:
        raise ValueError('The latest saved native turn does not confirm this quota failure.')
    expected = run.get('submitted_message') or run.get('prompt') or run.get('message')
    refs = [f"{a['name']}: {a['path']}" for a in run.get('attachment_records', []) if not a['mime'].startswith('image/')]
    if refs:
        expected += '\n\nAttached local files (read these paths using file tools):\n' + '\n'.join(refs)
    if not expected or last_user.strip() != expected.strip():
        raise ValueError('The saved native user turn does not match the interrupted request.')


def continue_run(obj, rid, launch, ctrl):
    """Called once cleanup has reaped the provider and released its ownership locks."""
    import broker
    run = obj.store.get(rid)
    if not eligible(run, ctrl):
        return None
    previous = run.get('quota_failover') or {}
    if previous.get('successor_run_id'):
        return obj.store.get(previous['successor_run_id'])
    marker = dict(status='switching', from_account_id=run.get('account_id'),
                  reason='Checking saved conversation before switching accounts.')
    def publish(**fields):
        marker.update(fields)
        obj.store.update(rid, quota_failover=marker)
        obj.store.emit(rid, 'account_switch', dict(marker))
    successor = None
    try:
        proc = ctrl.get('proc')
        if not proc or proc.poll() is None or launch.get('transport', 'local') != 'local':
            raise ValueError('The prior local provider process has not been verified exited.')
        if any(ctrl.get(k) for k in ('background', 'pending_requests', 'claude_inputs', 'pending_rpc', 'auto_compacting')):
            raise ValueError('Background work, pending permission or unconfirmed steering requires manual review.')
        if not run.get('account_id') or not run.get('session_id'):
            raise ValueError('The interrupted conversation has no verified account or native session binding.')
        model = run.get('effective_model') or run.get('model')
        if not model:
            raise ValueError('The interrupted model is unknown; capacity cannot be checked.')
        attempted = list(dict.fromkeys([*(run.get('quota_failover_attempted_accounts') or []), run['account_id']]))
        if len(attempted) >= MAX_ACCOUNTS:
            raise ValueError('Automatic continuation reached its account-attempt limit.')
        selected = host_connector.select_quota_failover('claude', model, run['account_id'], attempted)
        if not selected or selected.get('account_id') in attempted:
            raise ValueError('No other signed-in identity has verified available capacity for this model.')
        try:
            host_connector.shared_session_store('claude', run['account_id'], selected['account_id'])
            shared_store = True
        except ValueError:
            shared_store = False
        meta = {k: run[k] for k in ('cwd', 'account_id', 'host_id', 'conversation_key') if k in run}
        meta.update(id=run['session_id'], source='claude')
        payload = {'key': run['key'], 'message': CONTINUE, 'provider': 'claude',
            'model': model, 'effort': run.get('effort') or '',
            'account': selected['account_id'], 'request_id': 'quota-handoff-' + rid}
        provenance = {'run_id': rid, 'account_id': run['account_id']}
        if shared_store:
            _native_checkpoint(run, launch)
            meta['account_id'] = selected['account_id']
            for key in ('source_key', 'source_session_id', 'source_provider', 'source_turn'):
                if key in run:
                    meta[key] = run[key]
            spec = broker.prepare_run(payload, {run['key']: meta}, lambda key: {})
            spec['shared_session_from'] = run['account_id']
            provenance.update(mode='native_resume', native_session_id=run['session_id'])
        else:
            # A prior automatic segment already contains its transferred context
            # in its saved prompt. Separate stores require a complete linked copy.
            chat, evidence = _snapshot(run, launch)
            payload['handoff'] = True
            spec = broker.prepare_run(payload, {run['key']: meta}, lambda key: chat, lambda key: evidence)
            if spec.get('context_omitted_chars'):
                raise ValueError('Saved context would be truncated during automatic handoff.')
            spec['source_turn'] = chat['native_source_turn']
            provenance.update(mode='linked_handoff', prompt_sha256=hashlib.sha256(spec['prompt'].encode()).hexdigest())
        marker['mode'] = provenance['mode']
        spec.update(quota_failover_from=provenance,
                    quota_failover_attempted_accounts=attempted + [selected['account_id']])
        publish(to_account_id=selected['account_id'], reason='Continuing with verified available model quota.')
        successor = obj.start(spec)
        publish(status='continued', successor_run_id=successor['id'],
                reason='Account quota exhausted; continued the saved conversation on another account.')
        obj.store.emit(successor['id'], 'account_switch', dict(marker))
        return successor
    except Exception as exc:
        if successor is not None:
            # Startup succeeded: a notification failure must never imply that
            # no work is running, or permit a second automatic submission.
            marker.update(status='continued', successor_run_id=successor['id'])
            try:
                obj.store.update(rid, quota_failover=marker)
            except Exception:
                pass  # the stable request ID still identifies the successor
            return successor
        publish(status='blocked', reason=str(exc))
        return None
