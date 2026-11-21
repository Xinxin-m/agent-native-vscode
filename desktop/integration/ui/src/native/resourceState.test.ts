import {it,expect} from 'vitest';import {resourceState} from './resourceState';
it('pane presets use the current draft provider without changing stable tab identity',()=>{
 const resource={kind:'chat',draftId:'a',meta:{source:'claude'}};
 const result=resourceState(resource,{'library.workbench.conversation.draft:a':JSON.stringify({record:{id:'draft:a',provider:'codex',nativeKey:'codex-b'}})});
 expect(result.draftId).toBe('a');expect(result.meta.source).toBe('codex');expect(result.meta.nativeKey).toBe('codex-b');expect(resource.meta.source).toBe('claude');
});
it('a Files tab restores with the workspace chosen in it',()=>{
 const resource={kind:'files',root_id:'',title:'Files'};
 expect(resourceState(resource,{'library.files.root':JSON.stringify({root_id:'file:///w/a',title:'a'})})).toEqual({kind:'files',root_id:'file:///w/a',title:'a'});
 expect(resourceState(resource,{})).toBe(resource);
});
