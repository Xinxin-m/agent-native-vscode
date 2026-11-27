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

REPO=Path(__file__).resolve().parent
if REPO.name == "scripts":
    REPO=REPO.parent
ROOT=REPO/"app" if (REPO/"app/server.py").is_file() else REPO
MODULES='pools project_suggestions label_model json_cache session_reference sessions_cli attention_watch quota_failover auth_failure auth_recovery accounts automation broker classify clis commands conversation_settings doctor enrich file_service cross_host_transfer notices host_connector host_models model_updates hosts indexer live open_destinations pipeline preview_server projects remote_files remote_sync search_api search_chats server session_lineage session_start_refresh stream usage_history settings_insights workspace_groups project_review provider_host agent_activity autoclassify debug_agent live_library live_refresh notifications sidebar'.split()
EXACT=['README.md','LICENSE','docs/RELEASE.md','.gitignore','web/settings-insights.js','web/settings-insights.css',*(name+'.py' for name in MODULES),'install.sh','scripts/install_daily_label.sh','scripts/prune_bundles.py','scripts/restart_server.py','scripts/check_one_badge.py','scripts/ccs_pool_state.cjs','run.sh','package_workspace.py','start_project_map.py','data/config.example.json','workbench/package.json','workbench/package-lock.json','workbench/tsconfig.json','workbench/vite.config.ts','workbench/index.html']
TEST_HELPERS={'accounts_guard.py','enrich_fixture.py'}
TESTS_NOT_SHIPPED={'test_privacy_scan.py','test_workspace_package.py','test_sent_messages.py'}  # the first two exercise this private-tree packager; the third reads a real local transcript
SOURCE_SUFFIXES={'.js','.ts','.tsx','.css','.svg','.d.ts','.json'}



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
    extension_files = ['package.py','bundle_project_map.py','artifact-preview.cjs','live-document.cjs','project-map.cjs','package.json','extension.js','core.js','README.md','media/panel.js','media/auth-recovery.js','media/notification-center.js','media/notification-center.css','media/session-reference-menu.js','media/state.js','media/run-phase.js','media/resume-cost.js','media/native-commands.json','media/panel.css','media/markdown-it.min.js','media/purify.min.js','media/markdown-it.LICENSE','media/dompurify.LICENSE']
    extension_files += ['media/preview/'+name for name in ('katex.min.js','auto-render.min.js','runtime.js','katex.LICENSE')]
    extension_files += ['media/project-map/'+name for name in ['index.html','style.css','markdown-it.min.js','purify.min.js','graph_state.js','content.js','layout.js','display.js','bridge.js','app.js','bundle.json']]
    names += ['vscode-library/'+name for name in extension_files]
    names += ['project-map/'+name+'.py' for name in ['server','graph_data','project_graph','consultations','activity_filter']]
    names += ['project-map/web/'+name for name in ['index.html','style.css','markdown-it.min.js','purify.min.js','graph_state.js','content.js','layout.js','display.js','bridge.js','app.js']]
    for optional in ['docs/AGENT-ONBOARDING.md','CONTRIBUTING.md']:
        if ((REPO if root == ROOT else root)/optional).is_file():names.append(optional)
    names += ['AGENTS.md','CLAUDE.md']  # public agent instructions; the flat development tree keeps them in release/
    names += [str(p.relative_to(REPO if root == ROOT else root)) for p in sorted(((REPO if root == ROOT else root)/'docs/screenshots').glob('*.png'))]  # README images, synthetic data only
    for folder in ('prompts','agents'):  # shared agent harness: top-level Markdown folders
        base=REPO if root == ROOT else root
        names += [str(p.relative_to(base)) for p in sorted((base/folder).glob('*.md'))]
    names += [str(p.relative_to(base)) for p in sorted((base/'scripts/harness').glob('*')) if p.suffix in ('.py','.md','.json')]  # harness scripts and their tests
    names += [str(p.relative_to(root)) for p in sorted((root/'tests').glob('*.py')) if (p.name.startswith('test_') or p.name in TEST_HELPERS) and p.name not in TESTS_NOT_SHIPPED]
    for name in sorted(set(names)):
        path=(REPO/name) if root == ROOT and (name in {"README.md","CONTRIBUTING.md","AGENTS.md","CLAUDE.md","LICENSE","docs/RELEASE.md","docs/AGENT-ONBOARDING.md",".gitignore","package_workspace.py"} or name.startswith(("prompts/","agents/","docs/screenshots/","scripts/harness/"))) else root/name
        if name == "package_workspace.py" and ROOT != REPO:
            path=REPO/"scripts/package_workspace.py"
        if name in ("AGENTS.md","CLAUDE.md") and ROOT == REPO:
            path=REPO/"release"/name  # root AGENTS.md/CLAUDE.md here are private development files
        if not path.is_file() or path.is_symlink() or not path.resolve().is_relative_to((REPO if root == ROOT else root).resolve()):
            raise ValueError('Missing or unsafe allowlisted file: '+name)
        yield name,path


