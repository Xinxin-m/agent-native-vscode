"""Copy one allowlisted Project Map implementation into native clients."""
from pathlib import Path
import hashlib,json,shutil,sys,re
ROOT=Path(__file__).resolve().parent
SOURCE=ROOT.parent.parent/'project-session-dashboard'/'web'
ASSETS=('index.html','style.css','markdown-it.min.js','purify.min.js','graph_state.js','content.js','layout.js','display.js','bridge.js','app.js')
def bundle(target):
    target=Path(target)
    source=SOURCE if SOURCE.is_dir() else ROOT/'media/project-map'
    references=re.findall(r'(?:src|href)="([^"]+)"',(source/'index.html').read_text())
    for name in references:
        if name.startswith('data:'): continue
        if name not in ASSETS: raise ValueError('Shared HTML references an unbundled asset: '+name)
    missing=[n for n in ASSETS if not (source/n).is_file()]
    if missing: raise FileNotFoundError('Missing shared map assets: '+', '.join(missing))
    out=target/'media/project-map';out.mkdir(parents=True,exist_ok=True)
    manifest={}
    for name in ASSETS:
        src=source/name
        if src.is_symlink(): raise ValueError('Map assets must be regular source files')
        if src.resolve()!=(out/name).resolve():shutil.copy2(src,out/name)
        manifest[name]=hashlib.sha256(src.read_bytes()).hexdigest()
    (out/'bundle.json').write_text(json.dumps(manifest,indent=2)+'\n')
    host=ROOT/'project-map.cjs'
    if target.resolve()!=ROOT.resolve():shutil.copy2(host,target/host.name)
    return manifest
if __name__=='__main__':
    bundle(Path(sys.argv[1]) if len(sys.argv)>1 else ROOT)
    print('Bundled shared Project Map assets')
