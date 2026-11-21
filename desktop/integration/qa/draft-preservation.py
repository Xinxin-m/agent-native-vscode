"""Capture/check nonempty native conversation drafts without printing their content."""
import hashlib,json,sqlite3,sys
from pathlib import Path
mode,target=sys.argv[1:3]
path=Path(target)
root=Path.home()/'Library/Application Support/Library Code/User/workspaceStorage'
records={}
for database in root.glob('*/state.vscdb'):
    with sqlite3.connect('file:'+str(database)+'?mode=ro',uri=True) as conn:
        row=conn.execute('SELECT value FROM ItemTable WHERE key=?',('library.library-code',)).fetchone()
    if not row: continue
    for draft_key,values in json.loads(row[0]).items():
        if not draft_key.startswith('draft:') or not isinstance(values,dict): continue
        for key,raw in values.items():
            if '.conversation.' not in key: continue
            try: record=json.loads(raw).get('record',{})
            except (TypeError,ValueError): continue
            text=record.get('text','');attachments=record.get('attachments',[])
            if not str(text).strip() and not attachments: continue
            value={'text':text,'attachments':attachments}
            identity=database.parent.name+'|'+draft_key+'|'+key
            records[identity]={'hash':hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False).encode()).hexdigest(),'record':record}
if mode=='capture':
    with path.open('x') as f: json.dump(records,f,indent=2)
    path.chmod(0o600)
    print(json.dumps({'captured':len(records),'path':str(path)}))
elif mode=='check':
    before=json.loads(path.read_text())
    changed=[key for key,value in before.items() if records.get(key,{}).get('hash')!=value['hash']]
    print(json.dumps({'preserved':len(before)-len(changed),'expected':len(before),'changed':len(changed),'additional':len(set(records)-set(before))}))
    if changed: raise SystemExit('Draft contents changed; inspect the private capture before any repair.')
else: raise SystemExit('Use capture or check.')