# Private defaults that the shared tree reads from the environment instead, as regex rules so
# this file carries no private text. A rule applies only while the private text is present;
# the privacy scan still gates the result.
SANITIZE={
 'debug_agent.py':[
  (r"^RULES = '/[^']*'$","RULES = os.environ.get('LIBRARY_MONITOR_RULES', str(Path.home() / '.library-monitor' / 'monitor' / 'findings.md'))"),
  (r"^POLICY = '/(?:Users|home)/[^']*/\.claude/hooks/approval_policy\.py'$","POLICY = str(Path.home() / '.claude' / 'hooks' / 'approval_policy.py')")],
 'notifications.py':[
  (r"^MONITOR = '/(?:Users|home)/[^']*'$","MONITOR = os.environ.get('LIBRARY_MONITOR_DIR', str(Path.home() / '.library-monitor'))"),
  (r"^(MONITOR_URL = .*)$",r"\1\nOWNER_STATE = os.environ.get('LIBRARY_MONITOR_OWNER_STATE', 'for_owner')  # monitor card state that means "+'"waiting on the owner"'),
  (r"card\.get\('state'\) == 'for_[a-z]+'","card.get('state') == OWNER_STATE")],
 'tests/test_notifications.py':[(r"'for_(?!owner')[a-z]+'","'for_owner'")]}


def sanitized(name,raw):
    if name not in SANITIZE:return raw
    text=raw.decode()
    for pattern,replacement in SANITIZE.get(name,()):
        text=re.sub(pattern,replacement,text,flags=re.M)
    return text.encode()


DESKTOP_EXPORT=REPO.parent/'library-code-oss/integration/export_source.py'


def desktop_files():
    """The regular Library Code desktop source, from its own export allowlist when the
    sibling checkout exists, else the desktop/ folder of a public checkout."""
    if ROOT == REPO and DESKTOP_EXPORT.is_file():
        import importlib.util
        spec=importlib.util.spec_from_file_location('export_source',DESKTOP_EXPORT)
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        return [('desktop/'+name,raw) for name,raw in sorted(module.collect(DESKTOP_EXPORT.parents[1]).items())]
    base=REPO/'desktop'
    skip={'vscode','output','node_modules'}
    return [(str(p.relative_to(REPO)),p.read_bytes()) for p in sorted(base.rglob('*')) if p.is_file()
            and not any(part in skip or part.startswith(('VSCode-','.')) for part in p.relative_to(base).parts)] if base.is_dir() else []


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


DENYLIST=ROOT/'data/privacy-denylist.txt'  # private, one term per line; data/ never ships
BINARY={'.woff','.woff2','.ttf','.png','.jpg','.jpeg','.gif','.ico','.pdf'}
PATTERNS={
  'personal_home':re.compile(rb'/(?:Users|home)/(?!example(?:/|\b)|user(?:/|\b)|<)[-\w.]+'),
  'credential_material':re.compile(rb'(?:sk-(?:proj-|ant-)?[A-Za-z0-9_-]{24,}|-----BEGIN (?:RSA |OPENSSH |EC )?PRIVATE KEY-----|Bearer [A-Za-z0-9_.-]{30,}|\bAKIA[0-9A-Z]{16}\b|\bgh[pousr]_[A-Za-z0-9]{36,}|\bxox[abposr]-[A-Za-z0-9-]{10,}|\bAIza[0-9A-Za-z_-]{35}\b)'),
  'email_identity':re.compile(rb'(?i)(?<![\w.%+-])[a-z0-9._%+-]+@(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+(?:com|org|net|edu|gov|io|ai|dev|app|co|me|us|uk|ca|de|fr|ch|cn|jp|hk|sg|in|info|biz|xyz)(?![\w-])'),
  'phone_number':re.compile(rb'(?<![\w.+-])(?:\+\d{1,3}[ .-]?)?(?:\(\d{3}\) ?|\d{3}[ .-])\d{3}[ .-]\d{4}(?![\w.-])'),
  'edu_hostname':re.compile(rb'(?i)(?<![\w.-])(?:[a-z0-9-]+\.)+edu(?![\w-])'),
}
ALLOWED_EMAIL_DOMAINS=(b'example.com',b'example.org',b'example.net',b'users.noreply.github.com')


def denylist_terms(path=None):
    try:lines=Path(path or DENYLIST).read_text().splitlines()
    except OSError:return []
    return [line.strip() for line in lines if line.strip() and not line.lstrip().startswith('#')]


