const {test}=require('node:test');
const assert=require('node:assert/strict');
const {run}=require('../advanced-editor.cjs');
test('Settings editor tools dispatch only the fixed native command allowlist',async()=>{
 const calls=[];const vscode={commands:{executeCommand:async id=>{calls.push(id);return 'opened';}}};
 for(const tool of ['settings','keybindings','extensions','terminal','statusBar'])assert.equal(await run(vscode,tool),'opened');
 assert.deepEqual(calls,['workbench.action.openSettings','workbench.action.openGlobalKeybindings','workbench.view.extensions','workbench.action.terminal.toggleTerminal','workbench.action.toggleStatusbarVisibility']);
});
test('Settings editor tools reject arbitrary commands and inherited keys',()=>{
 const vscode={commands:{executeCommand:()=>assert.fail('Unexpected dispatch')}};
 for(const key of ['secondarySidebar','workbench.action.reloadWindow','__proto__','constructor',null,{}])assert.throws(()=>run(vscode,key),/Unknown editor tool/);
});
