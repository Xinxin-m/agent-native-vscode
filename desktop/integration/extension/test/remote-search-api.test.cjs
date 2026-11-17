'use strict';
const {test}=require('node:test'),assert=require('node:assert/strict'),path=require('node:path'),fs=require('node:fs');
const ts=require('../../../vscode/node_modules/typescript'),root=path.resolve(__dirname,'../../..');
test('remote search implementation type-checks against this core checkout API2 declarations',()=>{
 const dts=path.join(root,'vscode/src/vscode-dts'),files=['vscode.d.ts','vscode.proposed.fileSearchProvider2.d.ts','vscode.proposed.textSearchProvider.d.ts','vscode.proposed.textSearchProvider2.d.ts','vscode.proposed.textSearchComplete2.d.ts'].map(name=>path.join(dts,name));
 files.push(path.join(root,'integration/extension/remote-search.cjs'));
 const program=ts.createProgram(files,{allowJs:true,checkJs:true,strict:false,noImplicitAny:false,useUnknownInCatchVariables:false,noEmit:true,skipLibCheck:true,target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.Node16,moduleResolution:ts.ModuleResolutionKind.Node16,types:['node'],typeRoots:[path.join(root,'vscode/node_modules/@types')]});
 const diagnostics=ts.getPreEmitDiagnostics(program).filter(d=>d.category===ts.DiagnosticCategory.Error);
 assert.equal(diagnostics.length,0,ts.formatDiagnosticsWithColorAndContext(diagnostics,{getCurrentDirectory:()=>root,getCanonicalFileName:f=>f,getNewLine:()=>'\n'}));
});
test('built-in manifest grants the API2 proposals and activates searches for only registered roots',()=>{
 const manifest=JSON.parse(fs.readFileSync(path.join(root,'integration/extension/package.json'),'utf8'));
 for(const name of ['fileSearchProvider2','textSearchProvider2','textSearchComplete2'])assert.ok(manifest.enabledApiProposals?.includes(name),'missing proposal '+name);
 assert.ok(manifest.activationEvents.includes('onSearch:library-file'));
 assert.equal(manifest.activationEvents.includes('onSearch:library-reference'),false);
 const product=JSON.parse(fs.readFileSync(path.join(root,'vscode/product.json'),'utf8'));
 const overrides=product.extensionEnabledApiProposals?.[manifest.publisher+'.'+manifest.name];
 if(overrides)for(const proposal of manifest.enabledApiProposals)assert.ok(overrides.includes(proposal),'product override removes '+proposal);
});
