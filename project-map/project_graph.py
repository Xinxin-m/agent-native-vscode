#!/usr/bin/env python3
"""Register and open a local project graph without model inference or taxonomy edits."""
import argparse,fcntl,hashlib,json,os,re,subprocess,sys,time,urllib.request
from pathlib import Path
HERE=Path(__file__).resolve().parent

def within(path,folder):
 try:return Path(path).expanduser().resolve().is_relative_to(Path(folder).expanduser().resolve())
 except (OSError,ValueError):return False

def register_folder(folder,name=None,sessions=None,config=None):
 root=Path(folder).expanduser().resolve()
 if not root.is_dir():raise ValueError('Project folder does not exist: '+str(root))
 config=Path(config or HERE/'views.json');config.parent.mkdir(parents=True,exist_ok=True)
 with config.with_suffix('.lock').open('a') as lock:
  fcntl.flock(lock,fcntl.LOCK_EX)
  views=json.loads(config.read_text()) if config.exists() else []
  existing=next((v for v in views if str(root) in v.get('folders',[])),None)
  view=existing or {'id':'folder-'+hashlib.sha256(str(root).encode()).hexdigest()[:16],'name':name or root.name,'folders':[str(root)]}
  if name:view['name']=name
  if not existing:view['discover_references']=True
  view['sessions']=list(dict.fromkeys(view.get('sessions',[])+(sessions or [])))
  if not existing:views.append(view)
  temp=config.with_name(config.name+f'.{os.getpid()}.tmp');temp.write_text(json.dumps(views,indent=2)+'\n');os.replace(temp,config)
 return view

class FolderDiscovery:
 def __init__(self,cache_path):self.path=Path(cache_path);self.cache=None
 def matches(self,index,chatdir,folders):
  if self.cache is None:
   try:self.cache=json.loads(self.path.read_text())
   except (OSError,ValueError):self.cache={}
  result=set();changed=False;scope='v2\n'+'\n'.join(folders);cache=self.cache.setdefault(scope,{})
  # Path boundary prevents Project matching Project-other. No title-based model inference.
  patterns=[re.compile(re.escape(p)+r'(?=$|[/\s\x22\x27<>),:])') for p in folders]
  for key,meta in index.items():
   if any(within(meta.get('cwd',''),p) for p in folders if meta.get('cwd')):result.add(key);continue
   path=Path(chatdir)/(key+'.json')
   try:st=path.stat();sig=[st.st_mtime_ns,st.st_size]
   except OSError:continue
   old=cache.get(key)
   if old and old['sig']==sig:matched=old['match']
   else:
    try:
     doc=json.loads(path.read_text());text='\n'.join(t.get('text','').split('## My request:')[-1] for t in doc.get('turns',[]) if not t.get('text','').startswith('# AGENTS.md instructions'));matched=any(p.search(text) for p in patterns)
    except (OSError,ValueError):matched=False
    cache[key]={'sig':sig,'match':matched};changed=True
   if matched:result.add(key)
  if changed:
   self.path.parent.mkdir(parents=True,exist_ok=True);tmp=self.path.with_name(self.path.name+f'.{os.getpid()}.tmp');tmp.write_text(json.dumps(self.cache));os.replace(tmp,self.path)
  return result

def ensure_server():
 path=HERE/'output/url.txt'
 if path.exists():
  url=path.read_text().strip()
  if re.fullmatch(r'http://127\.0\.0\.1:\d+',url):
   try:
    with urllib.request.urlopen(url+'/api/projects',timeout=3) as r:
     if r.status==200:return url
   except OSError:pass
 (HERE/'output').mkdir(exist_ok=True)
 with (HERE/'output/server.log').open('a') as log:subprocess.Popen([sys.executable,'-u',str(HERE/'server.py'),'--port','53962'],cwd=HERE,stdin=subprocess.DEVNULL,stdout=log,stderr=log,start_new_session=True)
 for _ in range(50):
  time.sleep(.2)
  if path.exists():
   url=path.read_text().strip()
   try:
    with urllib.request.urlopen(url+'/api/projects',timeout=2) as r:
     if r.status==200:return url
   except OSError:pass
 raise RuntimeError('Dashboard server did not start; inspect output/server.log')

def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('folder');p.add_argument('--name');p.add_argument('--session',action='append',default=[],help='Explicit Library session key, repeatable');p.add_argument('--no-open',action='store_true');p.add_argument('--register-only',action='store_true');p.add_argument('--sync-chatgpt',action='store_true',help='Read only already linked ChatGPT conversations using the logged-in browser')
 a=p.parse_args();v=register_folder(a.folder,a.name,a.session)
 from graph_data import Catalog
 c=Catalog();members=c.members(v['id'])
 if a.sync_chatgpt:
  from sync_chatgpt import sync_project
  sync_project(c,v['id'])
 result={'project':v['id'],'name':v['name'],'folder':str(Path(a.folder).expanduser().resolve()),'matched_sessions':len(members)}
 if not a.register_only:
  result['url']=ensure_server()+'/?project='+v['id']
  if not a.no_open:subprocess.run(['open',result['url']],check=True)
 print(json.dumps(result,indent=2))
if __name__=='__main__':main()
