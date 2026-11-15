"""Evidence-based adapter for agent-called ChatGPT conversations, never account history."""
import hashlib,json,os,re,time,shlex
from pathlib import Path
from datetime import datetime

DEFAULT_JOBS=Path.home()/'.claude/state/cgpt-jobs'
CONV=re.compile(r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}',re.I)
def stamp(v):
 if isinstance(v,(int,float)):return float(v)
 try:return datetime.fromisoformat(str(v).replace('Z','+00:00')).timestamp()
 except (ValueError,TypeError):return 0

def read(path,default=None):
 try:return json.loads(Path(path).read_text())
 except (OSError,ValueError):return default if default is not None else {}

def consultation_state(events,now=None):
 now=now or time.time()
 if not events:return {'state':'unknown','evidence':'No observation'}
 e=events[-1];kind=e.get('kind');at=e.get('time',0)
 if kind=='retrieved':state='complete';evidence='Final reply saved locally'
 elif kind=='reply' and e.get('done'):state='complete';evidence='ChatGPT final reply observed'
 elif kind=='submitted':state='submitted';evidence='Prompt submitted; response not yet observed'
 elif kind=='progress' and e.get('turn_found') and not e.get('done') and 0<=now-at<90:state='working';evidence='Fresh ChatGPT response observation'
 else:state='unknown';evidence='Observation stale or unavailable; no work inferred'
 return {'state':state,'evidence':evidence,'at':at,'phase':'retrieved' if kind=='retrieved' else 'reply_ready' if kind=='reply' and e.get('done') else state}

def web_id(conv):return 'chatgpt-'+conv

def launch_commands(raw):
 """Recognize shell argv, not prose or echoed example commands."""
 commands=[]
 try:
  obj=json.loads(raw)
  if isinstance(obj,dict):commands=[obj.get('command') or obj.get('cmd') or obj.get('input') or '']
 except (ValueError,TypeError):
  # functions.exec source embeds literal JSON-compatible cmd strings.
  for value in re.findall(r'(?:cmd|command)\s*:\s*("(?:\\.|[^"\\])*")',raw):
   try:commands.append(json.loads(value))
   except ValueError:pass
 valid=[]
 for command in commands:
  if '<<' in command:continue  # Heredoc payload is data, not an executed shell line.
  try:
   lexer=shlex.shlex(command,posix=True,punctuation_chars=';&|');lexer.whitespace_split=True;tokens=list(lexer)
  except ValueError:continue
  segments=[[]]
  for token in tokens:
   if token in (';','&&','||','|','&'):segments.append([])
   else:segments[-1].append(token)
  variables={}
  for tokens in segments:
   for token in tokens:
    if re.match(r'^[A-Za-z_][A-Za-z0-9_]*=',token):
     name,value=token.split('=',1);variables[name]=value
    else:break
   tokens=[re.sub(r'\$([A-Za-z_][A-Za-z0-9_]*)',lambda m:variables.get(m[1],m[0]),v) for v in tokens]
   for i,token in enumerate(tokens):
    if Path(token).name not in ('cgpt.py','pw_gpt.py'):continue
    if i and Path(tokens[i-1]).name not in ('python','python3','python3.11','python3.12','python3.13'):continue
    prefix=tokens[:max(0,i-1)]
    if any('=' not in v and v not in ('env','nohup') for v in prefix):continue
    if '--help' in tokens or '-h' in tokens:continue
    if any(v in ('ask','wait') for v in tokens[i+1:i+4]):valid.append(shlex.join(tokens))
 return valid

