'use strict';
const test=require('node:test');const assert=require('node:assert/strict');
const {projectGrouping}=require('../tab-project-groups.cjs');
const vscode={Uri:{file:path=>({toString:()=>`file://${path}`}),from:({scheme,authority,path})=>({toString:()=>`${scheme}://${authority}${path}`})}};
test('maps project chats and local and remote workspace paths without guessing unrelated roots',()=>{
 const data=projectGrouping(vscode,{projects:{assignments:{chat:'p'}}},{projects:[{id:'p',name:'Project',paths:[{host:'mac',path:'/research'},{host:'sc',path:'/work/project'}]}]},[{id:'root1',host_id:'sc',path:'/work'},{id:'other',host_id:'mac',path:'/work'}],true);
 assert.deepEqual(data,{assignments:{chat:'p'},projects:[{id:'p',name:'Project',prefixes:['file:///research','library-file://root1/project']}],includeFiles:true,providers:{}});
});

test('remote project folder can contain the registered file root',()=>{
 const result=projectGrouping(vscode,{projects:{assignments:{}}},{projects:[{id:'p',name:'P',paths:[{host:'sc',path:'/work'}]}]},[{id:'child',host_id:'sc',path:'/work/deep'}],true);
 assert.deepEqual(result.projects[0].prefixes,['library-file://child/']);
});


test('infers unassigned project from deepest known workspace and keeps provider fallback without inference calls',()=>{
 const result=projectGrouping(vscode,{chats:[{key:'a',source:'codex',cwd:'/work/project/deep'},{key:'b',source:'claude',cwd:'/elsewhere'}],projects:{assignments:{}}},{projects:[{id:'p',name:'P',paths:[{host:'mac',path:'/work/project'}]}]},[],true);
 assert.equal(result.assignments.a,'p');assert.equal(result.assignments.b,undefined);assert.deepEqual(result.providers,{a:'codex',b:'claude'});
});
