#!/usr/bin/env python3
"""Guarded client-only installer for a pre-signed immutable Library Code candidate.

With no flags or --check, inspect the installed bundle without changing it.
Installation requires --install, a whole-bundle SHA-256, and the expected embedded
extension source hash. Create and sign the candidate as a separate reviewed
release step, then compute its identity with --candidate-hash; this installer never
signs bundles, rewrites integrity metadata, or mixes in a staged extension.
A running client also requires --restart and must pass preservation guards.
--variant official installs Library Code Official (built by build.py --variant official)
beside the daily app, with its own bundle id, profile and extensions folder.
"""
from pathlib import Path
import argparse
import base64
import hashlib
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
APP = Path.home() / 'Applications/Library Code.app'
PACKAGE = ROOT / 'VSCode-darwin-arm64/Library Code.app'
NODE = ROOT / 'output/toolchain/node-v24.22.1-darwin-arm64/bin/node'
if not NODE.is_file():
    NODE = Path(shutil.which('node') or 'node')
sys.path.insert(0, str(Path(__file__).resolve().parent))
import variants

class GuardError(RuntimeError):
    pass

def resources(bundle):
    return bundle / 'Contents/Resources/app'

def digest(file):
    h = hashlib.sha256()
    with file.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024*1024), b''):
            h.update(chunk)
    return base64.b64encode(h.digest()).decode().rstrip('=')

def bundle_hash(bundle):
    """Stable identity of paths, modes, file contents and symlink targets."""
    bundle = Path(bundle)
    if not bundle.is_dir() or bundle.is_symlink():
        raise GuardError('Candidate bundle is not a real directory')
    h = hashlib.sha256()
    def walk(directory):
        for entry in sorted(directory.iterdir(), key=lambda p:p.name):
            rel = entry.relative_to(bundle).as_posix().encode()
            info = entry.lstat()
            mode = info.st_mode & 0o7777
            if entry.is_symlink():
                tag, value = b'L', os.readlink(entry).encode()
            elif entry.is_dir():
                tag, value = b'D', b''
            elif entry.is_file():
                tag = b'F'
                file_hash = hashlib.sha256()
                with entry.open('rb') as stream:
                    for chunk in iter(lambda:stream.read(1024*1024), b''):
                        file_hash.update(chunk)
                value = file_hash.digest()
            else:
                raise GuardError('Unsupported bundle entry: '+str(entry))
            for field in (tag, rel, str(mode).encode(), value):
                h.update(len(field).to_bytes(8,'big')); h.update(field)
            if tag == b'D': walk(entry)
    walk(bundle)
    return h.hexdigest()

def bad_checksums(bundle):
    base = resources(bundle)
    product = json.loads((base / 'product.json').read_text())
    checksums = product.get('checksums')
    if not isinstance(checksums,dict) or not checksums:
        raise GuardError('Bundle has no product checksums')
    bad=[]
    for rel,want in checksums.items():
        relative=Path(rel)
        if relative.is_absolute() or '..' in relative.parts:
            raise GuardError('Invalid checksum path')
        if digest(base/'out'/relative) != str(want).rstrip('='): bad.append(rel)
    return bad

def run(argv):
    return subprocess.run([str(x) for x in argv],cwd=ROOT,check=True,capture_output=True,text=True).stdout

def close_daily_client():
    # The endpoint ownership and client PID are checked immediately before this
    # call. CDP may disconnect as Browser.close completes; no process is killed.
    script = "const {connect}=require('./integration/qa/webviews.cjs');(async()=>{const c=await connect(19342);const s=await c.browser.newBrowserCDPSession();await s.send('Browser.close').catch(e=>{if(!/closed|disconnected/i.test(e.message))throw e});await c.browser.close().catch(e=>{if(!/closed|disconnected/i.test(e.message))throw e})})().catch(e=>{console.error(e);process.exitCode=1})"
    run([NODE,'-e',script])

def verify_signature(bundle):
    try: run(['codesign','--verify','--deep','--strict',bundle])
    except (OSError,subprocess.CalledProcessError) as exc:
        raise GuardError('Bundle signature invalid; prepare and sign the immutable candidate separately') from exc

