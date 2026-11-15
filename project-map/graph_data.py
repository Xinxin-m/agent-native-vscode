"""Read-only, deterministic project/session graph over Library's local records."""
from pathlib import Path
from datetime import datetime, timezone
import json,re,hashlib,time,threading,urllib.request,urllib.parse,shlex,os,sys

HERE=Path(__file__).resolve().parent
LIBRARY=Path(os.environ.get('LIBRARY_ROOT',str(HERE.parent)))
sys.path.insert(0,str(LIBRARY))
from automation import automation_kind
from consultations import ConsultationStore, web_id, consultation_state, launch_commands
from project_graph import FolderDiscovery, within
from activity_filter import select_activity

BASE=os.environ.get('LIBRARY_URL','http://127.0.0.1:8744')

def timestamp(value):
 if isinstance(value,(int,float)):return float(value)
 try:return datetime.fromisoformat(str(value).replace('Z','+00:00')).timestamp()
 except (ValueError,TypeError):return 0

def read_json(path,default=None):
 try:return json.loads(Path(path).read_text())
 except (OSError,ValueError):return default if default is not None else {}

def ident(value):return hashlib.sha256(value.encode()).hexdigest()[:20]

def session_kind(meta,native):
 if meta.get('is_child') or meta.get('parent_key'):return 'child'
 source=native.get('source','')
 if isinstance(source,dict) or native.get('thread_source') not in (None,'user'):return 'child'
 if source=='exec' or native.get('originator')=='codex_exec' or automation_kind(meta):return 'run'
 return 'principal'

def resource_group(path,operation):
 name=Path(path).name.lower();ext=Path(path).suffix.lower()
 if name=='skill.md' or '/skills/' in path:return 'Skills'
 if name in ('agents.md','claude.md','project_rules.md','memory.md') or '/memory/' in path:return 'Rules'
 if operation in ('write','edit'):
  if ext in ('.png','.jpg','.jpeg','.svg','.webp','.pdf'):return 'Figures & PDFs'
  if ext in ('.csv','.tsv','.json','.parquet','.feather','.xlsx','.pkl','.h5'):return 'Datasets'
  if ext in ('.py','.js','.ts','.tsx','.sh','.r','.ipynb','.sql'):return 'Code & notebooks'
  return 'Documents'
 return 'Source material'

def paths_from_call(name,args,cwd):
 args=args if isinstance(args,dict) else {};low=name.lower();out=[]
 op='edit' if 'edit' in low or 'patch' in low else 'write' if 'write' in low else 'read' if 'read' in low or 'view_image' in low else 'reference'
 for key in ('file_path','path','filename','notebook_path'):
  val=args.get(key)
  if isinstance(val,str) and val and not val.startswith(('http:','https:')):
   out.append((str(Path(cwd,val).expanduser()) if not val.startswith('/') else val,op))
 command=args.get('command') or args.get('cmd') or args.get('input') or ''
 if not isinstance(command,str):return out
 for action,path in re.findall(r'^\*\*\* (Add|Update) File: (.+)$',command,re.M):out.append((str(Path(cwd,path)) if not path.startswith('/') else path,'write' if action=='Add' else 'edit'))
 # Shell arguments are references unless an unambiguous read command names the file.
 try:tokens=shlex.split(command)
 except ValueError:tokens=[]
 reader=tokens and Path(tokens[0]).name in ('cat','head','tail','sed','less','wc') and not any(x in tokens for x in ('>','>>','&&',';','|'))
 for token in tokens:
  if '\n' not in token and len(token)<600 and token.startswith(('/','~/')) and Path(token).suffix:
   out.append((str(Path(token).expanduser()),'read' if reader else 'reference'))
 # Nested functions.exec carries source text, not a JSON input object.
 for path in re.findall(r'''["']((?:/Users/|/sailhome/|/private/|/tmp/|~/)[^"'\n]{1,500}\.[a-zA-Z0-9]{1,10})["']''',command):
  if not any(path==p for p,_ in out):out.append((str(Path(path).expanduser()),'reference'))
 return list(dict.fromkeys(out))

