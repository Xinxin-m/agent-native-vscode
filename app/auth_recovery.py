"""Explicit chat recovery actions. No retry or credential change runs implicitly."""
import uuid
from pathlib import Path
import auth_failure
import stream

def source(obj,body):
    import broker,indexer
    run=obj.store.get(body['id']) if body.get('id') else None
    key=(run or {}).get('key') or body.get('key') or ''
    index=stream._jload(stream.INDEX,{})
    meta=dict(index.get(key) or {})
    if run:
        if run.get('status')!='failed':raise ValueError('Only a failed run can be recovered')
        meta.update({k:run[k] for k in ('cwd','account_id','host_id','conversation_key') if run.get(k)})
        meta.update(id=run.get('session_id') or '',source=run['provider'])
    if not meta and key:
        import sessions_cli
        broker.safe_id(key)
        provider,sid=sessions_cli.split_key(key)
        if provider not in ('claude','codex') or not sid:
            raise ValueError('Unknown native conversation')
        native_path,host=sessions_cli.find_transcript(provider,sid)
        if native_path and host in ('mac','local'):
            parsed=indexer.parse_claude(native_path) if provider=='claude' else indexer.parse_codex_group(sid,[native_path],{})
            if parsed:
                meta=dict(parsed,host_id=host)
    if not meta:raise ValueError('Conversation is not indexed yet')
    chat=stream._jload(str(Path(stream.CHATS)/(broker.safe_id(key)+'.json')),{}) if key else {}
    # Native official panels do not write broker runs. Read their known transcript
    # afresh rather than trusting a caller-supplied error or a stale cleaned file.
    path=meta.get('file') or chat.get('file')
    if path and Path(path).is_file():
        parsed=(indexer.parse_claude(path) if meta['source']=='claude' else indexer.parse_codex_group(meta['id'],meta.get('files_all') or [path],{}))
        if parsed:chat=parsed
    # Official panels can have switched accounts since indexing. An explicit
    # current account is source ownership only for native login/retry, never a
    # broker-run override or the target of an account/handoff action.
    explicit=body.get('account_id')
    if not run and explicit and body.get('action') in ('login','retry'):
        import host_connector
        provider=meta.get('source')
        if provider not in ('claude','codex') or meta.get('host_id',meta.get('node','mac')) not in ('mac','local'):
            raise ValueError('Explicit native account recovery requires a local provider session')
        _,home,_=host_connector.local_profile(provider,explicit)
        store=(Path(home)/('projects' if provider=='claude' else 'sessions')).resolve()
        paths=[path]+list(meta.get('files_all') or [])
        if not path or any(not item or not Path(item).is_file() or not Path(item).resolve().is_relative_to(store) for item in paths):
            raise ValueError('This account does not contain the native transcript')
        meta['account_id']=explicit
        chat=dict(chat,account_id=explicit)
    turns=chat.get('turns') or []
    failure=(run or {}).get('failure') or (auth_failure.classify(meta['source'],run['error'],meta.get('account_id'),run.get('updated_at')) if run and run.get('error') else None)
    error_index=next((i for i in range(len(turns)-1,-1,-1) if turns[i].get('error') or turns[i].get('failure')),None)
    if not failure and error_index is not None:
        failure=turns[error_index].get('failure') or auth_failure.classify(meta['source'],turns[error_index].get('text',''),meta.get('account_id'),turns[error_index].get('ts'))
    if not failure:raise ValueError('No failed turn is available to recover')
    native_message = next((t.get('text','') for t in reversed(turns[:error_index]) if t.get('role')=='user'),'') if error_index is not None else ''
    message=(run or {}).get('failed_message') or (run or {}).get('submitted_message') or native_message or (run or {}).get('message') or ''
    if not run and error_index is not None and any(t.get('role')=='assistant' and not t.get('error') for t in turns[error_index+1:]):
        raise ValueError('This conversation has already recovered')
    return run,key,meta,chat,failure,message,error_index

def assert_idle(obj, key, meta, chat):
    import broker,live
    sid=meta.get('id')
    for run in obj.store.list():
        if run.get('status') not in broker.TERMINAL and (key and run.get('key')==key or sid and run.get('provider')==meta.get('source') and run.get('session_id')==sid):
            raise broker.SessionBusy(run['id'])
    path=meta.get('file') or chat.get('file')
    if path and Path(path).is_file() and auth_failure.transcript_failure(meta['source'],path,meta.get('account_id')).get('active'):
        raise ValueError('This native session has a newer active turn; return to its existing chat')
    rows, _ = live.mac_sessions()  # The second list is bare PIDs without session identity.
    for row in rows:
        if not isinstance(row, dict) or row.get('alive') is False:
            continue
        exact=(key and row.get('chat_key')==key or sid and row.get('session_id')==sid)
        if exact and row.get('status')!='idle':
            raise ValueError('This native session is running or its status is uncertain')