def validate_candidate(candidate, expected_hash, expected_extension):
    if bundle_hash(candidate) != expected_hash:
        raise GuardError('Candidate identity does not match the reviewed whole-bundle hash')
    bad = bad_checksums(candidate)
    if bad: raise GuardError('Candidate product checksum mismatch: '+', '.join(bad))
    verify_signature(candidate)
    info = json.loads((resources(candidate)/'extensions/library-code/build-info.json').read_text())
    if info.get('sourceHash') != expected_extension:
        raise GuardError('Embedded extension does not match the reviewed source hash')
    return info

def processes():
    lines=run(['ps','-axo','pid=,ppid=,lstart=,comm=']).splitlines()
    result={}
    for line in lines:
        fields=line.strip().split(None,7)
        if len(fields)==8:
            result[int(fields[0])]={'parent':int(fields[1]),'started':' '.join(fields[2:7]),'command':fields[7]}
    return result

def client_processes(rows, app):
    executable=str(app/'Contents/MacOS/Library Code')
    return [pid for pid,row in rows.items() if row['command']==executable]

def check_children(pid, rows, app):
    descendants={pid}
    while True:
        new={p for p,r in rows.items() if r['parent'] in descendants}-descendants
        if not new: break
        descendants |= new
    outside=[p for p in descendants-{pid} if not rows[p]['command'].startswith(str(app)+'/')]
    if outside: raise GuardError('Client owns external processes; preserve them and do not close the client')
    return {p:(rows[p]['started'],rows[p]['command']) for p in descendants}

def check_debug_endpoint(identity):
    try:
        listeners={int(line) for line in run(['lsof','-nP','-t','-iTCP:19342','-sTCP:LISTEN']).splitlines() if line.strip()}
    except (OSError,ValueError,subprocess.SubprocessError) as exc:
        raise GuardError('Cannot verify the daily client debugging endpoint') from exc
    if len(listeners)!=1 or not listeners.issubset(identity):
        raise GuardError('Debugging endpoint does not belong to the selected client process tree')

def check_snapshot(snapshot):
    groups=snapshot.get('workbench',{}).get('groups')
    if not isinstance(groups,list) or not groups or 'activeRuns' not in snapshot or 'views' not in snapshot:
        raise GuardError('Client preservation snapshot is incomplete')
    if any(tab.get('dirty') for group in groups for tab in group.get('tabs',[])):
        raise GuardError('Dirty native editor is open; preserve it before closing the client')

def tab_layout(snapshot):
    return [[(tab.get('name'),bool(tab.get('dirty'))) for tab in group.get('tabs',[])]
            for group in snapshot['workbench']['groups']]

def draft_hashes(snapshot):
    return sorted((json.dumps(v.get('resource'),sort_keys=True),key,value)
                  for v in snapshot.get('views',[]) for key,value in v.get('draftHashes',{}).items())

def snapshot(receipt, name):
    target=receipt/(name+'.json')
    run([NODE,ROOT/'integration/qa/delivery-state.cjs',target])
    return json.loads(target.read_text())

def wait_restored(receipt, before, attempts=60):
    """Wait for restored groups, tabs and webviews; retain each private sample."""
    expected_groups=before['workbench']['groups']
    expected_tabs=sum(len(group.get('tabs',[])) for group in expected_groups)
    expected_views=len(before.get('views',[]))
    for attempt in range(attempts):
        try:
            after=snapshot(receipt,f'after-{attempt:03d}')
            groups=after.get('workbench',{}).get('groups',[])
            tabs=sum(len(group.get('tabs',[])) for group in groups)
            if len(groups)>=len(expected_groups) and tabs>=expected_tabs and len(after.get('views',[]))>=expected_views:
                return after
        except (subprocess.SubprocessError,OSError,ValueError,KeyError,TypeError):
            pass
        time.sleep(.5)
    raise GuardError('Client reopened; restoration verification is pending, possibly due to a Keychain prompt. Inspect '+str(receipt))

def draft_capture(receipt, mode):
    output=run([sys.executable,ROOT/'integration/qa/draft-preservation.py',mode,receipt/'drafts.json'])
    return json.loads(output)