def scan_native(paths,cwd):
 calls={};results={};events=[];native={};resources=[];missing=[];seen=set()
 for filename in dict.fromkeys(paths):
  try:
   with open(filename) as f:
    for lineno,line in enumerate(f,1):
     try:r=json.loads(line)
     except (ValueError,UnicodeError):continue
     ts=timestamp(r.get('timestamp'));p=r.get('payload') or {};msg=r.get('message') or {}
     if r.get('type')=='session_meta':native.update({k:p[k] for k in ('source','originator','thread_source','creator_account_id','creator_user_id') if k in p})
     if r.get('type')=='event_msg':
      kind=p.get('type','')
      mapped={'task_started':'start','task_complete':'complete','turn_aborted':'interrupted','task_failed':'failed'}.get(kind)
      if mapped:events.append({'time':ts,'kind':mapped,'label':mapped})
     blocks=msg.get('content',[]) if isinstance(msg.get('content'),list) else []
     if r.get('type')=='response_item':blocks=[p]
     for b in blocks:
      kind=b.get('type');cid=b.get('call_id') or b.get('tool_use_id') or b.get('id')
      if kind in ('function_call_output','custom_tool_call_output','tool_result'):
       output=b.get('output',b.get('content',''));text=output if isinstance(output,str) else json.dumps(output)
       failed=bool(b.get('is_error')) or bool(re.search(r'Process exited with code [1-9]|"exit_code":\s*[1-9]',text))
       results[cid]={'time':ts,'failed':failed,'text':text[:20000]};continue
      if kind not in ('function_call','custom_tool_call','tool_use'):continue
      cid=cid or ident(line+str(lineno))
      if cid in seen:continue
      seen.add(cid);name=b.get('name','Tool');a=b.get('input',b.get('arguments',{}));raw=a if isinstance(a,str) else json.dumps(a)
      if isinstance(a,str):
       try:a=json.loads(a)
       except ValueError:a={'input':a}
      if not isinstance(a,dict):a={}
      event={'time':ts,'kind':'tool','label':name,'call_id':cid,'source':f'{filename}:{lineno}'}
      events.append(event);calls[cid]={'event':event,'name':name,'args':a,'input':raw}
  except OSError:missing.append(filename)
 for cid,c in calls.items():
  result=results.get(cid);ev=c['event'];ev['finished']=result['time'] if result else 0;ev['failed']=bool(result and result['failed'])
  for path,op in paths_from_call(c['name'],c['args'],cwd):
   if any(part in path for part in ('auth.json','.credentials','/account-backups/')):continue
   # Successful native Read/Edit/Write is direct evidence; absent receipts remain references.
   actual=op if result and not result['failed'] else 'reference'
   resources.append({'path':os.path.normpath(path),'operation':actual,'time':result['time'] if result else ev['time'],'source':ev['source'],'tool':c['name'],'requested':op})
 return {'native':native,'events':sorted(events,key=lambda e:e['time']),'resources':resources,'tools':[{'time':c['event']['time'],'name':c['name'],'input':c['input'],'source':c['event']['source'],'result':results.get(cid,{}).get('text',''),'result_time':results.get(cid,{}).get('time',0),'failed':results.get(cid,{}).get('failed',False)} for cid,c in calls.items()],'missing':missing}