def recover(obj,body):
    import accounts,broker,host_connector
    run,key,meta,chat,failure,message,error_index=source(obj,body)
    action=body.get('action');provider=meta['source'];account=(run or {}).get('account_id') or meta.get('account_id') or failure.get('account_id')
    host=meta.get('host_id') or meta.get('node') or 'mac'
    def request_for(kind,extra):
        marker=(run or {}).get('id') or key+':'+str(error_index)+':'+failure['raw']
        return 'recover-'+uuid.uuid5(uuid.NAMESPACE_URL,marker+':'+str(kind)+':'+str(extra.get('account_id') or extra.get('provider') or '')).hex
    remote=host not in ('mac','local')
    if action=='login' and remote:
        # The remote account signs in on its own host; never start this Mac's login of the same id.
        if not account:raise ValueError('Choose the account that owns this chat first')
        try:login=accounts.start_remote_login(host,account,provider)
        except RuntimeError as error:raise ValueError(str(error)) from error
        return {'ok':True,'account_id':account,'host_id':host,'login':login}
    if action=='login_code':
        if not remote or not account:raise ValueError('Enter the code in the sign-in card that started it')
        done=obj.store.find_request(request_for('retry',{}))
        if done and done.get('status')!='failed':return {'ok':True,'run':done,'events':obj.store.events(done['id'])}
        try:accounts.submit_login_code(host+':'+account,body.get('login_id'),body.get('code'))
        except RuntimeError as error:raise ValueError(str(error)) from error
        action='retry'  # signed in: continue the same message once (stable request id below)
    if action=='login':
        if not account:raise ValueError('Choose the account in Accounts first; session ownership is unknown')
        result=accounts.start_login(account,when_idle=True)
        login=dict(result.get('login') or result)
        login.setdefault('url',login.get('authorization_url'))
        login.setdefault('needs_code',login.get('code_required',provider=='claude' and bool(login.get('login_id'))))
        login.setdefault('status','pending' if login.get('login_id') else 'started')
        return {'ok':True,'account_id':account,'login':login}
    if action not in ('retry','account','handoff','bind'):raise ValueError('Unknown recovery action')
    if not message:raise ValueError('The failed user message is unavailable; your draft is preserved')
    if action=='bind':
        choices=[c.get('id') for c in failure.get('candidates') or []]
        if failure.get('kind')!='account_required' or body.get('account_id') not in choices:raise ValueError('Choose one of the listed accounts')
        account=body['account_id']
    if not account and action!='handoff':raise ValueError('Session account is unknown; choose it in Accounts first')
    # Existing native/logical ownership locks reject concurrent requests. A stable
    # request id also makes a double click idempotent after a fast failed retry.
    request_id=body.get('request_id') or request_for(action,body)
    prior=obj.store.find_request(request_id)
    if prior and (body.get('request_id') or prior.get('status') != 'failed'):
        return {'ok':True,'run':prior,'events':obj.store.events(prior['id'])}
    if prior:
        request_id += '-' + uuid.uuid4().hex
    assert_idle(obj,key,meta,chat)
    if not run and error_index is not None and any(t.get('role')=='user' for t in chat.get('turns',[])[error_index+1:]):
        raise ValueError('A newer user turn follows this failure; return to that chat')
    same=lambda r:(run.get('key') and r.get('key')==run['key']) or (run.get('conversation_key') and r.get('conversation_key')==run['conversation_key'])
    if run and any(r['id']!=run['id'] and r.get('recovery_of') not in (run['id'],key) and r.get('status')!='failed' and r.get('created_at','')>run.get('created_at','') and same(r) for r in obj.store.list()):
        raise ValueError('A newer message follows this failure; return to that chat')
    target=body.get('provider') if action=='handoff' else provider
    if action=='handoff' and target not in ('claude','codex'):raise ValueError('Choose Claude or Codex for handoff')
    selected=body.get('account_id') if action=='account' else account
    shared_from=None
    if action=='account':
        if not selected or selected==account:raise ValueError('Choose another account of this provider')
        if meta.get('host_id',meta.get('node','mac')) not in ('mac','local'):raise ValueError('Account recovery on remote hosts is not available')
        host_connector.shared_session_store(provider,account,selected)
        shared_from=account
    local_meta=dict(meta,account_id=selected)
    index={key:local_meta} if key and meta.get('id') else {}
    payload={'key':key if index else '', 'message':message,'provider':target,
             'model':(run or {}).get('model','') if action!='handoff' else '',
             'effort':(run or {}).get('effort','') if action!='handoff' else '',
             'account':selected if action!='handoff' else body.get('account_id',''),
             'host_id':meta.get('host_id') or meta.get('node') or 'mac',
             'new':{'cwd':meta.get('cwd'),'tool':target},'handoff':action=='handoff',
             'request_id':request_id,'conversation_key':meta.get('conversation_key') or key,
             'attachments':list((run or {}).get('submitted_attachments', (run or {}).get('attachments') or [])) if action!='handoff' else []}
    if action=='handoff':
        payload['through_turn']=error_index-1 if error_index is not None else len(chat.get('turns') or [])-1
    if remote:
        import hosts
        if not hosts.get_host(host).get('can_execute'):
            hosts.probe_host(host)  # one explicit bounded probe; launch still requires it to pass
    def load(k):
        if k==key:return chat
        return stream._jload(str(Path(stream.CHATS)/(broker.safe_id(k)+'.json')),{})
    spec=broker.prepare_run(payload,index,load,None,clean_handoff=True)
    if shared_from:spec['shared_session_from']=shared_from
    spec['recovery_of']=(run or {}).get('id') or key
    new_run=obj.start(spec)
    return {'ok':True,'run':new_run,'events':obj.store.events(new_run['id'])}