def check_report(app=APP):
    try:
        bad=bad_checksums(app); verify_signature(app)
        return {'ok':not bad,'checksums':'ok' if not bad else bad,'signature':'ok','client_pids':client_processes(processes(),app)}
    except (OSError,ValueError,KeyError,GuardError,subprocess.SubprocessError) as exc:
        return {'ok':False,'error':str(exc)}

def install(candidate, expected_hash, expected_extension, restart=False, app=APP, bundle_id='local.library.code', before_replace=None, expected_previous=None):
    candidate=Path(candidate); app=Path(app)
    if candidate.resolve()==app.resolve(): raise GuardError('Candidate and installed app must be distinct')
    validate_candidate(candidate,expected_hash,expected_extension)
    # A variant's first install has no previous app.
    if app.exists() and not check_report(app)['ok']: raise GuardError('Installed app failed its integrity or signature check')
    installed_hash=lambda: bundle_hash(app) if app.exists() else None
    original=installed_hash()
    if expected_previous is not None and original != expected_previous:
        raise GuardError('Installed release changed after queueing.')
    receipt=Path(tempfile.mkdtemp(prefix='library-install-',dir=app.parent)); os.chmod(receipt,0o700)
    staged=receipt/'candidate.app'; backup=receipt/'previous.app'
    before=None; launch_args=[]; pid=None; close_attempted=False; reopened=False
    daily_profile=bundle_id=='local.library.code'
    try:
        rows=processes(); clients=client_processes(rows,app)
        if len(clients)>1: raise GuardError('Multiple daily clients detected; no files changed')
        if clients:
            if not daily_profile: raise GuardError('A running non-daily profile cannot be verified by the daily preservation helpers; close it safely before installing')
            if not restart: raise GuardError('Client is running; explicit --restart is required')
            pid=clients[0]; identity=check_children(pid,rows,app)
            raw=run(['ps','-p',str(pid),'-o','args=']).strip()
            executable=str(app/'Contents/MacOS/Library Code')
            if not raw.startswith(executable): raise GuardError('Cannot preserve launch arguments')
            launch_args=shlex.split(raw[len(executable):])
            if any(a in ('--user-data-dir','--profile','--profile-name') or a.startswith(('--user-data-dir=','--profile=','--profile-name=')) for a in launch_args):
                raise GuardError('A custom profile cannot be verified by the daily preservation helpers')
            ports=[a.split('=',1)[1] for a in launch_args if a.startswith('--remote-debugging-port=')]
            ports += [launch_args[i+1] for i,a in enumerate(launch_args[:-1]) if a=='--remote-debugging-port']
            if ports!=['19342']:
                raise GuardError('The daily client must use the verified 19342 debugging endpoint for preservation checks')
            check_debug_endpoint(identity)
            before=snapshot(receipt,'before');check_snapshot(before)
        if before_replace: before_replace()
        run(['ditto',candidate,staged])
        validate_candidate(staged,expected_hash,expected_extension)
        if daily_profile and not (before_replace and pid): draft_capture(receipt,'capture')
        if bundle_hash(candidate)!=expected_hash or installed_hash()!=original:
            raise GuardError('Candidate or installed app changed during preflight')
        if pid:
            if check_children(pid,processes(),app)!=identity: raise GuardError('Client process tree changed during preflight')
            check_debug_endpoint(identity)
            if before_replace:
                before = snapshot(receipt, 'before-close')
                check_snapshot(before)
                if before['activeRuns']:
                    raise GuardError('New agent activity appeared before client close.')
                draft_capture(receipt, 'capture')
                before_replace()
            close_attempted=True
            close_daily_client()
            for _ in range(150):
                if pid not in processes(): break
                time.sleep(.1)
            else: raise GuardError('Client did not close gracefully; no files changed')
            draft_capture(receipt,'check')
        if client_processes(processes(),app): raise GuardError('Client reopened during preflight; no files changed')
        if bundle_hash(candidate)!=expected_hash or installed_hash()!=original:
            raise GuardError('Candidate or installed app changed before replacement')
        if before_replace: before_replace()
        if original: app.rename(backup)
        try:
            staged.rename(app)
            validate_candidate(app,expected_hash,expected_extension)
        except Exception:
            if app.exists(): app.rename(receipt/'failed-candidate.app')
            if original: backup.rename(app)
            raise
        if pid:
            run(['open','-g','-a',app,'--args',*launch_args])
            reopened=True
            after=wait_restored(receipt,before)
            draft_capture(receipt,'check')
            if tab_layout(before)!=tab_layout(after) or draft_hashes(before)!=draft_hashes(after):
                raise GuardError('Reopened client state differs; inspect private receipt '+str(receipt))
            old={r['id']:(r.get('process_pid'),r.get('process_identity')) for r in before['activeRuns']}
            new={r['id']:(r.get('process_pid'),r.get('process_identity')) for r in after['activeRuns']}
            if any(new.get(k)!=v for k,v in old.items()):
                raise GuardError('Provider run identity changed or disappeared; inspect private receipt '+str(receipt))
        return {'ok':True,'rollback':str(backup),'receipt':str(receipt),'reopened':bool(pid)}
    except Exception as exc:
        # Only recover after an attempted close, and never disturb a client that
        # may already have reopened or an installation that changed concurrently.
        if close_attempted and not reopened:
            try:
                if not client_processes(processes(),app):
                    if backup.exists():
                        if app.exists():
                            if bundle_hash(app)!=expected_hash:
                                raise GuardError('Installed bundle changed; automatic recovery refused')
                            app.rename(receipt/'failed-candidate.app')
                        backup.rename(app)
                    if not app.exists() or bundle_hash(app)!=original:
                        raise GuardError('Original bundle identity changed; automatic recovery refused')
                    run(['open','-g','-a',app,'--args',*launch_args])
            except Exception as recovery:
                raise GuardError(str(exc)+'; original client recovery failed: '+str(recovery)+'; inspect '+str(receipt)) from exc
        if before_replace and not close_attempted and staged.is_dir() and not backup.exists():
            # A deferred idle check needs the receipt, not another full staging copy.
            shutil.rmtree(staged)
        # Keep the unique receipt, evidence and any backup for inspection.
        raise