def activity_state(meta,events,processes,now=None,run=None):
 now=now or time.time();key=meta.get('key');terminal={'completed':'complete','failed':'failed','interrupted':'interrupted'}
 if run:
  state=run.get('status','unknown')
  if state in terminal and timestamp(run.get('updated_at'))>=max((e['time'] for e in events),default=0):return {'state':terminal[state],'evidence':'Library broker','at':timestamp(run.get('updated_at'))}
  if run.get('_verified'):
   return {'state':'waiting' if run.get('pending') else 'working','evidence':'Verified Library process','at':now}
 for p in processes:
  if key and p.get('chat_key')==key and p.get('alive') and not p.get('process_only') and 0<=now-float(p.get('observed_at') or 0)<150:
   state=p.get('status','unknown');state='working' if state in ('busy','working','running') else 'waiting' if state=='waiting' else 'idle'
   return {'state':state,'evidence':'Attributed native process','at':float(p['observed_at'])}
 lifecycle=[e for e in events if e['kind'] in ('start','complete','failed','interrupted')]
 if lifecycle and lifecycle[-1]['kind']!='start':return {'state':lifecycle[-1]['kind'],'evidence':'Native lifecycle event','at':lifecycle[-1]['time']}
 if events and 0<=now-events[-1]['time']<20 and events[-1]['kind']=='tool':return {'state':'working','evidence':'Recent native tool event; process ownership unavailable','at':events[-1]['time']}
 if meta.get('source')=='claude' and meta.get('status')=='complete':return {'state':'complete','evidence':'Persisted final response','at':timestamp(meta.get('ts_last'))}
 return {'state':'unknown','evidence':'No fresh attributable activity evidence','at':timestamp(meta.get('ts_last'))}

def cut_graph(graph,at):
 nodes=[n for n in graph['nodes'] if n.get('first',0)<=at];ids={n['id'] for n in nodes}
 return {**graph,'nodes':nodes,'edges':[e for e in graph['edges'] if e.get('first',0)<=at and e['from'] in ids and e['to'] in ids],'events':[e for e in graph['events'] if e['time']<=at]}

