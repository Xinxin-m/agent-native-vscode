#!/usr/bin/env python3
"""Build and stage this client into the complete isolated source fork.

  python3 integration/build.py                     stage the Library extension (the core is packaged separately)
  python3 integration/build.py --variant official  also package the core into output/VSCode-darwin-arm64-official
                                                   and give it the Library Code Official identity
"""
from pathlib import Path
import subprocess,shutil,json,os,argparse,sys
parser=argparse.ArgumentParser();parser.add_argument('--variant',choices=['library','official'],default='library');options=parser.parse_args()
root=Path(__file__).resolve().parents[1]
repo=root/'vscode'
# Ship a checked local snapshot so exported source builds independently.
classifier=root.parent/'chat-dashboard/auth_failure.py'
if classifier.is_file():
 shutil.copy2(classifier,root/'integration/auth_failure.py')
preview_source=root.parent/'chat-dashboard/vscode-library/artifact-preview.cjs'
if preview_source.is_file():
 shutil.copy2(preview_source,root/'integration/extension/artifact-preview.cjs')
# The upstream webview bootstrap uses a pinned CSP hash. Keep it exact after
# native drag/drop and link handling changes; never weaken the script policy.
import re, hashlib, base64
pre=repo/'src/vs/workbench/contrib/webview/browser/pre/index.html'
source=pre.read_text()
script=re.search(r'<script async type="module">([\s\S]*?)</script>',source).group(1)
digest=base64.b64encode(hashlib.sha256(script.encode()).digest()).decode()
pre.write_text(re.sub(r"script-src 'sha256-[^']+'", "script-src 'sha256-"+digest+"'",source))
subprocess.run([str(repo/'build/node_modules/esbuild/bin/esbuild'),str(root/'integration/extension/html-source.mjs'),'--bundle','--platform=node','--format=cjs','--outfile='+str(root/'integration/extension/html-source.cjs')],check=True)
subprocess.run([str(repo/'build/node_modules/esbuild/bin/esbuild'),str(root/'integration/extension/markdown-source.mjs'),'--bundle','--platform=node','--format=cjs','--outfile='+str(root/'integration/extension/markdown-source.cjs')],check=True)
subprocess.run(['npm','run','build'],cwd=root/'integration/ui',check=True)
map_builder=root.parent/'chat-dashboard/vscode-library/bundle_project_map.py'
if map_builder.is_file():
 subprocess.run(['python3',str(map_builder),str(root/'integration/extension')],check=True)
else:
 # Portable source bundles carry only this verified, public-code asset snapshot.
 map_dir=root/'integration/extension/media/project-map'
 manifest=json.loads((map_dir/'bundle.json').read_text())
 for name,expected in manifest.items():
  if Path(name).name!=name or hashlib.sha256((map_dir/name).read_bytes()).hexdigest()!=expected:
   raise RuntimeError('Invalid bundled project map asset: '+name)

# A small, non-private build marker makes the user's running client verifiable.
from datetime import datetime, timezone
inputs=sorted((root/'integration/ui/src').rglob('*'))+sorted((root/'integration/extension').glob('*.cjs'))+[root/'integration/extension/package.json',root/'integration/official-sessions.py',root/'integration/auth_failure.py']
source_hash=hashlib.sha256()
for file in inputs:
 if file.is_file():
  source_hash.update(os.path.relpath(file,root).encode());source_hash.update(file.read_bytes())
source_hash.update(subprocess.check_output(['git','diff','--','src'],cwd=repo))
# New native files are not present in git diff until committed. Include their bytes.
for raw in sorted(subprocess.check_output(['git','ls-files','--others','--exclude-standard','-z','--','src'],cwd=repo).split(b'\0')):
 if raw:
  source_hash.update(raw);source_hash.update((repo/raw.decode()).read_bytes())
(root/'integration/extension/build-info.json').write_text(json.dumps({'id':datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'),'sourceHash':source_hash.hexdigest()})+'\n')
target=repo/'extensions/library-code'
shutil.copytree(root/'integration/extension',target,dirs_exist_ok=True,ignore=shutil.ignore_patterns('test','node_modules'))
# Each UI build adds a new hashed main-*.js; keep only the bundles the entries still load.
pruner=root.parent/'chat-dashboard/scripts/prune_bundles.py'
if pruner.is_file():
 for media in (root/'integration/extension/media',target/'media'):subprocess.run(['python3',str(pruner),str(media)],check=True)
p=repo/'product.json';product=json.loads(p.read_text());product.update(nameShort='Library Code',nameLong='Library Code',applicationName='library-code',dataFolderName='.library-code',sharedDataFolderName='.library-code-shared',darwinBundleIdentifier='local.library.code',urlProtocol='library-code',serverApplicationName='library-code-server',serverDataFolderName='.library-code-server',tunnelApplicationName='library-code-tunnel',win32MutexName='librarycode',linuxDesktopName='local.library.Code',linuxIconName='library-code');p.write_text(json.dumps(product,indent='\t')+'\n')
print('Staged built-in Library client and custom product identity.')
if options.variant!='library':
 # The variant shares the core build; it is packaged to its own folder so the default package stays untouched.
 sys.path.insert(0,str(root/'integration'));import variants
 package=root/variants.VARIANTS[options.variant]['package']
 toolchain=root/'output/toolchain'/('node-v'+(repo/'.nvmrc').read_text().strip()+'-darwin-arm64')/'bin'
 env={**os.environ,'LIBRARY_PACKAGE_DIR':str(package.relative_to(root)),'PATH':os.pathsep.join([str(toolchain),str(root/'output/toolchain/pnpm/node_modules/.bin'),os.environ['PATH']])}
 subprocess.run(['node','--experimental-strip-types','--max-old-space-size=8192','./node_modules/gulp/bin/gulp.js','vscode-darwin-arm64'],cwd=repo,env=env,check=True)
 bundle=package/'Library Code.app'
 variants.apply(bundle,options.variant)
 variants.set_defaults(bundle/'Contents/Resources/app/extensions/library-code',options.variant)
 subprocess.run(['codesign','--force','--deep','--sign','-',str(bundle)],check=True,capture_output=True)
 print('Packaged and signed',options.variant,'candidate:',bundle)
 print('Install: python3 integration/install.py --variant',options.variant,'--install --expected-sha256',subprocess.check_output(['python3',str(root/'integration/install.py'),'--variant',options.variant,'--candidate-hash'],text=True).strip(),'--expected-extension-hash',json.loads((bundle/'Contents/Resources/app/extensions/library-code/build-info.json').read_text())['sourceHash'])
