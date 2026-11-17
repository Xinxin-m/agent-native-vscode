import {describe,expect,it} from 'vitest';
import {matches,shownSections} from './settingsSearch';
import {durationLabel} from './SessionCleanup';

const rows=[{section:'Appearance',label:'Theme',hint:'Colors for the whole app.',keywords:'beige sage dark'},{section:'Sessions',label:'Session cleanup',hint:'Stop agents automatically.'}];

describe('settings search',()=>{
 it('matches every word against label, hint, section and keywords',()=>{
  expect(matches(rows[0],'beige')).toBe(true);
  expect(matches(rows[0],'appearance colors')).toBe(true);
  expect(matches(rows[0],'theme stop')).toBe(false);
  expect(matches(rows[1],'  ')).toBe(true);
 });
 it('leaves out sections without rows and keeps pages in order',()=>{
  expect(shownSections(rows)).toEqual(['Appearance','Sessions','Resources','Analytics']);
 });
 it('names cleanup limits in words',()=>{
  expect([12,24,72,168,5].map(durationLabel)).toEqual(['12 hours','1 day','3 days','7 days','5 hours']);
 });
});
