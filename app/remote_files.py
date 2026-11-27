"""On-demand scoped SSH file helper. No remote install, scheduler or project code."""
import base64
import json
import re
import shlex
import subprocess

import file_service as fs

BOOTSTRAP = "import sys; exec(compile(sys.stdin.read(), '<workbench-files>', 'exec'))"
# The fixed helper uses only stdlib filesystem operations. Request data is passed
# through stdin, never interpolated into a remote shell command.
HELPER = r'''
import base64, hashlib, json, mimetypes, os, stat, time, uuid

def perform(request):
    operation=request['operation']
    if operation not in ('root','list','stat','read','search','search2','save_document'):
        return {'ok':False,'code':'read_only','error':'Unsupported remote file operation','status':403}
    root=request.get('root')
    if not isinstance(root,str) or not root.startswith('/') or '\x00' in root:
        raise ValueError('An absolute remote root is required')
    # Registration resolves the selected scope exactly once. Later operations
    # walk that stored canonical path with O_NOFOLLOW, including its root.
    if operation=='root': root=os.path.realpath(root)
    path=request.get('path','')
    if not isinstance(path,str) or path.startswith('/') or '\x00' in path or '\\' in path or '..' in path.split('/'):
        raise ValueError('Path must remain within its registered root')
    components=[p for p in path.split('/') if p not in ('','.')] 
    descriptors=[]
    try:
        directory=os.open('/',os.O_RDONLY|os.O_DIRECTORY)
        descriptors.append(directory)
        directory_parts=[p for p in root.split('/') if p]
        if operation in ('root','list','search','search2'):
            directory_parts+=components
        else:
            if not components: raise ValueError('Choose a regular file')
            directory_parts+=components[:-1]
        for component in directory_parts:
            directory=os.open(component,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=directory)
            descriptors.append(directory)
        if operation=='root': return {'ok':True,'path':root}
        if operation=='search2': return native_search(directory,components,request)
        if operation=='search':
            query=request.get('query')
            if not isinstance(query,str) or not query.strip() or len(query)>200: raise ValueError('Invalid filename query')
            relocation=request.get('relocation') is True
            identity=request.get('identity') if relocation else None
            if identity is not None and (not isinstance(identity,list) or len(identity)!=4 or any(type(n) is not int for n in identity)):
                raise ValueError('Invalid file identity')
            pending=[[]]; entries=[]; scanned=0; deadline=time.monotonic()+2; truncated=False
            while pending:
                current=pending.pop()
                if len(current)>32 or scanned>=20000 or time.monotonic()>deadline or len(entries)>=100:
                    truncated=True; break
                opened=[]
                try:
                    child=directory
                    for component in current:
                        child=os.open(component,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=child)
                        opened.append(child)
                    with os.scandir(child) as iterator:
                        for item in iterator:
                            scanned+=1
                            if scanned>=20000 or time.monotonic()>deadline:
                                truncated=True; break
                            if item.is_dir(follow_symlinks=False):
                                if relocation and (item.name.startswith('.') or item.name in ('node_modules','__pycache__')):
                                    continue
                                if len(current)>=32: truncated=True
                                else: pending.append(current+[item.name])
                            elif (item.name==query if relocation else query.casefold() in item.name.casefold()) or (identity and item.inode()==identity[1]):
                                if not item.is_file(follow_symlinks=False): continue
                                info=item.stat(follow_symlinks=False)
                                verified=identity==[info.st_dev,info.st_ino,info.st_size,info.st_mtime_ns]
                                if relocation and item.name!=query and not verified: continue
                                entries.append({'name':item.name,'path':'/'.join(components+current+[item.name]),'type':'file','size':info.st_size,'mime':mimetypes.guess_type(item.name)[0] or 'application/octet-stream',**({'identity_match':True} if verified else {})})
                                if len(entries)>=100: truncated=True; break
                except OSError:
                    truncated=True
                finally:
                    for descriptor in reversed(opened): os.close(descriptor)
            return {'ok':True,'entries':entries,'next_cursor':None,'truncated':truncated}
        if operation=='list':
            entries=[]
            with os.scandir(directory) as iterator:
                for item in iterator:
                    if len(entries)>=10000: break
                    info=item.stat(follow_symlinks=False)
                    kind='directory' if stat.S_ISDIR(info.st_mode) else 'file' if stat.S_ISREG(info.st_mode) else 'symlink' if stat.S_ISLNK(info.st_mode) else 'unsupported'
                    entries.append({'name':item.name,'path':'/'.join(components+[item.name]),'type':kind,'size':info.st_size,'mime':mimetypes.guess_type(item.name)[0] or 'application/octet-stream'})
            return {'ok':True,'entries':entries,'truncated':len(entries)>=10000}
        descriptor=os.open(components[-1],os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=directory)
        descriptors.append(descriptor)
        info=os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode): raise ValueError('Only regular files may be opened')
        if operation=='read':
            offset=int(request.get('offset',0))
            length=int(request.get('length',64*1024*1024))
            if offset<0 or length<0 or length>64*1024*1024: raise ValueError('Invalid or oversized read range')
            if info.st_size-offset>64*1024*1024 and 'length' not in request:
                return {'ok':False,'code':'too_large','error':'Remote reads are limited to 64 MiB; use ranges','status':413}
            os.lseek(descriptor,offset,os.SEEK_SET)
            chunks=[]
            while length:
                chunk=os.read(descriptor,min(length,256*1024))
                if not chunk: break
                chunks.append(chunk)
                length-=len(chunk)
            return {'ok':True,'data_base64':base64.b64encode(b''.join(chunks)).decode()}
        raw=b''
        if info.st_size<=5*1024*1024:
            while len(raw)<info.st_size:
                chunk=os.read(descriptor,min(info.st_size-len(raw),256*1024))
                if not chunk: break
                raw+=chunk
        encoding=None
        try:
            if b'\0' not in raw and info.st_size<=5*1024*1024:
                encoding='utf-8-sig' if raw.startswith(b'\xef\xbb\xbf') else 'utf-8'
                raw.decode(encoding)
        except UnicodeDecodeError: encoding=None
        version=hashlib.sha256(('%s:%s:%s:%s:'%(info.st_dev,info.st_ino,info.st_mtime_ns,info.st_size)).encode()+raw).hexdigest()
        name=components[-1]
        document_editable=bool(encoding and name.lower().endswith(('.md','.markdown','.mdown','.html','.htm'))
                               and os.access(name,os.W_OK,dir_fd=directory,follow_symlinks=False)
                               and os.access('.',os.W_OK,dir_fd=directory))
        if operation=='save_document':
            if not document_editable:
                return {'ok':False,'code':'read_only','error':'This rendered document is read-only','status':403}
            if version!=request.get('base_version'):
                return {'ok':False,'code':'version_conflict','error':'File changed on disk; compare or reload before saving','status':409}
            if 'exact_base64' in request:
                output=base64.b64decode(request['exact_base64'],validate=True)
                if b'\0' in output:raise ValueError('Invalid text')
                output.decode('utf-8-sig')
            else:
                prior=raw.decode(encoding)
                text=request.get('text')
                if not isinstance(text,str) or '\0' in text: raise ValueError('Invalid text')
                if '\r\n' in prior and prior.count('\r\n')==prior.count('\n'):
                    text=text.replace('\r\n','\n').replace('\n','\r\n')
                output=text.encode(encoding)
            if len(output)>5*1024*1024:
                return {'ok':False,'code':'too_large','error':'Text exceeds the 5 MiB save limit','status':413}
            if output==raw:return {'ok':True}
            tmp='.workbench-'+uuid.uuid4().hex
            fd=os.open(tmp,os.O_WRONLY|os.O_CREAT|os.O_EXCL,stat.S_IMODE(info.st_mode),dir_fd=directory)
            try:
                with os.fdopen(fd,'wb') as handle:
                    handle.write(output)
                    handle.flush()
                    os.fsync(handle.fileno())
                current=os.stat(name,dir_fd=directory,follow_symlinks=False)
                if not stat.S_ISREG(current.st_mode) or (current.st_dev,current.st_ino,current.st_mtime_ns,current.st_size)!=(info.st_dev,info.st_ino,info.st_mtime_ns,info.st_size):
                    return {'ok':False,'code':'version_conflict','error':'File changed while saving; the buffer is retained','status':409}
                os.replace(tmp,name,src_dir_fd=directory,dst_dir_fd=directory)
            finally:
                try:os.unlink(tmp,dir_fd=directory)
                except FileNotFoundError:pass
            return {'ok':True}
        canonical=os.path.join(root,*components)
        resource={'path':canonical,'name':components[-1],'mime':mimetypes.guess_type(canonical)[0] or 'application/octet-stream','size':info.st_size,'version':version,'editable':False,'document_editable':document_editable,'encoding':encoding,'newline':'crlf' if b'\r\n' in raw else 'lf','relative_path':'/'.join(components)}
        if request.get('identity') is True:
            resource['disk_identity']=[info.st_dev,info.st_ino,info.st_size,info.st_mtime_ns]
        result={'ok':True,'resource':resource}
        if 'observed_version' in request and request['observed_version']!=version and encoding and name.lower().endswith(('.md','.markdown','.mdown','.html','.htm')):
            end=os.fstat(descriptor)
            if info.st_mtime_ns!=end.st_mtime_ns or len(raw)!=end.st_size:
                return {'ok':False,'code':'version_conflict','error':'Document changed while reading; renew to try again','status':409}
            result['document_base64']=base64.b64encode(raw).decode('ascii')
        return result
    finally:
        for descriptor in reversed(descriptors): os.close(descriptor)

def main(request):
    try: result=perform(request)
    except FileNotFoundError: result={'ok':False,'code':'not_found','error':'Remote resource no longer exists','status':404}
    except (ValueError,OSError,TypeError,KeyError) as error: result={'ok':False,'code':'outside_root','error':str(error),'status':403}
    print(json.dumps(result,separators=(',',':')))
'''


