import {describe,it,expect} from 'vitest';
import {commandCatalog,commandName,slashQuery,matchingCommands} from './slashCommands';
describe('native slash commands',()=>{
 it('recognizes aliases and leaves ordinary messages alone',()=>{expect(commandName('/remote control')).toEqual({name:'remote-control',args:''});expect(commandName('/goal finish tests')).toEqual({name:'goal',args:'finish tests'});expect(commandName('What does /compact do?')).toBeNull();expect(slashQuery('/co')).toBe('co');expect(slashQuery('x /co')).toBeNull();expect(slashQuery('/goal finish tests')).toBeNull();expect(slashQuery('/remote c')).toBe('remote c')});
 it('preserves native compact dispatch over skill collisions',()=>{const list=commandCatalog('claude',[{name:'compact',insert:'/compact',description:'fake'}]);expect(list.find(c=>c.name==='compact')?.operation).toBe('compact')});
 it('uses provider-specific catalogs and filters aliases',()=>{const list=commandCatalog('claude',[],[{name:'goal',insert:'/goal',description:'Native goal'}]);expect(list.find(c=>c.name==='goal')?.operation).toBeUndefined();expect(matchingCommands(list,'remote control')[0].name).toBe('remote-control');expect(commandCatalog('codex',[]).some(c=>c.name==='remote-control')).toBe(false)});
});
