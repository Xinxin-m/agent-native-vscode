"""Verified transfers between this Mac and an enrolled cluster. Cluster file traffic uses its data-transfer alias only."""
import contextlib
import ctypes
import errno
import hashlib
import json
import os
import re
import shlex
import stat
import subprocess
import sys
import tarfile
import threading
import time
from urllib.parse import quote
import uuid

STREAM_TIMEOUT = 3600
MAX_ENTRIES = 100000


def parts(value):
    if not isinstance(value, str) or value.startswith('/') or '\\' in value or '\0' in value or any(p == '..' for p in value.split('/')):
        raise ValueError('A transfer path must remain inside its workspace')
    return [p for p in value.split('/') if p not in ('', '.')]


@contextlib.contextmanager
def directory(root, relative=''):
    if not isinstance(root, str) or not root.startswith('/') or '\0' in root:
        raise ValueError('An absolute registered workspace is required')
    handles = []
    try:
        fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
        handles.append(fd)
        for name in [p for p in root.split('/') if p] + parts(relative):
            fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            handles.append(fd)
        yield fd
    finally:
        for fd in reversed(handles):
            os.close(fd)


def stamp(info):
    return [info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns, stat.S_IMODE(info.st_mode)]


def inspect(root, relative):
    names = parts(relative)
    if not names:
        raise ValueError('A whole workspace cannot be transferred')
    entries, identities, total = [], {}, [0]
    def visit(parent, name, path):
        if len(entries) >= MAX_ENTRIES:
            raise ValueError('A transfer is limited to 100000 entries')
        info = os.stat(name, dir_fd=parent, follow_symlinks=False)
        if stat.S_ISLNK(info.st_mode) or not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode)):
            raise ValueError('Transfers support regular files and folders, without symlinks or special files')
        flags = os.O_RDONLY | os.O_NOFOLLOW | (os.O_DIRECTORY if stat.S_ISDIR(info.st_mode) else os.O_NONBLOCK)
        fd = os.open(name, flags, dir_fd=parent)
        try:
            before = os.fstat(fd)
            if stamp(info) != stamp(before):
                raise ValueError('The source changed while preparing the transfer')
            identities[path] = stamp(before)
            if stat.S_ISDIR(before.st_mode):
                entries.append({'path':path, 'type':'directory', 'mode':stat.S_IMODE(before.st_mode) & 0o777})
                for child in sorted(os.listdir(fd)):
                    visit(fd, child, '/'.join(filter(None, (path, child))))
            else:
                total[0] += before.st_size
                digest = hashlib.sha256()
                while True:
                    chunk = os.read(fd, 1024 * 1024)
                    if not chunk:
                        break
                    digest.update(chunk)
                entries.append({'path':path, 'type':'file', 'mode':stat.S_IMODE(before.st_mode) & 0o777, 'size':before.st_size, 'sha256':digest.hexdigest()})
            if stamp(before) != stamp(os.fstat(fd)):
                raise ValueError('The source changed while preparing the transfer')
        finally:
            os.close(fd)
    with directory(root, '/'.join(names[:-1])) as parent:
        visit(parent, names[-1], '')
    return {'entries':entries, 'identity':identities, 'bytes':total[0]}


def remove_private(parent, name):
    info = os.stat(name, dir_fd=parent, follow_symlinks=False)
    if stat.S_ISDIR(info.st_mode):
        fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
        try:
            os.fchmod(fd, stat.S_IMODE(info.st_mode) | 0o700)
            for child in os.listdir(fd):
                remove_private(fd, child)
        finally:
            os.close(fd)
        os.rmdir(name, dir_fd=parent)
    else:
        os.unlink(name, dir_fd=parent)


def stage_name(token):
    if not re.fullmatch('[0-9a-f]{32}', str(token)):
        raise ValueError('Invalid transfer token')
    return '.library-transfer-' + token