NATIVE_SEARCH_HELPER = r'''
import re
def native_search(directory, components, request):
    mode=request.get('mode');query=request.get('query');options=request.get('options') or {}
    if mode not in ('files','text') or not isinstance(query,str) or len(query)>200 or not isinstance(options,dict):raise ValueError('Invalid native search request')
    if mode=='text' and (not query or '\n' in query or '\r' in query or options.get('isRegExp') or options.get('isMultiline')):raise ValueError('Remote content search supports single-line literal patterns only')
    for key in ('isCaseSensitive','isWordMatch','isRegExp','isMultiline'):
        if key in options and type(options[key]) is not bool:raise ValueError('Invalid search flag')
    def number(key,default,maximum):
        value=options.get(key,default)
        if type(value) is not int or value<0:raise ValueError('Invalid search limit')
        return min(value,maximum)
    limit=number('maxResults',500 if mode=='files' else 200,500 if mode=='files' else 200)
    max_file=number('maxFileSize',1024*1024,1024*1024);preview_chars=max(20,number('previewChars',200,2000))
    def relative(value):
        if not isinstance(value,str) or value.startswith('/') or '\\' in value or '\0' in value or any(p in ('.','..') for p in value.split('/')):raise ValueError('Invalid search scope')
        return value.strip('/')
    def expand(value,depth=0):
        begin=value.find('{')
        if begin<0:return [value]
        nesting=0;end=None
        for i in range(begin,len(value)):
            if value[i]=='{':nesting+=1
            elif value[i]=='}':
                nesting-=1
                if nesting==0:end=i;break
        if end is None:return [value]
        body=value[begin+1:end];choices=[];start=0;nesting=0
        for i,c in enumerate(body):
            if c=='{':nesting+=1
            elif c=='}':nesting-=1
            elif c==',' and nesting==0:choices.append(body[start:i]);start=i+1
        if not choices:return [value]
        if depth>=4:raise ValueError('Search glob is too complex')
        choices.append(body[start:]);result=[]
        for choice in choices:
            result.extend(expand(value[:begin]+choice+value[end+1:],depth+1))
            if len(result)>64:raise ValueError('Search glob is too complex')
        return result
    class SearchBudget(Exception):pass
    compiled_chars=[0];glob_work=[0]
    def tokenize(value):
        result=[];i=0
        while i<len(value):
            c=value[i];boundary=i==0 or value[i-1]=='/'
            if boundary and value[i:i+3]=='**/':result.append(('dir',None));i+=3;continue
            if boundary and value[i:]=='**':result.append(('tree',None));i+=2;continue
            if c=='*':
                if not result or result[-1][0]!='star':result.append(('star',None))
            elif c=='?':result.append(('one',None))
            elif c=='[' and value.find(']',i+1)>i+1:
                end=value.find(']',i+1);body=value[i+1:end];negative=body.startswith('!')
                if negative:body=body[1:]
                body=body.replace('\\','\\\\').replace('^','\\^')
                try:pattern=re.compile('['+('^' if negative else '')+body+']')
                except re.error as error:raise ValueError('Invalid search glob') from error
                result.append(('class',pattern));i=end
            else:result.append(('literal',c))
            i+=1
        return result
    def compile_globs(key):
        values=options.get(key,[])
        if not isinstance(values,list) or len(values)>64:raise ValueError('Too many search globs')
        result=[]
        for value in values:
            base=None
            if isinstance(value,dict):base=relative(value.get('base',''));value=value.get('pattern')
            if not isinstance(value,str) or len(value)>500:raise ValueError('Invalid search glob')
            alternatives=[]
            for expanded in expand(value):
                compiled_chars[0]+=len(expanded)
                if compiled_chars[0]>32000:raise ValueError('Search glob is too complex')
                alternatives.append(tokenize(expanded))
            result.append((base,alternatives))
        return result
    includes=compile_globs('includes');excludes=compile_globs('excludes')
    def accepts(pattern,value):
        def closure(states):
            pending=list(states)
            while pending:
                index,partial=pending.pop()
                if index>=len(pattern):continue
                if pattern[index][0] in ('star','tree') or (pattern[index][0]=='dir' and not partial):
                    following=(index+1,False)
                    if following not in states:states.add(following);pending.append(following)
            return states
        states=closure({(0,False)})
        for char in value:
            glob_work[0]+=len(states)
            if glob_work[0]>2000000 or time.monotonic()>deadline:raise SearchBudget()
            following=set()
            for index,partial in states:
                if index>=len(pattern):continue
                kind,data=pattern[index]
                if kind=='tree':following.add((index,False))
                elif kind=='star' and char!='/':following.add((index,False))
                elif kind=='dir':
                    if char!='/':following.add((index,True))
                    elif partial:following.add((index,False))
                elif kind=='literal' and data==char:following.add((index+1,False))
                elif kind=='one' and char!='/':following.add((index+1,False))
                elif kind=='class' and char!='/' and data.fullmatch(char):following.add((index+1,False))
            states=closure(following)
            if not states:return False
        return any(index==len(pattern) for index,partial in states)
    def matches(patterns,local,full):
        for base,alternatives in patterns:
            candidate=local
            if base is not None:
                if base and not full.startswith(base+'/'):continue
                candidate=full[len(base)+1:] if base else full
            if any(accepts(pattern,candidate) for pattern in alternatives):return True
        return False
    needle=re.compile((r'(?<!\w)' if options.get('isWordMatch') else '')+re.escape(query)+(r'(?!\w)' if options.get('isWordMatch') else ''),0 if options.get('isCaseSensitive') else re.I) if mode=='text' else None
    def fuzzy(value):
        pos=0;value=value.casefold()
        for char in query.casefold():
            pos=value.find(char,pos)
            if pos<0:return False
            pos+=1
        return True
    def units(value):return len(value.encode('utf-16-le'))//2
    pending=[[]];results=[];scanned=0;read_bytes=0;deadline=time.monotonic()+2;truncated=False;stopped=False
    while pending and not stopped:
        current=pending.pop()
        if len(current)>32 or scanned>=20000 or time.monotonic()>deadline:truncated=True;break
        opened=[]
        try:
            child=directory
            for component in current:
                child=os.open(component,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=child);opened.append(child)
            with os.scandir(child) as iterator:
                for item in iterator:
                    scanned+=1
                    if scanned>=20000 or time.monotonic()>deadline:truncated=True;stopped=True;break
                    if '\\' in item.name:truncated=True;continue
                    local='/'.join(current+[item.name]);full='/'.join(components+current+[item.name])
                    if matches(excludes,local,full) or matches(excludes,local+'/',full+'/'):continue
                    if item.is_dir(follow_symlinks=False):
                        if len(current)>=32:truncated=True
                        else:pending.append(current+[item.name])
                        continue
                    if not item.is_file(follow_symlinks=False) or (includes and not matches(includes,local,full)):continue
                    if mode=='files':
                        if not fuzzy(local):continue
                        info=item.stat(follow_symlinks=False);results.append({'name':item.name,'path':full,'type':'file','size':info.st_size,'mime':mimetypes.guess_type(item.name)[0] or 'application/octet-stream'})
                    else:
                        descriptor=None
                        try:
                            descriptor=os.open(item.name,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=child);info=os.fstat(descriptor)
                            if not stat.S_ISREG(info.st_mode):continue
                            if info.st_size>max_file or read_bytes+info.st_size>8*1024*1024:truncated=True;continue
                            raw=b''
                            while len(raw)<=info.st_size:
                                chunk=os.read(descriptor,min(info.st_size+1-len(raw),256*1024))
                                if not chunk:break
                                raw+=chunk
                            read_bytes+=len(raw)
                            if len(raw)!=info.st_size:truncated=True;continue
                            if b'\0' in raw:continue
                            try:text=raw.decode('utf-8-sig')
                            except UnicodeDecodeError:continue
                            for line_number,line in enumerate(text.replace('\r\n','\n').replace('\r','\n').split('\n')):
                                if time.monotonic()>deadline:truncated=True;stopped=True;break
                                for match in needle.finditer(line):
                                    start=max(0,match.start()-preview_chars//3);end=min(len(line),max(start+preview_chars,match.end()))
                                    results.append({'path':full,'line':line_number,'start':units(line[:match.start()]),'end':units(line[:match.end()]),'preview':line[start:end],'preview_start':units(line[start:match.start()]),'preview_end':units(line[start:match.end()])})
                                    if len(results)>limit:break
                                if len(results)>limit:break
                        except OSError:truncated=True
                        finally:
                            if descriptor is not None:os.close(descriptor)
                    if len(results)>limit:truncated=True;stopped=True;break
        except SearchBudget:truncated=True;stopped=True
        except OSError:truncated=True
        finally:
            for descriptor in reversed(opened):os.close(descriptor)
    return {'ok':True,'search_version':1,'entries' if mode=='files' else 'matches':results[:limit],'truncated':truncated,'next_cursor':None}
'''