def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    mode=parser.add_mutually_exclusive_group()
    mode.add_argument('--check',action='store_true',help='inspect installed app without changes (default)')
    mode.add_argument('--install',action='store_true',help='install a reviewed pre-signed candidate')
    mode.add_argument('--candidate-hash',action='store_true',help='print the candidate whole-bundle SHA-256')
    parser.add_argument('--candidate',type=Path,default=PACKAGE)
    parser.add_argument('--expected-sha256')
    parser.add_argument('--expected-extension-hash')
    parser.add_argument('--restart',action='store_true',help='allow graceful client close and reopen after preservation checks')
    parser.add_argument('--variant',choices=sorted(variants.VARIANTS),default='library',help='which app: the daily Library Code or Library Code Official')
    args=parser.parse_args(argv)
    app=variants.paths(args.variant)['app']
    if args.variant!='library' and args.candidate==PACKAGE: args.candidate=ROOT/variants.VARIANTS[args.variant]['package']/'Library Code.app'
    if args.restart and not args.install: parser.error('--restart requires --install')
    if args.install and (not args.expected_sha256 or not args.expected_extension_hash):
        parser.error('--install requires --expected-sha256 and --expected-extension-hash')
    try:
        if args.candidate_hash: print(bundle_hash(args.candidate));return 0
        if args.install and args.variant=='official':
            variants.validate_official_runtime(args.candidate, ROOT.parent/'chat-dashboard')
        result=install(args.candidate,args.expected_sha256,args.expected_extension_hash,args.restart,app,variants.VARIANTS[args.variant]['bundle_id']) if args.install else check_report(app)
        if args.install and args.variant=='official':
            result['extensions']=variants.install_official_extensions(variants.paths(args.variant)['extensions'])
            result['official_runtime']=variants.install_official_runtime(app, ROOT.parent/'chat-dashboard')
        print(json.dumps(result));return 0 if result.get('ok') else 1
    except (GuardError,OSError,ValueError,KeyError,subprocess.SubprocessError) as exc:
        print(json.dumps({'ok':False,'error':str(exc)}),file=sys.stderr);return 1

if __name__=='__main__': sys.exit(main())
