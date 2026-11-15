import {describe,it,expect} from 'vitest';
import {composerModels,currentClaudeModels} from './modelCatalog';
describe('current Claude model choices',()=>{
 it('shows exact current versions even while an older broker remains running',()=>{
  const models=composerModels('claude',[{id:'',name:'Default'},{id:'opus',name:'Opus 5'},{id:'custom-model',name:'Custom'}]);
  expect(models.slice(1,5).map(m=>m.id)).toEqual(currentClaudeModels.map(m=>m.id));
  expect(models.find(m=>m.id==='opus')?.label).toBe('Opus (CLI alias)');
  expect(models.some(m=>m.id==='custom-model')).toBe(true);
 });
 it('preserves native metadata and avoids duplicate explicit choices',()=>{
  const models=composerModels('claude',[{id:'claude-opus-5-5',name:'Old label',efforts:['high']}]);
  expect(models.filter(m=>m.id==='claude-opus-5-5')).toHaveLength(1);
  expect(models[0].name).toBe('Opus 5.5');
  expect(models[0].efforts).toEqual(['high']);
 });
 it('keeps the advertised Codex catalog unchanged',()=>{
  const models=[{id:'codex-test',name:'Test'}];expect(composerModels('codex',models)).toBe(models);
 });
});
