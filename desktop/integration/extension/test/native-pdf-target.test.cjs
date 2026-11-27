// Item 50 natively: a PDF opened without a destination goes to the pane to the right (new split when none), and an open PDF is focused, not duplicated.
const {test}=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const panes=require('../panes.cjs');
function fixture({layout,active,tabs=[]}){
 const calls=[],groups=layout.groups.map((_,i)=>({viewColumn:i+1,tabs:tabs[i]||[]}));
 const tabGroups={all:groups,activeTabGroup:groups[active-1]};
 const vscode={Uri:{file:p=>({path:p,toString:()=>'file://'+p})},Range:class{},window:{tabGroups},commands:{executeCommand:async(...args)=>{calls.push(args);
  if(args[0]==='vscode.getEditorLayout')return layout;
  if(args[0]==='workbench.action.newGroupRight'){const g={viewColumn:groups.length+1,tabs:[]};groups.push(g);tabGroups.activeTabGroup=g;}}}};
 const scope={preferenceWrites:{settled:Promise.resolve()},vscode,path,preferences:{maxAutoPanes:3,openDefault:'right'},...panes,fs:{stat:async()=>({isFile:()=>true})},filesTab:{},require:()=>({workspaceFor:async()=>{}})};
 const source=fs.readFileSync(path.join(__dirname,'../extension.cjs'),'utf8'),start=source.indexOf(' async function open(resource,destination,explicitColumn){'),end=source.indexOf('\n function connect(',start);
 vm.runInNewContext(source.slice(start,end)+'\nthis.open=open;',scope);
 return {open:scope.open,calls,column:()=>calls.find(c=>c[0]==='vscode.open')[2].viewColumn,split:()=>calls.some(c=>c[0]==='workbench.action.newGroupRight')};
}
const pdf={kind:'file',host_id:'mac',path:'/w/paper.pdf'};
const sideBySide=n=>({orientation:0,groups:Array.from({length:n},()=>({size:1}))});
test('one pane: the PDF opens in a new pane to the right',async()=>{const f=fixture({layout:sideBySide(1),active:1});await f.open(pdf);assert.deepEqual([f.split(),f.column()],[true,2]);});
test('two panes, left current: the PDF opens in the right pane without a split',async()=>{const f=fixture({layout:sideBySide(2),active:1});await f.open(pdf);assert.deepEqual([f.split(),f.column()],[false,2]);});
test('four panes, rightmost current: the PDF opens in the pane to its left',async()=>{const f=fixture({layout:sideBySide(4),active:4});await f.open(pdf);assert.deepEqual([f.split(),f.column()],[false,3]);});
test('an open PDF is focused in its pane, not opened again elsewhere',async()=>{const f=fixture({layout:sideBySide(3),active:3,tabs:[[{input:{uri:{toString:()=>'file:///w/paper.pdf'}}}]]});await f.open(pdf);assert.deepEqual([f.split(),f.column()],[false,1]);});
test('an explicit destination keeps its pane; other files also reuse the adjacent pane',async()=>{
 const a=fixture({layout:sideBySide(1),active:1});await a.open(pdf,'tab');assert.deepEqual([a.split(),a.column()],[false,1]);
 const b=fixture({layout:sideBySide(2),active:1});await b.open({kind:'file',host_id:'mac',path:'/w/notes.md'});assert.deepEqual([b.split(),b.column()],[false,2]);
});
