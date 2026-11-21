import {describe,it,expect} from 'vitest';
import {readFileSync} from 'node:fs';
// The working indicator turns only while active; waiting and finished are static and distinct.
const css=readFileSync(new URL('./ActivitySummary.css',import.meta.url),'utf8');
const rules=(selector:string)=>css.split('\n').filter(line=>line.startsWith(selector)).join('\n');
describe('activity indicator',()=>{
 it('spins with a transform only while active',()=>{
  expect(rules('.activity-summary-dot.is-active')).toMatch(/animation:activity-spin/);
  expect(css).toMatch(/@keyframes activity-spin\{to\{transform:rotate\(360deg\)\}\}/);
  expect(rules('.activity-summary-dot{')).not.toMatch(/animation/);
 });
 it('shows waiting and finished without motion',()=>{
  expect(rules('.activity-summary[data-phase="Waiting for you"]')).not.toMatch(/animation/);
  expect(rules('.activity-summary[data-phase="Finished"]')).toMatch(/::after/);
 });
});
