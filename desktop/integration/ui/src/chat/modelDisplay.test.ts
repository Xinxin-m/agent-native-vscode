import {describe,it,expect} from 'vitest';
import {modelDisplay} from './modelDisplay';
describe('model display',()=>{
 it('uses advertised resolved version while hiding context suffix',()=>expect(modelDisplay({id:'opus[1m]',resolved_model:'claude-opus-5-5',name:'opus[1m]'})).toBe('Opus 5.5'));
 it('resolves stored aliases against the current catalog',()=>expect(modelDisplay('opus[1m]',[{id:'claude-opus-5-5'}])).toBe('Opus 5.5'));
 it('preserves advertised Codex names',()=>expect(modelDisplay({id:'gpt-6.1-sol',name:'GPT-6.1 Sol'})).toBe('GPT-6.1 Sol'));
});
