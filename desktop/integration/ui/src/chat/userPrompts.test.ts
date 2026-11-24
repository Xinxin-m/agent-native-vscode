import {describe,it,expect} from 'vitest';
import {firstLine,stickyPromptIndex,userPrompts} from './userPrompts';
describe('her prompts',()=>{
 it('lists only her main-chat prompts, oldest first, then sent messages not yet in the transcript',()=>{
  const turns=[{role:'user',text:'Audit the data',ts:'1'},{role:'assistant',text:'Done'},{role:'user',text:'  '},{role:'tool',text:'ls'},{role:'user',text:'Now plot it',ts:'2'}];
  expect(userPrompts(turns,[{text:'Also add labels',sentAt:'3'}]).map(p=>p.text)).toEqual(['Audit the data','Now plot it','Also add labels']);
 });
 it('first line skips blank lines and Markdown markers and is cut at the limit',()=>{
  expect(firstLine('\n\n## Check the logs\nthen the plots')).toBe('Check the logs');
  expect(firstLine('- first item')).toBe('first item');
  expect(firstLine('x'.repeat(10),5)).toBe('xxxx…');
 });
 it('sticks the prompt the visible answer belongs to, only once that prompt is scrolled out above',()=>{
  const boxes=[{top:-900,bottom:-800},{top:-300,bottom:-200},{top:400,bottom:450}];
  expect(stickyPromptIndex(boxes,0)).toBe(1);
  expect(stickyPromptIndex([{top:-900,bottom:-800},{top:-50,bottom:40}],0)).toBe(-1);
  expect(stickyPromptIndex([{top:100,bottom:200}],0)).toBe(-1);
 });
});
