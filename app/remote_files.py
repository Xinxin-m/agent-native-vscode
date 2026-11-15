"""On-demand read-only SSH file helper. No remote install, scheduler or project code."""
import base64
import json
import re
import shlex
import subprocess

import file_service as fs

BOOTSTRAP = "import sys; exec(compile(sys.stdin.read(), '<workbench-read-only>', 'exec'))"
# The fixed helper contains only stdlib filesystem reads. Request data is passed
# through stdin, never interpolated into a remote shell command.
HELPER = r'''
import base64, hashlib, json, mimetypes, os, stat

def perform(request):
    operation=request['operation']
    if operation not in ('root','list','stat','read'):
        return {'ok':False,'code':'read_only','error':'Only root/list/stat/read are supported','status':403}
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
        if operation in ('root','list'):
            directory_parts+=components
        else:
            if not components: raise ValueError('Choose a regular file')
            directory_parts+=components[:-1]
        for component in directory_parts:
            directory=os.open(component,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=directory)
            descriptors.append(directory)
        if operation=='root': return {'ok':True,'path':root}
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
        if not stat.S_ISREG(info.st_mode): raise ValueError('Only regular files may be read')
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
        canonical=os.path.join(root,*components)
        return {'ok':True,'resource':{'path':canonical,'name':components[-1],'mime':mimetypes.guess_type(canonical)[0] or 'application/octet-stream','size':info.st_size,'version':version,'editable':False,'encoding':encoding,'newline':'crlf' if b'\r\n' in raw else 'lf','relative_path':'/'.join(components)}}
    finally:
        for descriptor in reversed(descriptors): os.close(descriptor)

def main(request):
    try: result=perform(request)
    except FileNotFoundError: result={'ok':False,'code':'not_found','error':'Remote resource no longer exists','status':404}
    except (ValueError,OSError,TypeError,KeyError) as error: result={'ok':False,'code':'outside_root','error':str(error),'status':403}
    print(json.dumps(result,separators=(',',':')))
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


def request(host_id, operation, value):
    if operation not in ('root','list','stat','read'):
        raise fs.FileError('read_only','Remote workspaces support read-only file access',403)
    host = host_config(host_id)
    alias = host.get('read_alias') or ''
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,160}',alias) or alias in ('cluster','example-cluster','cluster-via-cluster-dtn','cluster-tmux') or alias.startswith('login.'):
        raise fs.FileError('remote_unsupported','Configure a safe data-reading SSH alias',501)
    if host.get('kind') == 'slurm' and alias != 'cluster-dtn':
        raise fs.FileError('remote_unsupported','Cluster file reads must use the cluster-dtn data-transfer alias',501)
    import host_connector
    argv = ['ssh',*host_connector.SSH_OPTIONS,alias,'python3 -c '+shlex.quote(BOOTSTRAP)]
    script = HELPER + '\nmain(json.loads(' + repr(json.dumps(dict(value,operation=operation))) + '))\n'
    try:
        result = subprocess.run(argv,input=script,capture_output=True,text=True,timeout=30)
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