class ConsultationStore:
 def __init__(self,root=DEFAULT_JOBS):self.root=Path(root);self.cache={};self.discovered={};self.bind_cache={}
 def records(self):
  result={}
  files=list(self.root.glob('*.json'))+list((self.root/'events').glob('*.jsonl'))+list((self.root/'snapshots').glob('*.json'))
  ids={p.stem for p in files if CONV.fullmatch(p.stem)}
  for conv in sorted(ids):
   paths=[self.root/(conv+'.json'),self.root/'events'/(conv+'.jsonl'),self.root/'snapshots'/(conv+'.json')]
   sig=tuple((p.stat().st_mtime_ns,p.stat().st_size) if p.exists() else None for p in paths)
   old=self.cache.get(conv)
   if old and old[0]==sig:r=old[1];r['status']=consultation_state(r['events']);result[conv]=r;continue
   legacy=read(paths[0]);events=[]
   try:
    for line in paths[1].read_text().splitlines():
     try:e=json.loads(line)
     except ValueError:continue
     if e.get('conv')==conv:events.append(e)
   except OSError:pass
   if not events and legacy:
    parent=legacy.get('session');parent=parent if not parent or parent.startswith(('codex-','claude-')) else 'claude-'+parent
    if legacy.get('sent'):events.append({'kind':'submitted','time':stamp(legacy['sent']),'parent':parent,'out':legacy.get('out')})
    events.append({'kind':'retrieved' if legacy.get('status') in ('complete','no_marker') else 'legacy','time':stamp(legacy.get('updated')),'done':legacy.get('status') in ('complete','no_marker'),'parent':parent,'out':legacy.get('out')})
   snap=read(paths[2]);events.sort(key=lambda e:e.get('time',0))
   parents=list(dict.fromkeys(e['parent'] for e in events if e.get('parent')))
   turns=[];inputs={};outputs={};resources=[];account={};model='';title='ChatGPT consultation';cwd='';calls=[]
   for e in events:
    at=e.get('time',0);kind=e.get('kind');cwd=e.get('project_root') or e.get('cwd') or cwd;title=e.get('title') or title;model=e.get('model') or model;account=e.get('account') or account
    if kind=='submitted':
     calls.append(e);text=e.get('prompt');call=e.get('call') or str(at)
     if text is not None:
      t={'role':'user','text':text,'ts':datetime.fromtimestamp(at).astimezone().isoformat(),'id':call};turns.append(t);inputs[call]=t
     for path in e.get('attachments',[]):resources.append({'path':path,'operation':'attachment','time':at,'source':str(paths[1]),'tool':'ChatGPT upload'})
    if e.get('user_text') is not None:
     uid=e.get('user_id') or e.get('call') or str(at)
     match=inputs.get(uid) or next((t for t in reversed(turns) if t['role']=='user' and t['text']==e['user_text']),None)
     if not match:
      match={'role':'user','text':e['user_text'],'ts':datetime.fromtimestamp(stamp(e.get('user_time')) or at).astimezone().isoformat(),'id':uid};turns.append(match)
     match['id']=uid;inputs[uid]=match
    if e.get('text'):
     key=e.get('user_id') or e.get('call') or 'legacy';t=outputs.get(key)
     if t is None:
      t={'role':'assistant','text':e['text'],'ts':datetime.fromtimestamp(at).astimezone().isoformat(),'id':'reply-'+key,'reply_user_id':e.get('user_id'),'provider_time':e.get('reply_time'),'versions':[{'time':at,'text':e['text']}]};outputs[key]=t;turns.append(t)
     else:
      if t['text']!=e['text']:t['versions'].append({'time':at,'text':e['text']})
      t.update(text=e['text'])
    if kind=='retrieved' and e.get('out'):
     path=e['out'];resources.append({'path':path,'operation':'write','time':at,'source':str(paths[0] if not paths[1].exists() else paths[1]),'tool':'ChatGPT reply saved'})
     if not turns:
      try:body=Path(path).read_text() if Path(path).stat().st_size<2_000_000 else ''
      except OSError:body=''
      if body:turns.append({'role':'assistant','text':body,'ts':datetime.fromtimestamp(at).astimezone().isoformat(),'id':'legacy-output'})
   if snap.get('ok'):
    merged=[dict(t) for t in snap.get('turns',[])];last_user=None
    for t in merged:
     if t['role']=='user':last_user=t.get('id')
     else:
      t.setdefault('reply_user_id',last_user);t.setdefault('versions',[{'time':snap.get('captured') or snap.get('observed',0),'text':t['text']}])
    for t in turns:
     if not t.get('versions') and stamp(t.get('ts'))<=snap.get('captured',0):continue
     same=[i for i,v in enumerate(merged) if (t.get('id') and v.get('id')==t['id']) or (t['role']=='assistant' and t.get('reply_user_id') and v['role']=='assistant' and v.get('reply_user_id')==t['reply_user_id'])]
     if same and t.get('versions'):
      history=sorted(t['versions']+[v for i in same for v in merged[i].get('versions',[])],key=lambda v:v['time']);versions=[]
      for version in history:
       if not versions or versions[-1]['text']!=version['text']:versions.append(version)
      merged[same[0]]={**merged[same[0]],'text':versions[-1]['text'],'versions':versions}
      for i in reversed(same[1:]):merged.pop(i)
     elif same:
      if not any(merged[i]['text']==t['text'] for i in same):
       merged[same[0]]=t
       for i in reversed(same[1:]):merged.pop(i)
     else:merged.append(t)
    turns=merged
    if not any(e.get('account') and e['time']>snap.get('captured',0) for e in events):account=snap.get('account') or account
    if not any(e.get('model') and e['time']>snap.get('captured',0) for e in events):model=snap.get('model') or model
    title=snap.get('title') or title
   # A failed optional refresh does not erase final receipts, but does invalidate active status.
   state_events=events
   if snap.get('observed') and (not events or snap['observed']>events[-1]['time']):
    state_events=events+[{'kind':'error' if snap.get('refresh_failed') else 'reply' if snap.get('done') else 'progress' if snap.get('ok') else 'error','time':snap['observed'],'done':snap.get('done'),'turn_found':bool(snap.get('turns'))}]
   status=consultation_state(state_events)
   r={'conv':conv,'id':web_id(conv),'url':'https://chatgpt.com/c/'+conv,'parents':parents,'calls':calls,'events':state_events,'turns':turns,'resources':resources,'account':account,'model':model,'title':title,'cwd':cwd,'paths':[str(p) for p in paths if p.exists()],'first':min([e['time'] for e in events if e.get('time')]+[stamp(t.get('ts')) for t in turns if stamp(t.get('ts'))],default=0),'status':status,'legacy':legacy,'sync_error':snap.get('error')}
   self.cache[conv]=(sig,r);result[conv]=r
  for conv,r in self.discovered.items():
   if conv not in result:result[conv]=r
   else:
    for parent in r['parents']:
     if parent not in result[conv]['parents']:result[conv]['parents'].append(parent)
    for call in r['calls']:
     if not any(e.get('source')==call.get('source') and e.get('parent')==call.get('parent') for e in result[conv]['calls']):result[conv]['calls'].append(call)
  return result
 def bind(self,records,key,tools):
  """Only executed driver calls establish lineage. Mentioned URLs remain references."""
  signature=tuple(sorted((conv,r.get('legacy',{}).get('out') or '') for conv,r in records.items()));cached=self.bind_cache.get(key)
  if cached and cached[0] is tools and cached[1]==signature:return
  self.bind_cache[key]=(tools,signature)
  if len(self.bind_cache)>100:self.bind_cache.pop(next(iter(self.bind_cache)))
  for t in tools:
   commands=launch_commands(t.get('input',''))
   if not commands:continue
   if t.get('failed'):continue
   found=set();outs=[]
   # Parse returned metadata, not arbitrary links in prompts or prose.
   def returned(value,depth=0):
    if depth>5:return
    if isinstance(value,dict):
     url=value.get('conversation','')
     if isinstance(url,str) and re.fullmatch(r'https://chatgpt\.com/(?:g/[^/]+/)?c/'+CONV.pattern,url):found.add(url.rsplit('/',1)[-1])
     for v in value.values():returned(v,depth+1)
    elif isinstance(value,list):
     for v in value:returned(v,depth+1)
    elif isinstance(value,str):
     decoder=json.JSONDecoder()
     for m in re.finditer(r'[\[{]',value):
      try:v,_=decoder.raw_decode(value[m.start():])
      except ValueError:continue
      returned(v,depth+1)
   returned(t.get('result',''))
   for command in commands:
    tokens=shlex.split(command)
    for i,token in enumerate(tokens[:-1]):
     if token=='--conversation':
      conv=tokens[i+1].rsplit('/',1)[-1]
      if CONV.fullmatch(conv):found.add(conv)
     elif token=='--out':outs.append(str(Path(tokens[i+1]).expanduser()))
   for conv in found:
    if conv not in records:
     at=t.get('time',0);records[conv]={'conv':conv,'id':web_id(conv),'url':'https://chatgpt.com/c/'+conv,'parents':[],'calls':[],'events':[],'turns':[],'resources':[],'account':{},'model':'','title':'ChatGPT consultation','cwd':'','paths':[t.get('source','').rsplit(':',1)[0]],'first':at,'status':consultation_state([]),'legacy':{}}
     self.discovered[conv]=records[conv]
   for conv,r in list(records.items()):
    out=r.get('legacy',{}).get('out')
    if conv not in found and not (out and out in outs):continue
    if key not in r['parents']:r['parents'].append(key)
    if not any(e.get('parent')==key and e.get('source')==t.get('source') for e in r['calls']):r['calls'].append({'kind':'submitted','parent':key,'time':t.get('time',0),'source':t.get('source'),'evidence':'Recorded ChatGPT driver invocation'})
    self.discovered[conv]=r
 def node(self,r,parent=None,root=None):
  return {'id':r['id'],'key':r['id'],'kind':'child','provider':'chatgpt','label':r['title'],'parent':parent,'root':root or parent,'parents':r['parents'],'url':r['url'],'first':r['first'],'last':max((e['time'] for e in r['events']),default=r['first']),'status':r['status'],'summary':'Agent-called ChatGPT consultation. '+str(len(r['calls']))+' recorded requests.','latest_prompt':next((t['text'][-1600:] for t in reversed(r['turns']) if t['role']=='user'),''),'latest_output':next((t['text'][-2000:] for t in reversed(r['turns']) if t['role']=='assistant'),''),'account':self.account(r),'paths':r['paths'],'model':r['model']}
 def account(self,r):
  a=r['account'];return {'id':a.get('id'),'label':a.get('name') or 'ChatGPT account','email':a.get('email'),'verified':bool(a.get('id') or a.get('email')),'evidence':'Observed signed-in browser identity; historical workspace not reconstructed' if a else 'Account identity was not captured in this job receipt'}
 def session(self,key):
  r=self.records().get(key.removeprefix('chatgpt-'))
  if not r:raise KeyError('Consultation unavailable')
  return {'key':key,'turns':r['turns'],'tools':[{'time':e['time'],'name':'ChatGPT '+e['kind'],'input':json.dumps({k:v for k,v in e.items() if k not in ('text','account')},ensure_ascii=False),'result':e.get('text',''),'result_time':e['time'] if e.get('text') else 0} for e in r['events']],'resources':r['resources'],'paths':r['paths'],'account':self.account(r),'model':r['model'],'cwd':r['cwd'],'status':r['status'],'descendants':[],'url':r['url'],'attachments':[a for t in r['turns'] for a in t.get('attachments',[])],'notice':'Only recorded calls and the last targeted browser capture are available. Missing earlier inputs are not reconstructed.','run_output':'','missing':[]}
