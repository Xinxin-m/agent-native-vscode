"""Read-only execution defaults and an explicit native folder picker. Never launches agents."""
import json
import os
import subprocess
import sys
import tomllib
from pathlib import Path
import host_connector

HERE=Path(__file__).resolve().parent


def _json(path, fallback):
    try:
        return json.loads(Path(path).read_text())
    except (OSError,ValueError):
        return fallback


def local_defaults(provider, meta=None, account=None, model=None):
    meta=meta or {}
    # provider_account_id identifies a provider user, never a configured CLI home.
    bound=meta.get('account_id') or meta.get('account')
    wanted=bound or account
    resolve=lambda aid:host_connector.resolve_local_account(provider,account_id=aid,model=model or meta.get('model_main'),
                     session_id=meta.get('id'),cwd=os.path.realpath(meta.get('cwd') or str(Path.home())))
    try:
        selection=resolve(wanted)
    except host_connector.SessionAccountMismatch as exc:
        # A stale binding (the profile signed in to another login since) must not block
        # the resume: pick among the profiles that can read the transcript instead.
        if not exc.candidates:
            error=host_connector.AmbiguousSessionAccount(str(exc));error.candidates=[];raise error from exc
        selection=dict(resolve(None),requested_account_id=wanted)
    aid,directory,env=host_connector.local_profile(provider,selection['account_id'])
    directory=os.path.realpath(directory)
    config={}
    try:
        if provider=='codex':
            with open(Path(directory)/'config.toml','rb') as handle:
                config=tomllib.load(handle)
            if config.get('profile'):
                config={**config,**config.get('profiles',{}).get(config['profile'],{})}
        else:
            config=_json(Path(directory)/'settings.json',{})
    except (OSError,ValueError):
        pass
    result={'provider':provider,'host_id':'mac','account':aid,'model':config.get('model') or '',
            'selectedAccount':aid, 'nativeAccount':aid if meta.get('id') else '', 'native_provider_account_id':meta.get('provider_account_id') or '',
            'effort':config.get('model_reasoning_effort') or config.get('effortLevel') or '',
            'account_selection':selection}
    if provider=='claude':
        configured_env=config.get('env') or {}
        result['model']=configured_env.get('ANTHROPIC_MODEL') or result['model']
        result['effort']=configured_env.get('CLAUDE_CODE_EFFORT_LEVEL') or result['effort']
    if not meta.get('id'):result['account']=''
    if meta.get('cwd'):result['cwd']=meta['cwd']
    # The chat's own last model and effort, as indexed; the owner-profile transcript below may refine them.
    resume=meta.get('resume') or {}
    result['model']=resume.get('model') or result['model']
    result['effort']=resume.get('effort') or result['effort']
    paths=meta.get('files_all') or [meta.get('file')]
    # Only consult session files in the verified owner profile, never a mirror.
    paths=[Path(p) for p in paths if p and Path(p).is_file() and os.path.commonpath([os.path.realpath(p),directory])==directory]
    for path in sorted(paths,key=lambda p:p.stat().st_mtime):
        try:
            with path.open('rb') as handle:
                handle.seek(max(0,path.stat().st_size-4*1024*1024))
                if handle.tell():handle.readline()
                for line in handle:
                    try:row=json.loads(line)
                    except (ValueError,UnicodeError):continue
                    payload=row.get('payload') or {}
                    if row.get('type')=='turn_context':
                        result['model']=payload.get('model') or result['model']
                        result['effort']=payload.get('effort') or payload.get('reasoning_effort') or result['effort']
                    elif provider=='claude' and not row.get('isSidechain'):
                        msg=row.get('message') or {}
                        if row.get('type')=='assistant' and msg.get('model') and msg['model']!='<synthetic>':result['model']=msg['model']
                        # The session's own effort, never a subagent's.
                        effort=((row.get('params') or {}).get('output_config') or {}).get('effort')
                        if row.get('type')=='api-request' and effort and not str(row.get('querySource','')).startswith('agent:'):result['effort']=effort
        except OSError:continue
    if model:
        result['model']=model
    return result


def defaults(req,query,body):
    try:
        key=query.get('key') or ''
        meta={}
        if key:
            meta=_json(HERE/'data/index.json',{}).get(key)
            if not meta:raise ValueError('Unknown native conversation')
        provider=query.get('provider') or meta.get('source') or 'claude'
        if provider not in ('claude','codex'):raise ValueError('Unknown provider')
        native=meta if meta.get('source')==provider and query.get('handoff')!='1' else {}
        host=native.get('execution_host') or native.get('host_id') or query.get('host_id') or native.get('node') or 'mac'
        if '+' in host:host=query.get('host_id') if query.get('host_id') in host.split('+') else host
        if host not in ('mac','local'):
            return {'defaults':{'provider':provider,'host_id':host,**({'cwd':native['cwd']} if native.get('cwd') else {}),'model':query.get('model') or (native.get('resume') or {}).get('model') or native.get('model_main') or '', 'effort':(native.get('resume') or {}).get('effort') or '','account':native.get('account_id') or query.get('account') or ''}}
        notice=''
        try:
            value=local_defaults(provider,native,query.get('account'),query.get('model'))
        except host_connector.AmbiguousSessionAccount as exc:
            # Profiles that share one login already resolved upstream. Only different
            # logins reach here; that goes to the notification center, once per chat.
            value=local_defaults(provider,{k:v for k,v in native.items() if k not in ('id','account_id','account')},None,query.get('model'))
            candidates=list(getattr(exc,'candidates',()))
            value.update(account='',selectedAccount='',account_required=True,account_candidates=candidates)
            value.pop('account_selection',None)
            # Only a transcript no profile can read is a real failure; subagent transcripts never resume natively.
            if not candidates and not native.get('is_child') and '-agent-' not in key:
                import notices
                title=(native.get('title_seed') or 'this chat')[:80]
                notices.note(HERE/'data/notices.json','account:'+key,f'{provider.title()} chat "{title}" cannot be resumed: no {provider.title()} account on this Mac can read its transcript.')
        if not value.get('cwd'):
            import hosts
            value['cwd']=hosts.get_host('mac')['default_cwd']
        return {'defaults':value,**({'notice':notice} if notice else {})}
    except (ValueError,OSError) as exc:
        return {'ok':False,'error':str(exc)},400


def choose_folder(initial=''):
    if sys.platform!='darwin':raise ValueError('The native folder picker is available on macOS; enter a folder path instead.')
    initial=initial if isinstance(initial,str) and os.path.isdir(initial) else str(Path.home())
    script='''on run argv
try
set chosen to choose folder with prompt "Choose a conversation folder" default location (POSIX file (item 1 of argv))
return POSIX path of chosen
on error number -128
return ""
end try
end run'''
    result=subprocess.run(['/usr/bin/osascript','-l','AppleScript','-',initial],input=script,capture_output=True,text=True,timeout=120)
    if result.returncode:raise ValueError('The folder picker could not open. Enter the folder path instead.')
    path=result.stdout.strip()
    if not path:return {'cancelled':True}
    if not os.path.isdir(path):raise ValueError('The selected folder is unavailable')
    return {'path':os.path.realpath(path)}


def picker(req,query,body):
    try:
        if body.get('host_id','mac') not in ('mac','local'):raise ValueError('Enter the folder path on the selected remote host.')
        return choose_folder(body.get('path') or '')
    except (ValueError,OSError,subprocess.TimeoutExpired) as exc:return {'ok':False,'error':str(exc)},400

ROUTES={('GET','/api/conversation/defaults'):defaults,('POST','/api/conversation/folder'):picker}