def privacy_scan(entries,terms=None):
    """Return one finding per hit: path, line, category and the matched text."""
    terms=denylist_terms() if terms is None else terms
    # A term written as =Word matches that exact case only (for words common in lowercase).
    deny=[re.compile((rb'' if term.startswith('=') else rb'(?i)')+rb'(?<![A-Za-z0-9])'+re.escape(term.removeprefix('=').encode())+rb'(?![A-Za-z0-9])') for term in terms]
    findings=[]
    def hit(name,raw,category,match):
        findings.append({'path':name,'line':raw.count(b'\n',0,match.start())+1,'category':category,'match':match.group().decode(errors='replace')[:80]})
    for name,raw in entries:
        for pattern in deny:
            for match in pattern.finditer(name.encode()):hit(name,b'',  'denylist_filename',match)
        if Path(name).suffix.lower() in BINARY:continue
        for pattern in deny:
            for match in pattern.finditer(raw):hit(name,raw,'denylist',match)
        for category,pattern in PATTERNS.items():
            scanned=raw.replace(b'/home/'+b'tables',b'/example/tables') if category=='personal_home' and Path(name).name.startswith('ts.worker-') else raw
            for match in pattern.finditer(scanned):
                text=match.group().lower()
                if category=='email_identity' and (name in ('THIRD-PARTY-LICENSES.txt','desktop/upstream-ThirdPartyNotices.txt') or text.split(b'@')[-1].endswith(ALLOWED_EMAIL_DOMAINS)):continue
                if category=='edu_hostname' and text.endswith(b'example.edu'):continue
                hit(name,scanned,category,match)
    return findings


def package(output=None,build=False):
    if build:subprocess.run(['npm','run','build'],cwd=ROOT/'workbench',check=True,stdout=subprocess.DEVNULL)
    entries=[(name,sanitized(name,path.read_bytes())) for name,path in files_to_ship()]+desktop_files()
    entries.append(('skill/SKILL.md',b'''---
name: chat-dashboard
description: Install and operate a local Library Workspace and keep its data private.
---

Read {{TOOL_ROOT}}/../README.md and {{TOOL_ROOT}}/../docs/AGENT-ONBOARDING.md.
Preserve existing private configuration, transcripts and curated projects.
Use Python 3.11+ and parse-only indexing for installation. Automatic model labeling requires explicit user opt-in.
Do not send paid chat messages, stop sessions, delete transcripts, or expose the local server publicly during installation QA.
Use native provider profile IDs for account routing; never substitute provider UUIDs or infer account ownership from shared transcript directories.
Before restarting a running service, inspect /api/runs and data/execution/ for active work.
'''))
    # A test that mentions private names, hosts or paths stays in the private tree.
    leaky={f['path'] for f in privacy_scan([e for e in entries if e[0].startswith('tests/')])}
    excluded_tests=sorted(leaky)
    entries=[e for e in entries if e[0] not in leaky]
    if not any(name=='web/workbench/index.html' for name,_ in entries):raise ValueError('Build Workbench assets before packaging')
    entries += [('THIRD-PARTY-LICENSES.txt',third_party_licenses()),('web/index.html',b'<!doctype html><meta charset="utf-8"><meta http-equiv="refresh" content="0;url=/workbench"><title>Library Workspace</title><a href="/workbench">Open Workspace</a>')]
    # Keep the runnable tree cohesive: its modules resolve data/assets relative to
    # themselves. Public entry points are tiny wrappers; no import rewriting.
    arranged=[]
    for name,raw in entries:
        if name == 'package_workspace.py':
            arranged.append(('scripts/'+name,raw))
        elif name in ('README.md','CONTRIBUTING.md','AGENTS.md','CLAUDE.md','LICENSE','.gitignore','THIRD-PARTY-LICENSES.txt') or name.startswith(('docs/','prompts/','agents/','scripts/harness/','desktop/')):
            arranged.append((name,raw))
        else:
            arranged.append(('app/'+name,raw))
    for script in ('install.sh','run.sh'):
        wrapper='''#!/usr/bin/env bash
set -euo pipefail
LIBRARY_REPO="$(cd "$(dirname "$0")" && pwd)"
if [[ -d "$LIBRARY_REPO/data" ]] && [[ -n "$(find "$LIBRARY_REPO/data" -mindepth 1 ! -name config.example.json -print -quit)" ]]; then
  echo "Legacy root/data detected. Stop here to preserve your library. Follow docs/AGENT-ONBOARDING.md: Upgrading from the flat layout." >&2
  exit 2
fi
exec bash "$LIBRARY_REPO/app/'''+script+'" "$@"\n'
        arranged.append((script,wrapper.encode()))
    entries=arranged
    findings=privacy_scan(entries)
    if findings:raise ValueError('Package privacy scan failed:\n'+'\n'.join(f"{f['path']}:{f['line']}: {f['category']}: {f['match']}" for f in findings))
    stamp=datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    out=Path(output) if output else ROOT/'output'/('workspace-package-'+stamp)
    out.mkdir(parents=True,exist_ok=False)
    manifest={'format':1,'created_utc':stamp,'policy':'Explicit backend/source/asset allowlist; no installed user data, profiles, transcripts, QA fixtures or local reports.','files':[{'path':name,'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest()} for name,raw in sorted(entries)]}
    scan={'ok':True,'files_scanned':len(entries),'denylist_terms':len(denylist_terms()),'findings':findings,'tests_withheld_for_private_text':excluded_tests,'scope':'Detects private deny-list terms, personal home paths, emails, phone numbers, .edu hostnames and credential-shaped values. An automated scan is not a guarantee that arbitrary private text cannot exist.'}
    # Inventory and scan stay beside the archive, outside the shipped tree.
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
