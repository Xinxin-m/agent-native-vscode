#!/usr/bin/env python3
"""Build a private-data-free Workspace distribution from an explicit allowlist."""
import argparse
import datetime
import hashlib
import json
from pathlib import Path
import re
import subprocess
import tarfile
import tempfile

ROOT=Path(__file__).resolve().parent
MODULES='accounts automation broker classify clis commands conversation_settings doctor enrich file_service host_connector hosts indexer live open_destinations pipeline preview_server projects remote_files remote_sync search_api search_chats server session_lineage session_start_refresh stream usage_history settings_insights workspace_groups project_review'.split()
EXACT=['README.md','LICENSE','docs/RELEASE.md','.gitignore','web/settings-insights.js','web/settings-insights.css',*(name+'.py' for name in MODULES),'install.sh','run.sh','package_workspace.py','start_project_map.py','data/config.example.json','workbench/package.json','workbench/package-lock.json','workbench/tsconfig.json','workbench/vite.config.ts','workbench/index.html']
SOURCE_SUFFIXES={'.ts','.tsx','.css','.svg','.d.ts','.json'}



def bundled_files(root):
    """Follow the current HTML/chunk graph; old build hashes never ship."""
    base=root/'web/workbench';assets={p.name:p for p in (base/'assets').rglob('*') if p.is_file() and not p.name.endswith('.map')}
    seen=set();queue=[base/'index.html']
    tokens=re.compile(r'[A-Za-z0-9_.-]+\.(?:m?js|css|woff2?|ttf|svg|wasm)')
    while queue:
        path=queue.pop()
        if path in seen:continue
        seen.add(path)
        if path.suffix in ('.html','.js','.mjs','.css'):
            for name in tokens.findall(path.read_text(errors='replace')):
                if name in assets and assets[name] not in seen:queue.append(assets[name])
    seen.update(p for p in (base/'pdfjs').rglob('*') if p.is_file())
    return seen


def files_to_ship(root=ROOT):
    names=list(EXACT)
    names += [str(p.relative_to(root)) for p in (root/'workbench/src').rglob('*') if p.is_file() and p.suffix in SOURCE_SUFFIXES]
    names += [str(p.relative_to(root)) for p in bundled_files(root)]
    extension_files = ['package.py','bundle_project_map.py','project-map.cjs','package.json','extension.js','core.js','README.md','media/panel.js','media/state.js','media/run-phase.js','media/native-commands.json','media/panel.css','media/markdown-it.min.js','media/purify.min.js','media/markdown-it.LICENSE','media/dompurify.LICENSE']
    extension_files += ['media/project-map/'+name for name in ['index.html','style.css','markdown-it.min.js','purify.min.js','graph_state.js','content.js','layout.js','display.js','bridge.js','app.js','bundle.json']]
    names += ['vscode-library/'+name for name in extension_files]
    names += ['project-map/'+name+'.py' for name in ['server','graph_data','project_graph','consultations','activity_filter']]
    names += ['project-map/web/'+name for name in ['index.html','style.css','markdown-it.min.js','purify.min.js','graph_state.js','content.js','layout.js','display.js','bridge.js','app.js']]
    for optional in ['docs/AGENT-ONBOARDING.md']:
        if (root/optional).is_file():names.append(optional)
    for name in sorted(set(names)):
        path=root/name
        if not path.is_file() or path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
            raise ValueError('Missing or unsafe allowlisted file: '+name)
        yield name,path


def third_party_licenses():
    lock=json.loads((ROOT/'workbench/package-lock.json').read_text())
    blocks=[]
    for name,meta in sorted(lock.get('packages',{}).items()):
        if not name.startswith('node_modules/') or meta.get('dev'):continue
        if meta.get('optional') and name.startswith('node_modules/@napi-rs/canvas-'):
            continue  # Native PDF server accelerators are never part of the browser build.
        directory=ROOT/'workbench'/name
        if not directory.exists() and meta.get('optional'):
            continue  # Platform-specific optional code is absent and is not bundled.
        files=[p for p in directory.glob('*') if p.is_file() and re.match(r'(?i)^(licen[sc]e|copying)(?:[.-].*)?$',p.name)]
        if not files:
            raise ValueError('Install locked frontend dependencies before packaging: npm ci in workbench/ (missing license for '+name+')')
        title=name.removeprefix('node_modules/')+' '+str(meta.get('version',''))+' ('+str(meta.get('license','see upstream'))+')'
        blocks.append(title+'\n'+'\n'.join(p.read_text(errors='replace') for p in files))
    return '\n\n'.join(blocks).encode()