def export_archive(request, output):
    root, source = request['root'], request['path']
    manifest = inspect(root, source)
    with tarfile.open(fileobj=output, mode='w|') as archive:
        for entry in manifest['entries']:
            rel = entry['path']
            header = tarfile.TarInfo('payload' + ('/' + rel if rel else ''))
            header.mode = entry['mode']
            if entry['type'] == 'directory':
                header.type = tarfile.DIRTYPE
                archive.addfile(header)
                continue
            header.size = entry['size']
            full = '/'.join(parts(source) + parts(rel))
            names = parts(full)
            with directory(root, '/'.join(names[:-1])) as parent:
                fd = os.open(names[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
                with os.fdopen(fd, 'rb') as file:
                    if stamp(os.fstat(file.fileno())) != manifest['identity'][rel]:
                        raise ValueError('The source changed during transfer')
                    archive.addfile(header, file)
    output.flush()


def receive_archive(request, input):
    root, dest, token = request['root'], request.get('dir', ''), request['token']
    stage = stage_name(token)
    with directory(root, dest) as parent:
        os.mkdir(stage, 0o700, dir_fd=parent)
    relstage = '/'.join(parts(dest) + [stage])
    try:
        modes, seen, total = [], set(), 0
        with tarfile.open(fileobj=input, mode='r|') as archive:
            for member in archive:
                names = parts(member.name)
                if not names or names[0] != 'payload' or member.name.startswith('/') or not (member.isfile() or member.isdir()) or member.name in seen:
                    raise ValueError('Unsafe or duplicate archive entry')
                if len(seen) >= MAX_ENTRIES:
                    raise ValueError('Too many transfer entries')
                seen.add(member.name)
                total += member.size
                with directory(root, '/'.join(parts(relstage) + names[:-1])) as parent:
                    if member.isdir():
                        os.mkdir(names[-1], 0o700, dir_fd=parent)
                        modes.append(('/'.join(parts(relstage) + names), member.mode & 0o777))
                    else:
                        fd = os.open(names[-1], os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent)
                        with os.fdopen(fd, 'wb') as output:
                            source = archive.extractfile(member)
                            count = 0
                            while True:
                                chunk = source.read(1024 * 1024)
                                if not chunk:
                                    break
                                output.write(chunk)
                                count += len(chunk)
                            if count != member.size:
                                raise ValueError('Incomplete transfer archive')
                            output.flush()
                            os.fsync(output.fileno())
                            os.fchmod(output.fileno(), member.mode & 0o777)
        if not seen:
            raise ValueError('Empty transfer archive')
        for relative, mode in reversed(modes):
            with directory(root, relative) as fd:
                os.fchmod(fd, mode)
        return {'ok':True, 'manifest':inspect(root, relstage + '/payload')}
    except BaseException:
        with directory(root, dest) as parent:
            remove_private(parent, stage)
        raise


def rename_exclusive(source_fd, source, dest_fd, dest):
    libc = ctypes.CDLL(None, use_errno=True)
    if hasattr(libc, 'renameat2'):
        result = libc.renameat2(source_fd, os.fsencode(source), dest_fd, os.fsencode(dest), 1)
    elif hasattr(libc, 'renameatx_np'):
        result = libc.renameatx_np(source_fd, os.fsencode(source), dest_fd, os.fsencode(dest), 0x4)
    else:
        raise OSError('This filesystem does not support atomic no-replace transfers')
    if result:
        code = ctypes.get_errno()
        raise OSError(code, os.strerror(code))


def perform(request):
    operation, root = request['operation'], request['root']
    if operation == 'inspect':
        return {'ok':True, 'manifest':inspect(root, request['path'])}
    if operation == 'list':
        with directory(root, request.get('dir', '')) as fd:
            return {'ok':True, 'names':os.listdir(fd)}
    if operation == 'discard':
        with directory(root, request.get('dir', '')) as fd:
            try:
                remove_private(fd, stage_name(request['token']))
            except FileNotFoundError:
                pass
        return {'ok':True}
    if operation == 'commit':
        name = request['name']
        if parts(name) != [name]:
            raise ValueError('Invalid destination name')
        stage = stage_name(request['token'])
        with directory(root, request.get('dir', '')) as fd:
            stagefd = os.open(stage, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            try:
                rename_exclusive(stagefd, 'payload', fd, name)
            finally:
                os.close(stagefd)
            os.rmdir(stage, dir_fd=fd)
        return {'ok':True}
    if operation == 'retire':
        source, token = request['path'], request['token']
        expected = request['expected']
        if inspect(root, source) != expected:
            raise ValueError('The source changed; the destination copy is complete and the source was preserved')
        names = parts(source)
        stage_name(token)
        backup = '.library-transfer-source-' + token
        with directory(root, '/'.join(names[:-1])) as fd:
            rename_exclusive(fd, names[-1], fd, backup)
        backup_path = '/'.join(names[:-1] + [backup])
        try:
            staged = inspect(root, backup_path)
            if staged['entries'] != expected['entries']:
                raise ValueError('The source changed; the move was cancelled')
        except BaseException as error:
            try:
                with directory(root, '/'.join(names[:-1])) as fd:
                    rename_exclusive(fd, backup, fd, names[-1])
            except BaseException:
                raise RecoveryError(str(error), [{'path':os.path.join(root,backup_path)}]) from error
            raise
        return {'ok':True, 'backup':backup_path, 'manifest':staged}
    if operation == 'restore':
        source, backup = parts(request['path']), parts(request['backup'])
        if source[:-1] != backup[:-1] or not backup[-1].startswith('.library-transfer-source-'):
            raise ValueError('Invalid source recovery path')
        with directory(root, '/'.join(source[:-1])) as fd:
            rename_exclusive(fd, backup[-1], fd, source[-1])
        return {'ok':True}
    if operation == 'trash':
        backup = request['backup']
        names = parts(backup)
        if not names or not re.fullmatch(r'\.library-transfer-source-[0-9a-f]{32}', names[-1]):
            raise ValueError('Invalid source recovery path')
        # Keep originals recoverable. Never unlink a verified source: an open writer could change it after verification.
        home = os.path.expanduser('~')
        mac = sys.platform == 'darwin'
        trash = os.path.join(home,'.Trash') if mac else os.path.join(os.environ.get('XDG_DATA_HOME') or os.path.join(home,'.local','share'),'Trash','files')
        os.makedirs(trash, mode=0o700, exist_ok=True)
        name = request['name'] + '-library-' + request['token']
        if parts(name) != [name]:
            raise ValueError('Invalid trash name')
        info_path = None
        with directory(root, '/'.join(names[:-1])) as sourcefd, directory(trash) as trashfd:
            if os.fstat(sourcefd).st_dev != os.fstat(trashfd).st_dev:
                return {'ok':True, 'path':os.path.join(root,backup), 'kind':'staging', 'warning':'Trash is on another filesystem; source recovery data was retained in place'}
            if not mac:
                info = os.path.join(os.path.dirname(trash),'info')
                os.makedirs(info,mode=0o700,exist_ok=True)
                info_path = os.path.join(info,name+'.trashinfo')
                with open(info_path,'x',encoding='utf-8') as file:
                    file.write('[Trash Info]\nPath='+quote(os.path.join(root,request['original']))+'\nDeletionDate='+time.strftime('%Y-%m-%dT%H:%M:%S')+'\n')
            try:
                rename_exclusive(sourcefd,names[-1],trashfd,name)
            except BaseException:
                if info_path:
                    os.unlink(info_path)
                raise
        return {'ok':True, 'path':os.path.join(trash,name), 'kind':'trash'}
    raise ValueError('Unsupported transfer operation')


class RecoveryError(ValueError):
    def __init__(self, message, recovery):
        super().__init__(message)
        self.recovery = recovery


class Endpoint:
    def __init__(self, root):
        self.root = root
        self.remote = root.get('host_id', 'mac') != 'mac'

    def argv(self):
        import remote_files
        import host_connector
        host = remote_files.host_config(self.root['host_id'])
        if host.get('kind') != 'slurm':
            raise ValueError('Choose an enrolled cluster workspace for cross-host transfers')
        alias = remote_files.data_alias(host)
        with open(__file__, encoding='utf-8') as file:
            script = file.read()
        return ['ssh', *host_connector.SSH_OPTIONS, alias, 'python3 -c ' + shlex.quote(script)]

    def command(self, operation, **values):
        request = dict(values, operation=operation, root=self.root['path'])
        if not self.remote:
            try:
                return perform(request)
            except RecoveryError as error:
                error.recovery=[dict(row,host_id=self.root.get('host_id','mac')) for row in error.recovery]
                raise
        result = subprocess.run(self.argv(), input=(json.dumps(request)+'\n').encode(), capture_output=True, timeout=3600)
        if result.returncode:
            message=result.stderr.decode(errors='replace')[-4000:]
            try:
                failure=json.loads(message.splitlines()[-1])
            except (ValueError,IndexError):
                raise ValueError(message or 'Cluster transfer helper failed')
            raise RecoveryError(failure.get('error','Cluster transfer failed'),[dict(row,host_id=self.root['host_id']) for row in failure.get('recovery',[])])
        payload = json.loads(result.stdout)
        if not payload.get('ok'):
            raise ValueError(payload.get('error') or 'Cluster transfer failed')
        return payload


def stream(source, relative, destination, dest_dir, token):
    export = {'operation':'export', 'root':source.root['path'], 'path':relative}
    receive = {'operation':'receive', 'root':destination.root['path'], 'dir':dest_dir, 'token':token}
    if destination.remote:
        process = subprocess.Popen(destination.argv(), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        timer=threading.Timer(STREAM_TIMEOUT,process.kill);timer.daemon=True;timer.start()
        try:
            process.stdin.write((json.dumps(receive)+'\n').encode())
            export_archive(export, process.stdin)
            process.stdin.close()
            output, error = process.stdout.read(), process.stderr.read()
            if process.wait(timeout=3600):
                raise ValueError(error.decode(errors='replace')[-2000:] or 'Cluster upload failed')
            result = json.loads(output)
            if not result.get('ok'):
                raise ValueError(result.get('error') or 'Cluster upload failed')
            return result
        finally:
            if process.poll() is None:
                process.kill()
            process.wait();timer.cancel()
            for pipe in (process.stdin,process.stdout,process.stderr):pipe.close()
    process = subprocess.Popen(source.argv(), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    timer=threading.Timer(STREAM_TIMEOUT,process.kill);timer.daemon=True;timer.start()
    try:
        process.stdin.write((json.dumps(export)+'\n').encode())
        process.stdin.close()
        result = receive_archive(receive, process.stdout)
        process.stdout.read()
        error = process.stderr.read()
        if process.wait(timeout=3600):
            raise ValueError(error.decode(errors='replace')[-2000:] or 'Cluster download failed')
        return result
    finally:
        if process.poll() is None:
            process.kill()
        process.wait();timer.cancel()
        for pipe in (process.stdin,process.stdout,process.stderr):pipe.close()


def keep_both(taken, name, directory):
    stem, ext = (name, '') if directory else os.path.splitext(name)
    while stem+ext in taken:
        match = re.fullmatch(r'(.+ copy)( \d+)?', stem)
        stem = (match.group(1)+' '+str(int(match.group(2) or 1)+1)) if match else stem+' copy'
    return stem+ext


def transfer(service, items, dest, dest_dir='', copy=False, conflict='ask', confirmed_move=False):
    import file_service as fs
    if not copy and not confirmed_move:
        raise fs.FileError('move_confirmation_required', 'Choose Move to confirm copying, verification and source removal', 409)
    if conflict not in ('ask', 'keep_both'):
        raise fs.FileError('unsafe_replace', 'Cross-host transfers preserve existing items. Choose Keep both.', 409)
    roots = [service.get_root(item.get('root_id')) for item in items]
    sources = [Endpoint(root) for root in roots]
    target = Endpoint(dest)
    if any(source.remote == target.remote for source in sources):
        raise fs.FileError('cross_host', 'Transfer one direction at a time between this Mac and one remote host', 409)
    if not target.remote and dest.get('access') != 'write':
        raise fs.FileError('read_only', 'The destination workspace is read-only', 403)
    for source in sources:
        if not copy and not source.remote and source.root.get('access') != 'write':
            raise fs.FileError('read_only', 'The source workspace is read-only', 403)
    parts(dest_dir)
    plans, stages, published, retired, warnings, recoveries = [], [], [], [], [], []
    receipt = None
    caught_error = None
    attempted = []
    try:
        taken = set(target.command('list', dir=dest_dir)['names'])
        names = set()
        for source, item in zip(sources, items):
            relative = '/'.join(parts(item.get('path')))
            if not relative:
                raise ValueError('A whole workspace cannot be transferred')
            name = relative.split('/')[-1]
            if name in names:
                raise fs.FileError('duplicate_names', 'Two selected items have the same name: '+name, 409)
            names.add(name)
            manifest = source.command('inspect', path=relative)['manifest']
            if name in taken:
                if conflict == 'ask':
                    raise fs.FileError('destination_exists', 'An item already exists in the destination', 409, names=[name])
                name = keep_both(taken, name, manifest['entries'][0]['type']=='directory')
            taken.add(name)
            plans.append({'source':source, 'path':relative, 'name':name, 'expected':manifest, 'token':uuid.uuid4().hex})
        for plan in plans:
            stages.append(plan)
            result = stream(plan['source'], plan['path'], target, dest_dir, plan['token'])
            if result['manifest']['entries'] != plan['expected']['entries']:
                raise ValueError('Destination verification failed; all source data was preserved')
        for plan in plans:
            if plan['source'].command('inspect', path=plan['path'])['manifest'] != plan['expected']:
                raise ValueError('The source changed during transfer; all source data was preserved')
        for plan in plans:
            attempted.append(plan)
            target.command('commit', dir=dest_dir, token=plan['token'], name=plan['name'])
            published.append(plan)
        for plan in plans:
            destination = '/'.join(parts(dest_dir) + [plan['name']])
            if target.command('inspect', path=destination)['manifest']['entries'] != plan['expected']['entries']:
                raise ValueError('Destination changed before source removal; source data was preserved')
        if not copy:
            for plan in plans:
                result = plan['source'].command('retire', path=plan['path'], token=plan['token'], expected=plan['expected'])
                plan['retired'] = result
                retired.append(plan)
            for plan in retired:
                try:
                    result = plan['source'].command('trash', backup=plan['retired']['backup'], original=plan['path'], name=plan['path'].split('/')[-1], token=plan['token'])
                    recovery={'host_id':plan['source'].root['host_id'],'path':result['path'],'kind':result['kind']}
                    recoveries.append(recovery)
                    if result.get('warning'):
                        warnings.append(dict(recovery,message=result['warning']))
                except Exception as error:
                    recovery={'host_id':plan['source'].root['host_id'],'path':os.path.join(plan['source'].root['path'],plan['retired']['backup']),'kind':'staging'}
                    recoveries.append(recovery);warnings.append(dict(recovery,message=str(error)))
        receipt = {'ok':True, 'cross_host':True, 'copy':bool(copy), 'token':None, 'warnings':warnings, 'recovery':recoveries,
                'moved':[{'root_id':p['source'].root['id'], 'path':p['path'], 'dest_root_id':dest['id'], 'dest_path':'/'.join(parts(dest_dir)+[p['name']]), 'host_id':p['source'].root['host_id'], 'dest_host_id':dest['host_id']} for p in plans]}
        return receipt
    except BaseException as error:
        recovery = list(getattr(error,'recovery',[]))
        for plan in reversed(retired):
            try:
                plan['source'].command('restore', path=plan['path'], backup=plan['retired']['backup'])
            except Exception:
                recovery.append({'host_id':plan['source'].root['host_id'], 'path':os.path.join(plan['source'].root['path'], plan['retired']['backup'])})
        if isinstance(error, fs.FileError) and not published and not retired:
            caught_error = error
            error.details.setdefault('recovery',[])
            raise
        checks=[{'host_id':dest['host_id'],'path':os.path.join(dest['path'],dest_dir,p['name']),'kind':'check_destination'} for p in attempted if p not in published]
        message=str(error)+(' Check destination before retrying: '+', '.join(row['host_id']+': '+row['path'] for row in checks) if checks else '')
        caught_error = fs.FileError('transfer_partial' if published else 'transfer_failed', message, 409,
                           completed=[{'host_id':dest['host_id'], 'path':os.path.join(dest['path'],dest_dir,p['name'])} for p in published], recovery=recovery+checks, check_destinations=checks)
        raise caught_error from error
    finally:
        for plan in stages:
            if plan in published:
                continue
            try:
                target.command('discard', dir=dest_dir, token=plan['token'])
            except Exception as cleanup_error:
                retained={'host_id':dest['host_id'],'path':os.path.join(dest['path'],dest_dir,stage_name(plan['token'])),'kind':'staging','message':'Destination staging cleanup could not be confirmed: '+str(cleanup_error)}
                if caught_error is not None:
                    caught_error.details.setdefault('recovery',[]).append(retained)
                elif receipt is not None:
                    receipt.setdefault('warnings',[]).append(retained)
                    receipt.setdefault('recovery',[]).append(retained)


if __name__ == '__main__':
    try:
        request = json.loads(sys.stdin.buffer.readline())
        if request['operation'] == 'export':
            export_archive(request, sys.stdout.buffer)
        else:
            result = receive_archive(request, sys.stdin.buffer) if request['operation']=='receive' else perform(request)
            print(json.dumps(result, separators=(',', ':')))
    except BaseException as error:
        print(json.dumps({'error':str(error),'recovery':getattr(error,'recovery',[])}), file=sys.stderr)
        sys.exit(1)
