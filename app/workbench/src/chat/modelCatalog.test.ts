import {describe,it,expect} from 'vitest';
import {readFileSync} from 'node:fs';
import {composerModels} from './modelCatalog';
describe('composer model choices',()=>{
 it('keeps the server order and labels CLI aliases',()=>{
  const models=composerModels('claude',[{id:'claude-x-1',name:'X 1'},{id:'',name:'Session default'},{id:'opus',name:'Opus'},{id:'custom-model',name:'Custom'}]);
  expect(models.map(m=>m.id)).toEqual(['claude-x-1','','opus','custom-model']);
  expect(models.find(m=>m.id==='opus')?.label).toBe('Opus (CLI alias)');
 });
 it('keeps the advertised Codex catalog unchanged',()=>{
  const models=[{id:'codex-test',name:'Test'}];expect(composerModels('codex',models)).toBe(models);
 });
 it('hard-codes no model id',()=>{
  expect(readFileSync(new URL('./modelCatalog.ts',import.meta.url),'utf8')).not.toMatch(/claude-[a-z]+-\d/);
 });
});
