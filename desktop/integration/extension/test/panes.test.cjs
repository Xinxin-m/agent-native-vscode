const {test}=require('node:test');const assert=require('node:assert/strict');
let panes;try{panes=require('../panes.cjs')}catch{}
test('automatic previews cap at three groups and reuse the final group',()=>{
 assert.ok(panes,'pane policy exists');
 assert.equal(panes.targetColumn([1],1,'right',3),2);
 assert.equal(panes.targetColumn([1,2],2,'right',3),3);
 assert.equal(panes.targetColumn([1,2,3],1,'right',3),3);
 assert.equal(panes.targetColumn([1,2,3],2,'preview',3),2);
 assert.equal(panes.targetColumn([1],1,'right',1),1);
});
test('identity deduplicates conversations, separates explicit forks and remote files',()=>{
 assert.ok(panes,'pane policy exists');
 assert.equal(panes.resourceKey({kind:'chat',key:'codex-one',title:'Old'}),panes.resourceKey({kind:'chat',key:'codex-one',title:'New'}));
 assert.notEqual(panes.resourceKey({kind:'chat',draftId:'a'}),panes.resourceKey({kind:'chat',draftId:'b'}));
 assert.notEqual(panes.resourceKey({kind:'file',host_id:'mac',path:'/a'}),panes.resourceKey({kind:'file',host_id:'cluster',path:'/a'}));
});
test('one Files tab per workspace: opening the same workspace again finds the open tab',()=>{
 assert.equal(panes.resourceKey({kind:'files',root_id:'file:///w/a',title:'a'}),panes.resourceKey({kind:'files',root_id:'file:///w/a',title:'renamed'}));
 assert.notEqual(panes.resourceKey({kind:'files',root_id:'file:///w/a'}),panes.resourceKey({kind:'files',root_id:'file:///w/b'}));
 assert.notEqual(panes.resourceKey({kind:'files',root_id:'all'}),panes.resourceKey({kind:'files',root_id:''}));
});
// Item 50: the pane a PDF opens in; the same 6 cases as workbench tests/pdf-target.test.ts.
const col=(id,i)=>({id,rect:{left:i*304,right:i*304+300,top:0,bottom:800}});
const four=[col('c',2),col('a',0),col('d',3),col('b',1)];
test('a PDF opens in the pane to the right, a new right split, or at 4 panes the pane to the left',()=>{
 assert.deepEqual(panes.pdfTargetPane([col('a',0)],'a'),{split:'right'});
 assert.deepEqual(panes.pdfTargetPane([col('b',1),col('a',0)],'a'),{group:'b'});
 assert.deepEqual(panes.pdfTargetPane([col('a',0),col('b',1)],'b'),{split:'right'});
 assert.deepEqual(panes.pdfTargetPane(four,'d'),{group:'c'});
 assert.deepEqual(panes.pdfTargetPane(four,'b'),{group:'c'});
 const stacked=[col('a',0),col('b',1),{id:'b2',rect:{left:304,right:604,top:404,bottom:800}},{id:'c',rect:{left:608,right:908,top:0,bottom:800}}];
 assert.deepEqual(panes.pdfTargetPane(stacked,'c'),{group:'b'});
});
test('native editor layout becomes pane rectangles in view column order',()=>{
 // vscode.getEditorLayout: orientation 0 = side by side; nested groups alternate direction; leaves in grid order are view columns 1..n.
 const rects=panes.layoutRects({orientation:0,groups:[{size:300},{size:300,groups:[{size:400},{size:400}]},{size:600}]});
 assert.deepEqual(rects.map(g=>g.id),[1,2,3,4]);
 assert.deepEqual(panes.pdfTargetPane(rects,4),{group:2});
 assert.deepEqual(panes.pdfTargetPane(rects,1),{group:2});
 assert.deepEqual(panes.pdfTargetPane(rects,3),{group:4});
 assert.deepEqual(panes.pdfTargetPane(panes.layoutRects({orientation:1,groups:[{},{}]}),1),{split:'right'});
});
test('item 47: Arrange Panes sits at the bottom of the pane "..." menu, below every core group',()=>{
 // Core editor title groups (editor.contribution.ts): 1_diff, 3_open, 5_close, 7_settings, 8_group_operations, 9_configure; menuService sorts groups with localeCompare.
 const group=require('../package.json').contributes.menus['editor/title'].find(m=>m.command==='library.arrange').group.split('@')[0];
 for(const core of ['1_diff','3_open','5_close','7_settings','8_group_operations','9_configure'])assert.ok(group.localeCompare(core)>0,group+' after '+core);
});
