"""Read-only clipboard payloads; the agent reference is owned by sessions_cli."""
import shlex
import re

def payload(key, context=None):
    import sessions_cli
    if not isinstance(key,str) or not re.fullmatch(r'(claude|codex)-[A-Za-z0-9_-]+',key):
        raise ValueError('A full Claude or Codex session key is required')
    ctx=context or sessions_cli.Context()
    try:
        key=sessions_cli.resolve(ctx,key)
    except SystemExit as error:
        raise ValueError(str(error)) from None
    provider,sid=sessions_cli.split_key(key)
    transcript,_=ctx.transcript(key)
    cwd=ctx.cwd_of(key)
    command=('claude --resume ' if provider=='claude' else 'codex resume ')+shlex.quote(sid)
    if cwd:command='cd -- '+shlex.quote(cwd)+' && '+command
    # cmd_ref uses print(line). Preserve its trailing newline byte for byte.
    return {'ok':True,'key':key,'reference':sessions_cli.ref_line(ctx,key)+'\n',
            'session_id':sid,'transcript_path':transcript or '', 'resume_command':command}

def endpoint(req,query,body):
    try:return payload((query or {}).get('key') or (body or {}).get('key') or '')
    except (ValueError,KeyError,TypeError) as error:return {'ok':False,'error':str(error)},400

ROUTES={('GET','/api/session/reference'):endpoint}
