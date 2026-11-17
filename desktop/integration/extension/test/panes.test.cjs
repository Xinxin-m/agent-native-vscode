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
 assert.notEqual(panes.resourceKey({kind:'file',host_id:'mac',path:'/a'}),panes.resourceKey({kind:'file',host_id:'sc',path:'/a'}));
});
test('one Files tab per workspace: opening the same workspace again finds the open tab',()=>{
 assert.equal(panes.resourceKey({kind:'files',root_id:'file:///w/a',title:'a'}),panes.resourceKey({kind:'files',root_id:'file:///w/a',title:'renamed'}));
 assert.notEqual(panes.resourceKey({kind:'files',root_id:'file:///w/a'}),panes.resourceKey({kind:'files',root_id:'file:///w/b'}));
 assert.notEqual(panes.resourceKey({kind:'files',root_id:'all'}),panes.resourceKey({kind:'files',root_id:''}));
});