def host_config(host_id):
    import hosts
    try:
        host = hosts.get_host(host_id)
    except ValueError as exc:
        raise fs.FileError('remote_unsupported',str(exc),501)
    if not host.get('configured') or not host.get('read_alias'):
        raise fs.FileError('remote_unsupported','This host has no enrolled read-only SSH alias',501)
    return host


def data_alias(host):
    alias = host.get('read_alias') or ''
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,160}',alias) or alias in (host.get('login_aliases') or ()) or alias.startswith('login.'):
        raise fs.FileError('remote_unsupported','Configure a safe data-reading SSH alias',501)
    if host.get('kind') == 'slurm' and alias == host.get('execution_alias'):
        raise fs.FileError('remote_unsupported','Cluster file reads must use a data-transfer alias, not the compute node',501)
    return alias


def request(host_id, operation, value):
    if operation not in ('root','list','stat','read','search','search2','save_document'):
        raise fs.FileError('read_only','Unsupported remote file operation',403)
    host = host_config(host_id)
    alias = data_alias(host)
    import host_connector
    argv = ['ssh',*host_connector.SSH_OPTIONS,alias,'python3 -c '+shlex.quote(BOOTSTRAP)]
    script = HELPER + (NATIVE_SEARCH_HELPER if operation=='search2' else '') + '\nmain(json.loads(' + repr(json.dumps(dict(value,operation=operation))) + '))\n'
    try:
        result = subprocess.run(argv,input=script,capture_output=True,text=True,timeout=8 if operation in ('search','search2') else 30)
    except (OSError,subprocess.SubprocessError):
        raise fs.FileError('host_unavailable','Remote read connection failed or timed out',503)
    if result.returncode or len(result.stdout) > 90*1024*1024:
        raise fs.FileError('host_unavailable','Remote reader failed or exceeded its bounded response limit',503)
    try:
        payload = json.loads(result.stdout)
    except (ValueError,TypeError):
        raise fs.FileError('host_unavailable','Remote reader returned an invalid response',503)
    if not isinstance(payload,dict):
        raise fs.FileError('host_unavailable','Remote reader returned an invalid response',503)
    if payload.get('ok') is not True:
        raise fs.FileError(payload.get('code','remote_read_failed'),payload.get('error','Remote read failed'),payload.get('status',503))
    return payload
