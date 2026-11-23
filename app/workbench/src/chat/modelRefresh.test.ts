import {describe,it,expect} from 'vitest';
import {describeModelRefresh,knownModels,versionLine} from './modelRefresh';
const tool=(over:any={})=>({version_before:'1.0.0',version_after:'1.0.0',latest:'1.0.0',update_available:false,new_models:[],...over});
describe('model update text',()=>{
 it('lists new models, versions and offers install only when a CLI is behind',()=>{
  const text=describeModelRefresh({claude:tool({latest:'1.0.2',update_available:true,new_models:[{id:'a',name:'Fable 9.2'}]}),codex:tool(),checked_at:'2026-01-01T00:00:00Z',tiers_stale:'x is newer than y in the tiers file'});
  expect(text.models).toBe('New models: Fable 9.2');
  expect(text.versions).toEqual(['Claude Code 1.0.0, 1.0.2 available','Codex 1.0.0, up to date']);
  expect(text.changes).toEqual(['Claude Code 1.0.0, 1.0.2 available']);
  expect(text.canInstall).toBe(true);
  expect(text.notes).toEqual(['Tiers file: x is newer than y in the tiers file']);
 });
 it('reports a skipped or finished install without offering it again',()=>{
  const skipped=describeModelRefresh({claude:tool({update_available:true}),codex:tool(),checked_at:'',install_skipped:'1 active run(s) in the broker; nothing installed.'});
  expect(skipped.canInstall).toBe(false);
  expect(skipped.changes.at(-1)).toBe('1 active run(s) in the broker; nothing installed');
  const done=tool({update_available:true,version_after:'1.0.2',install:{ok:true,output:''}});
  expect(versionLine('claude',done)).toBe('Claude Code updated from 1.0.0 to 1.0.2');
  expect(versionLine('codex',tool({update_available:true,install:{ok:false,output:'npm ERR\nEACCES\n'}}))).toBe('Codex update failed: EACCES');
  expect(describeModelRefresh({claude:done,codex:tool(),checked_at:''}).canInstall).toBe(false);
 });
 it('sends the ids the client already shows',()=>{
  expect(knownModels({claude:{models:[{id:''},{id:'opus'}]}})).toEqual({claude:['','opus'],codex:[]});
 });
});
