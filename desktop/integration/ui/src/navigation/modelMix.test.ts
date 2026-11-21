import {describe,expect,it} from 'vitest';
import {modelMix} from './modelMix';

describe('model composition',()=>{
 it('splits output tokens by provider and model, largest first, shares summing to one',()=>{
  const mix=modelMix([{source:'claude',model:'claude-opus-5-5',out:30},{source:'claude',model:'claude-fable-5',out:60},{source:'claude',model:'claude-opus-5-5',out:10},{source:'codex',model:'gpt-6.1-sol',out:5},{source:'other',model:'x',out:9}]);
  expect(mix.map(p=>p.provider)).toEqual(['claude','codex']);
  expect(mix[0].models.map(m=>[m.label,m.share])).toEqual([['Fable 5',0.6],['Opus 5.5',0.4]]);
  expect(mix[1].total).toBe(5);
 });
 it('folds models past the fifth into Other',()=>{
  const mix=modelMix(Array.from({length:8},(_,i)=>({source:'codex',model:'m'+i,out:8-i})));
  expect(mix[0].models.map(m=>m.label)).toEqual(['m0','m1','m2','m3','m4','Other']);
  expect(mix[0].models[5].value).toBe(6);
 });
});
