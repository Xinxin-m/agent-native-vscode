import {describe,it,expect} from 'vitest';
import {readFileSync} from 'node:fs';
import {composerModels} from './modelCatalog';
// Shape of the server list: exact versions from the tiers file, then the CLI aliases.
const claude=[{id:'claude-fable-9-1',name:'Fable 9.1'},{id:'claude-opus-9-5',name:'Opus 9.5'},{id:'claude-sonnet-9-5',name:'Sonnet 9.5'},{id:'',name:'Session default'},
 {id:'opus',name:'Opus (CLI alias)'},{id:'opus[1m]',name:'Opus (1M context)'},{id:'sonnet',name:'Sonnet (CLI alias)'},{id:'sonnet[1m]',name:'Sonnet (1M context)'},{id:'haiku',name:'Haiku (CLI alias)'},{id:'fable',name:'Fable (CLI alias)'}];
const labels=(models:any[])=>models.filter(m=>m.id).map(m=>m.label);
describe('composer model choices',()=>{
 it('shows each model once and puts the 1M variant after its base',()=>{
  expect(labels(composerModels('claude',claude))).toEqual(['Fable 9.1','Opus 9.5','Opus 9.5 (1M context)','Sonnet 9.5','Sonnet 9.5 (1M context)','Haiku (CLI alias)']);
 });
 it('keeps a saved alias as the selected value instead of adding a duplicate row',()=>{
  const models=composerModels('claude',claude,'opus');
  expect(models.filter(m=>m.label==='Opus 9.5').map(m=>m.id)).toEqual(['opus']);
  expect(new Set(labels(models)).size).toBe(labels(models).length);
 });
 it('keeps distinct Codex models unchanged in order',()=>{
  expect(composerModels('codex',[{id:'',name:'Session default'},{id:'codex-a',name:'A'},{id:'codex-b',name:'B'}]).map(m=>m.id)).toEqual(['','codex-a','codex-b']);
 });
 it('hard-codes no model id',()=>{
  expect(readFileSync(new URL('./modelCatalog.ts',import.meta.url),'utf8')).not.toMatch(/claude-[a-z]+-\d/);
 });
});