class Catalog:
 def __init__(self,library=LIBRARY):
  self.library=Path(library);self.data=self.library/'data';self.lock=threading.RLock();self.cache={};self.raw_cache={};self.allowed={};self.last_live=0;self.live={'sessions':[]};self.last_accounts=0;self.accounts=[];self.consultation_candidates={};self.consultations=ConsultationStore();self.folder_discovery=FolderDiscovery(HERE/'output/folder-discovery.json')
 def json(self,path,default=None):
  path=Path(path)
  try:sig=(path.stat().st_mtime_ns,path.stat().st_size)
  except OSError:return default if default is not None else {}
  key=str(path);old=self.cache.get(key)
  if old and old[0]==sig:return old[1]
  d=read_json(path,default);self.cache[key]=(sig,d);return d
 def index(self):return self.json(self.data/'index.json')
 def views(self):return self.json(HERE/'views.json',[])
 def projects(self):
  ix=self.index();pj=self.json(self.data/'projects.json');en=self.json(self.data/'enrich.json');counts={}
  for k,m in ix.items():
   if m.get('is_child') or automation_kind(m):continue
   pid=pj.get('assignments',{}).get(k,'');counts[pid]=counts.get(pid,0)+1
  return [{'id':v['id'],'name':v['name'],'count':len(self.members(v['id'])),'view':True} for v in self.views()]+[{**p,'count':counts.get(p['id'],0)} for p in pj.get('projects',[]) if not p.get('automation')]
 def members(self,project):
  ix=self.index();pj=self.json(self.data/'projects.json');en=self.json(self.data/'enrich.json');view=next((v for v in self.views() if v['id']==project),None)
  selected=set()
  if view and view.get('discover_references'):selected.update(self.folder_discovery.matches(ix,self.data/'chats',view.get('folders',[])))
  for k,m in ix.items():
   if automation_kind(m):continue
   if view:
    match=k in view.get('sessions',[]) or any(within(m.get('cwd'),p) for p in view.get('folders',[]) if m.get('cwd'))
    if view.get('library_project'):
     text=(en.get(k,{}).get('title','')+' '+en.get(k,{}).get('description',''))
     match=match or (pj.get('assignments',{}).get(k)==view['library_project'] and (not view.get('project_title_pattern') or re.search(view['project_title_pattern'],text,re.I)))
    title=en.get(k,{}).get('title') or m.get('title_seed','')
    if view.get('title_pattern') and re.search(view['title_pattern'],title,re.I) and not re.search(view.get('exclude_titles','(?!)'),title,re.I):match=True
   else:match=pj.get('assignments',{}).get(k)==project
   if match:selected.add(k)
  for k in list(selected):
   ancestor=ix[k].get('root_key') or ix[k].get('parent_key')
   if ancestor in ix:selected.add(ancestor)
  for k,m in ix.items():
   if m.get('root_key') in selected or m.get('parent_key') in selected:selected.add(k)
  return selected
 def native_head(self,m):
  path=m.get('file','')
  try:
   stat=Path(path).stat();key='head:'+path;old=self.cache.get(key)
   if old:return old[1]
   with open(path) as f:
    r=json.loads(f.readline());p=r.get('payload') or {}
   result={k:p[k] for k in ('source','originator','thread_source','creator_account_id') if k in p};self.cache[key]=(None,result);return result
  except (OSError,ValueError):return {}
 def scanned(self,m):
  paths=m.get('files_all') or [m.get('file')];paths=[p for p in paths if p];sig=[]
  for p in paths:
   try:st=Path(p).stat();sig.append((p,st.st_mtime_ns,st.st_size))
   except OSError:sig.append((p,0,0))
  key=m.get('key') or m.get('id');old=self.raw_cache.get(key)
  if old and old[0]==sig:return old[1]
  result=scan_native(paths,m.get('cwd',''));self.raw_cache[key]=(sig,result)
  # Bound cached transcript evidence, independent from the indexed archive.
  if len(self.raw_cache)>100:self.raw_cache.pop(next(iter(self.raw_cache)))
  return result
 def fetch_live(self):
  if time.monotonic()-self.last_live<2:return
  try:
   with urllib.request.urlopen(BASE+'/api/live',timeout=3) as r:self.live=json.load(r)
   self.live['reachable']=True
  except Exception:self.live={**self.live,'reachable':False,'sessions':[]}
  self.last_live=time.monotonic()
 def account(self,m,native,run):
  if time.monotonic()-self.last_accounts>60:
   try:
    with urllib.request.urlopen(BASE+'/api/accounts',timeout=3) as r:self.accounts=json.load(r).get('accounts',[])
   except Exception:pass
   self.last_accounts=time.monotonic()
  aid=(run or {}).get('account_id') or m.get('account_id');reason='Explicit Library session binding';matches=[]
  if aid:matches=[a for a in self.accounts if a['id']==aid]
  if not matches and m.get('source')=='codex' and native.get('creator_account_id'):
   for a in self.accounts:
    if a.get('tool')!='codex':continue
    auth=self.json(Path(a['config_dir']).expanduser()/'auth.json');creator=(auth.get('tokens') or {}).get('account_id')
    if creator==native['creator_account_id']:matches.append(a)
   reason='Native creator account matches authenticated profile'
  if len(matches)==1:
   a=matches[0];return {k:a.get(k,'') for k in ('id','label','email','plan')}|{'verified':True,'evidence':reason}
  profiles={p for l in m.get('source_locations',[]) for p in l.get('profile_ids',[])}
  if m.get('source')=='claude' and len(profiles)==1:
   a=next((a for a in self.accounts if a['id'] in profiles),None)
   if a:return {k:a.get(k,'') for k in ('id','label','email','plan')}|{'verified':False,'evidence':'Current owning profile; historical creator identity is not stored in this transcript'}
  return {'label':'Identity unavailable','verified':False,'evidence':'No unique creator-to-profile match; shared transcript folders do not establish account ownership'}
 def runs(self):
  latest={}
  for p in (self.data/'execution/runs').glob('*/run.json'):
   r=self.json(p);key=r.get('key') or r.get('conversation_key')
   if key and r.get('created_at','')>latest.get(key,{}).get('created_at',''):latest[key]=r
  # Verify only nonterminal identities. Importing broker does not start its instance.
  import sys
  if str(self.library) not in sys.path:sys.path.insert(0,str(self.library))
  try:
   from broker import observe_process
   for k,r in list(latest.items()):
    if r.get('status') not in ('completed','failed','interrupted'):latest[k]={**r,'_verified':observe_process(r).get('state')=='same'}
  except (ImportError,OSError):pass
  return latest
 def bind_consultations(self,records,keys,index):
  # Cheap signature-cached candidate pass precedes filtering. Full native parsing
  # is needed only for transcripts mentioning an actual supported driver name;
  # bind() then validates invocation syntax and rejects examples/heredocs.
  for key in keys:
   meta=index[key];paths=meta.get('files_all') or [meta.get('file')];signature=[]
   for filename in filter(None,paths):
    try:st=Path(filename).stat();signature.append((filename,st.st_mtime_ns,st.st_size))
    except OSError:continue
   cached=self.consultation_candidates.get(key)
   if cached and cached[0]==signature:
    if cached[1]:self.consultations.bind(records,key,cached[2])
    continue
   else:
    candidate=False
    for filename,_,_ in signature:
     try:
      with open(filename,'rb') as stream:
       carry=b''
       while chunk:=stream.read(1024*1024):
        text=carry+chunk
        if b'cgpt.py' in text or b'pw_gpt.py' in text:candidate=True;break
        carry=text[-32:]
     except OSError:continue
     if candidate:break
    tools=[t for t in self.scanned(meta)['tools'] if launch_commands(t.get('input',''))] if candidate else []
    self.consultation_candidates[key]=(signature,bool(tools),tools)
   if tools:self.consultations.bind(records,key,tools)
 def graph(self,project,query='',offset=0,limit=16,period='all'):
  with self.lock:
   started=time.monotonic();ix=self.index();en=self.json(self.data/'enrich.json');keys=self.members(project);runs=self.runs();self.fetch_live();now=time.time();principals=[];children=[];aux=[]
   for k in keys:
    m=ix[k];kind=session_kind(m,self.native_head(m));label=en.get(k,{}).get('title') or m.get('agent_label') or m.get('title_seed') or 'Untitled session'
    if kind=='child':children.append(k)
    elif kind=='run':aux.append(k)
    else:principals.append(k)
   if query:
    matched={k for k in keys if query.casefold() in ((en.get(k,{}).get('title') or ix[k].get('agent_label') or ix[k].get('title_seed',''))+' '+en.get(k,{}).get('description','')+' '+ix[k].get('first_prompt','')).casefold()}
    ancestors={ix[k].get('root_key') or ix[k].get('parent_key') for k in matched}
    principals=[k for k in principals if k in matched or k in ancestors]
    aux=[k for k in aux if k in matched or k in ancestors]
   working={s.get('chat_key') for s in self.live.get('sessions',[]) if s.get('alive') and not s.get('process_only') and s.get('status') in ('working','busy','running','waiting') and 0<=now-float(s.get('observed_at') or 0)<150}
   working|={k for k,r in runs.items() if r.get('_verified')}
   consultations=self.consultations.records()
   self.bind_consultations(consultations,keys,ix)
   eligible=select_activity(ix,keys,working,consultations,period,now)
   principals=[k for k in principals if k in eligible];aux=[k for k in aux if k in eligible]
   active_family=select_activity(ix,keys,working,consultations,'live',now)
   principals.sort(key=lambda k:(k in active_family,ix[k].get('ts_last','')),reverse=True)
   total=len(principals);selected=principals[offset:offset+limit];selected_set=set(selected)
   # Always include explicitly linked descendants of the visible principals.
   family=[k for k in children if ix[k].get('root_key') in selected_set or ix[k].get('parent_key') in selected_set]
   nodes=[];events=[];resources={};edges=[]
   for k in selected+family:
    self.consultations.bind(consultations,k,self.scanned(ix[k])['tools'])
   for k in selected+family:
    m=ix[k];chat=self.json(self.data/'chats'/f'{k}.json');sc=self.scanned(m);turns=chat.get('turns',[]);kind='child' if k in family else 'principal';label=en.get(k,{}).get('title') or m.get('agent_label') or m.get('title_seed') or 'Untitled session'
    n={'id':k,'key':k,'kind':kind,'label':label,'provider':m.get('source'),'first':timestamp(m.get('ts_first')),'last':timestamp(m.get('ts_last')),'parent':m.get('parent_key'),'root':m.get('root_key') or k,'summary':en.get(k,{}).get('description') or '', 'summary_source':'Existing Library description' if en.get(k,{}).get('description') else 'Recorded prompt','latest_prompt':next((t['text'] for t in reversed(turns) if t['role']=='user'),''),'latest_output':next((t['text'] for t in reversed(turns) if t['role']=='assistant'),''),'account':self.account(m,sc['native'],runs.get(k)),'model':m.get('model_main',''),'status':activity_state(m,sc['events'],self.live.get('sessions',[]),now,runs.get(k)),'cwd':m.get('cwd'),'paths':m.get('files_all') or [m.get('file')], 'turn_count':len(turns)}
    # Keep initial graph summaries compact; full content is fetched on selection.
    n['latest_prompt']=n['latest_prompt'][-1600:];n['latest_output']=n['latest_output'][-2000:];nodes.append(n)
    for t in turns:
     ts=timestamp(t.get('ts'))
     if ts:events.append({'time':ts,'node':k,'kind':t['role'],'label':t['text'][:220]})
    events.extend({**e,'node':k} for e in sc['events'])
    for r in sc['resources']:
     rid='file-'+ident(r['path']);group=resource_group(r['path'],r['operation']);direction='input' if group in ('Rules','Skills','Source material') else 'output'
     rec=resources.setdefault(rid,{'id':rid,'path':r['path'],'name':Path(r['path']).name,'first':r['time'],'links':[],'group':group,'direction':direction})
     if direction=='output':rec.update(group=group,direction=direction)
     rec['first']=min(rec['first'],r['time']);rec['links'].append({'session':k,**r});self.allowed[rid]=r['path']
    if n['parent'] and n['parent'] in selected_set|set(family):edges.append({'from':n['parent'],'to':k,'kind':'delegation','first':n['first']})
   # Linked web conversations are a distinct secondary shape; references are not delegation.
   linked={}
   for k in selected:
    chat=self.json(self.data/'chats'/f'{k}.json')
    for t in chat.get('turns',[]):
     for url in re.findall(r'https://chatgpt\.com/(?:g/[^\s)<>]+/)?c/[a-zA-Z0-9-]+',t.get('text','')):
      conv=url.rsplit('/',1)[-1];nid=web_id(conv);ts=timestamp(t.get('ts'))
      if conv in consultations and any(p in selected_set or p in family for p in consultations[conv]['parents']):continue
      if nid not in linked:linked[nid]={'id':nid,'kind':'linked','provider':'chatgpt','label':'ChatGPT review','first':ts,'parent':k,'root':k,'url':url,'summary':'Linked ChatGPT conversation. Open the original for complete history; live web activity and account identity are not imported.','status':{'state':'unknown','evidence':'No live web-app adapter'},'paths':[]}
      else:linked[nid]['first']=min(linked[nid]['first'],ts)
      if conv in consultations:
       refs=list(dict.fromkeys(linked[nid].get('referenced_by',[])+[k]));owner=refs[0]
       linked[nid]={**self.consultations.node(consultations[conv],owner,owner),'kind':'linked','referenced_by':refs,'summary':'Recorded ChatGPT consultation, referenced here. Calling session is not established by this link.'}
      edge={'from':k,'to':nid,'kind':'linked','first':ts}
      if not any(e['from']==k and e['to']==nid for e in edges):edges.append(edge)
   nodes.extend(linked.values())
   visible=set(selected+family)
   for conv,r in consultations.items():
    parents=[p for p in r['parents'] if p in visible]
    if not parents and r['id'] not in linked:continue
    if parents:
     parent=parents[0];root=ix[parent].get('root_key') or parent;n=self.consultations.node(r,parent,root);nodes.append(n)
    else:n=linked[r['id']]
    for p in parents:
     calls=[e for e in r['calls'] if e.get('parent')==p];first=min((e.get('time',0) for e in calls),default=r['first'])
     edges.append({'from':p,'to':n['id'],'kind':'delegation','first':first,'active':r['status']['state']=='working'})
    for e in r['events']:
     kind={'submitted':'submitted','progress':'start','reply':'complete','retrieved':'retrieved','error':'unavailable'}.get(e['kind'],e['kind'])
     events.append({'time':e['time'],'node':n['id'],'kind':kind,'label':(e.get('prompt') or e.get('text','') or e['kind'])[:220],'consultation_state':consultation_state([e],e['time'])['state']})
    for t in r['turns']:
     for version in t.get('versions') or [{'time':timestamp(t['ts']),'text':t['text']}]:events.append({'time':version['time'],'node':n['id'],'kind':t['role'],'label':version['text'][:220]})
    for rr in r['resources']:
     rid='file-'+ident(rr['path']);group=resource_group(rr['path'],rr['operation']);direction='output' if rr['operation']=='write' else 'input';rec=resources.setdefault(rid,{'id':rid,'path':rr['path'],'name':Path(rr['path']).name,'first':rr['time'],'links':[],'group':group,'direction':direction});rec['links'].append({'session':n['id'],**rr});self.allowed[rid]=rr['path']
    for e in r['events']:
     if e['kind']=='retrieved':
      recipient=e.get('parent')
      if recipient in parents:edges.append({'from':n['id'],'to':recipient,'kind':'returned','first':e['time'],'active':False})
   # Preserve explicit spawn records even if no child transcript was indexed.
   existing={n['id'] for n in nodes}
   for k in selected:
    for c in ix[k].get('spawned_children',[]):
     if any(c.get('agent_id') in str(n.get('id')) for n in nodes):continue
     cid='child-'+k+'-'+c['agent_id'];nodes.append({'id':cid,'kind':'child','label':c.get('label') or 'Delegated task','provider':ix[k]['source'],'first':timestamp(ix[k].get('ts_first')),'first_unknown':True,'parent':k,'root':k,'status':{'state':'unknown','evidence':'Delegation metadata only; exact creation time unavailable'},'summary':'Delegation is recorded; child transcript not indexed.','paths':[]});edges.append({'from':k,'to':cid,'kind':'delegation','first':0,'first_unknown':True})
   other=[{'id':k,'label':en.get(k,{}).get('title') or ix[k].get('title_seed') or 'Command run','first':timestamp(ix[k].get('ts_first')),'provider':ix[k].get('source')} for k in sorted(aux,key=lambda k:ix[k].get('ts_last',''),reverse=True)]
   project_view=next((v for v in self.views() if v['id']==project),{})
   for r in consultations.values():
    if not r['parents'] and r['id'] not in {n['id'] for n in nodes} and (period=='all' or r.get('status',{}).get('state') in ('working','waiting') or (period!='live' and any(now-int(period)*86400<=e.get('time',0)<=now for e in r['events']))) and any(within(rr['path'],folder) for rr in r['resources'] for folder in project_view.get('folders',[])):
     other.append(self.consultations.node(r))
   events=[e for e in events if e['time']>0];events.sort(key=lambda e:e['time']);times=[n['first'] for n in nodes if n.get('first')]+[e['time'] for e in events]
   view=next((v for v in self.views() if v['id']==project),None);project_meta=view or next((p for p in self.json(self.data/'projects.json').get('projects',[]) if p['id']==project),{'name':project})
   return {'project':{'id':project,'name':project_meta['name']},'nodes':nodes,'edges':edges,'resources':list(resources.values()),'events':events,'other_runs':other,'total':total,'offset':offset,'limit':limit,'period':period,'range':[min(times) if times else now,now],'captured':now,'library_reachable':self.live.get('reachable',False),'process_observed':self.live.get('fetched_at',0),'generation_ms':round((time.monotonic()-started)*1000),'method':'Deterministic native-event extraction; existing Library descriptions; no model calls'}
 def session(self,key):
  with self.lock:
   if key.startswith('chatgpt-'):
    detail=self.consultations.session(key)
    for r in detail['resources']:self.allowed['file-'+ident(r['path'])]=r['path']
    return detail
   ix=self.index()
   if key not in ix:raise KeyError('Session unavailable')
   m=ix[key];chat=self.json(self.data/'chats'/f'{key}.json');sc=self.scanned(m);runs=self.runs();run=runs.get(key);self.fetch_live()
   desc=[];family={key};changed=True;en=self.json(self.data/'enrich.json')
   while changed:
    changed=False
    for ck,cm in ix.items():
     if ck not in family and (cm.get('parent_key') in family or cm.get('root_key')==key):family.add(ck);changed=True
   for ck in sorted(family-{key},key=lambda x:ix[x].get('ts_first','')):
    cm=ix[ck];desc.append({'id':ck,'key':ck,'kind':'child','label':en.get(ck,{}).get('title') or cm.get('agent_label') or cm.get('title_seed') or 'Delegated task','provider':cm.get('source'),'first':timestamp(cm.get('ts_first')),'parent':cm.get('parent_key'),'paths':cm.get('files_all') or [cm.get('file')],'status':{'state':'unknown','evidence':'Open transcript for current evidence'}})
   records=self.consultations.records()
   for member in family:self.consultations.bind(records,member,self.scanned(ix[member])['tools'])
   desc.extend(self.consultations.node(r,next(p for p in r['parents'] if p in family),key) for r in records.values() if any(p in family for p in r['parents']))
   return {'descendants':desc,'key':key,'turns':chat.get('turns',[]),'tools':sc['tools'],'resources':sc['resources'],'paths':m.get('files_all') or [m.get('file')],'cleaned_path':str(self.data/'chats'/f'{key}.json'),'account':self.account(m,sc['native'],run),'model':m.get('model_main',''),'cwd':m.get('cwd'),'status':activity_state(m,sc['events'],self.live.get('sessions',[]),time.time(),run),'run_output':(run or {}).get('text',''),'run_updated':(run or {}).get('updated_at'),'missing':sc['missing']}
 def resource(self,rid):
  with self.lock:
   if rid not in self.allowed:raise KeyError('File is not in this graph')
   path=Path(self.allowed[rid]);allowed_suffix=('.md','.txt','.tex','.py','.js','.ts','.tsx','.css','.json','.csv','.tsv','.yaml','.yml','.toml','.sh','.r','.sql','.log')
   if not path.is_file():return {'path':str(path),'error':'File no longer exists locally'}
   if path.suffix.lower() not in allowed_suffix:return {'path':str(path),'error':'Binary preview unavailable; copy the file path','size':path.stat().st_size}
   if path.stat().st_size>2_000_000:return {'path':str(path),'error':'File exceeds the 2 MB preview limit; copy the file path','size':path.stat().st_size}
   return {'path':str(path),'text':path.read_text(errors='replace'),'modified':path.stat().st_mtime,'current':True}