def privacy_scan(entries):
    findings=[]
    patterns={
      'personal_home':re.compile(rb'/(?:Users|home)/(?!example(?:/|\b)|user(?:/|\b))[-\w.]+'),
      'credential_material':re.compile(rb'(?:sk-(?:proj-|ant-)?[A-Za-z0-9_-]{24,}|-----BEGIN (?:RSA |OPENSSH |EC )?PRIVATE KEY-----|Bearer [A-Za-z0-9_.-]{30,})'),
      'email_identity':re.compile(rb'''(?<=[\s"'<>])[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,24}(?=[\s"'<>])'''),
    }
    for name,raw in entries:
        if Path(name).suffix.lower() in {'.woff','.woff2','.ttf','.png','.jpg','.jpeg','.gif','.ico','.pdf'}:continue
        for category,pattern in patterns.items():
            scanned=raw.replace(b'/home/'+b'tables',b'/example/tables') if category=='personal_home' and Path(name).name.startswith('ts.worker-') else raw
            matches=list(pattern.finditer(scanned))
            if category=='email_identity' and name=='THIRD-PARTY-LICENSES.txt':matches=[]
            if category=='email_identity':matches=[m for m in matches if m.group().split(b'@')[-1] not in (b'example.com',b'example.org',b'example.net') and all(part and not part.startswith(b'-') and not part.endswith(b'-') for part in m.group().split(b'@')[-1].split(b'.'))]
            if matches:findings.append({'path':name,'category':category,'matches':len(matches)})
    return findings


def package(output=None,build=False):
    if build:subprocess.run(['npm','run','build'],cwd=ROOT/'workbench',check=True,stdout=subprocess.DEVNULL)
    entries=[(name,path.read_bytes()) for name,path in files_to_ship()]
    entries=[(name,raw.replace(b'npm test\n',b'# Development tests are excluded from this release archive.\n').replace(b'The original Library remains at `/`.',b'The share archive opens Workspace from `/` as well.')) if name=='docs/AGENT-ONBOARDING.md' else (name,raw) for name,raw in entries]
    entries.append(('skill/SKILL.md',b'''---
name: chat-dashboard
description: Install and operate this user's private local Library Workspace.
---

Read {{TOOL_ROOT}}/README.md and {{TOOL_ROOT}}/docs/AGENT-ONBOARDING.md.
Preserve existing private configuration, transcripts and curated projects.
Use Python 3.11+ and parse-only indexing for installation. Automatic model labeling requires explicit user opt-in.
Do not send paid chat messages, stop sessions, delete transcripts, or expose the local server publicly during installation QA.
Use native provider profile IDs for account routing; never substitute provider UUIDs or infer account ownership from shared transcript directories.
Before restarting a running service, inspect /api/runs and data/execution/ for active work.
'''))
    if not any(name=='web/workbench/index.html' for name,_ in entries):raise ValueError('Build Workbench assets before packaging')
    entries += [('THIRD-PARTY-LICENSES.txt',third_party_licenses()),('web/index.html',b'<!doctype html><meta charset="utf-8"><meta http-equiv="refresh" content="0;url=/workbench"><title>Library Workspace</title><a href="/workbench">Open Workspace</a>')]
    findings=privacy_scan(entries)
    if findings:raise ValueError('Package privacy scan failed: '+json.dumps(findings))
    stamp=datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    out=Path(output) if output else ROOT/'output'/('workspace-package-'+stamp)
    out.mkdir(parents=True,exist_ok=False)
    manifest={'format':1,'created_utc':stamp,'policy':'Explicit backend/source/asset allowlist; no installed user data, profiles, transcripts, QA fixtures or local reports.','files':[{'path':name,'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest()} for name,raw in sorted(entries)]}
    scan={'ok':True,'files_scanned':len(entries),'findings':findings,'scope':'Detects absolute personal homes, known personal identities and credential-shaped values. An automated scan is not a guarantee that arbitrary private text cannot exist.'}
    entries += [('PACKAGE-INVENTORY.json',json.dumps(manifest,indent=2).encode()),('PRIVACY-SCAN.json',json.dumps(scan,indent=2).encode())]
    archive=out/'library-workspace.tar.gz'
    import io
    with tarfile.open(archive,'w:gz') as tf:
        for name,raw in sorted(entries):
            info=tarfile.TarInfo('library-workspace/'+name);info.size=len(raw);info.mode=0o755 if name.endswith('.sh') else 0o644
            tf.addfile(info,io.BytesIO(raw))
    (out/'PACKAGE-INVENTORY.json').write_text(json.dumps(manifest,indent=2)+'\n')
    (out/'PRIVACY-SCAN.json').write_text(json.dumps(scan,indent=2)+'\n')
    return archive


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--build',action='store_true');parser.add_argument('--output',help='New output directory; must not already exist')
    args=parser.parse_args();print(package(args.output,args.build))
