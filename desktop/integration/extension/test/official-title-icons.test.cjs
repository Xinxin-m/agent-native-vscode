const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs'),os=require('node:os'),path=require('node:path');
const core=path.join(__dirname,'../../../vscode/src/vs/workbench/services/actions/common/menusExtensionPoint.ts');
const source=fs.readFileSync(core,'utf8');
const commands=fs.readFileSync(path.join(__dirname,'../../../vscode/src/vs/workbench/services/commands/common/commandService.ts'),'utf8');
test('both builds preserve vendor title icons and diff actions',()=>{
 assert.doesNotMatch(source,/ContextKeyExpr\.notEquals\('config\.library\.chatEngine', 'official'\)/);
 assert.match(source,/item.when = ContextKeyExpr.deserialize\(menuItem.when\)/);
});
test('every installed provider title chat icon routes through current-pane new chat handling',t=>{
 const root=path.join(os.homedir(),'.vscode/extensions');
 const dirs=fs.existsSync(root)?fs.readdirSync(root).filter(d=>/^(anthropic\.claude-code|openai\.chatgpt)-/.test(d)):[];
 if(!dirs.length)return t.skip('official extensions not installed');
 for(const dir of dirs){
  const manifest=JSON.parse(fs.readFileSync(path.join(root,dir,'package.json'),'utf8'));
  for(const item of manifest.contributes?.menus?.['editor/title']||[]){
   if((item.group||'').split('@')[0]!=='navigation'||/(?:ProposedDiff|resolveMergeConflicts)$/.test(item.command))continue;
   assert.ok(commands.includes("['"+item.command+"',"),dir+' '+item.command+' must route to the current pane');
  }
 }
});
