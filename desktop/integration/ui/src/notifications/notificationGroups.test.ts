import {describe,it,expect} from 'vitest';
import {isWaiting,isFailed,historyRows,byDay} from './feed';
const item=(kind:string,extra:any={})=>({id:kind,source:'agent',kind,title:'',body:'',at:0,origin:'',options:[],allow_text:false,needs_answer:false,state:'open',answer:null,link:'',seen:false,shown:false,...extra}) as any;
describe('notification dots',()=>{
 it('failed runs are red even with recovery options; waiting items are blue; the rest unmarked',()=>{
  expect(isFailed(item('limit_reached',{needs_answer:true}))&&!isWaiting(item('limit_reached',{needs_answer:true}))).toBe(true);
  expect(isFailed(item('other',{run_id:'r'}))).toBe(true);
  expect(isWaiting(item('question',{needs_answer:true}))&&isWaiting(item('waiting'))).toBe(true);
  expect(isWaiting(item('limit_cleared'))||isFailed(item('limit_cleared'))).toBe(false);
  expect(isWaiting(item('question',{needs_answer:true,state:'answered'}))).toBe(false);
 });
});
describe('history (item 44)',()=>{
 const now=new Date(2026,10,23,15,0),at=(d:number,h:number)=>new Date(2026,10,d,h).getTime()/1000;
 const rows=[item('question',{id:'a',state:'answered',at:at(23,9)}),item('limit_reached',{id:'b',state:'archived',at:at(23,14)}),item('signed_out',{id:'c',source:'app',state:'archived',at:at(22,10)}),item('question',{id:'d',state:'archived',at:at(19,8)}),item('question',{id:'e',at:at(23,12)})];
 it('splits by message type, Failed across both, never open items',()=>{
  expect(historyRows(rows,'agents').map(i=>i.id).sort()).toEqual(['a','b','d']);
  expect(historyRows(rows,'app').map(i=>i.id)).toEqual(['c']);
  expect(historyRows(rows,'failed').map(i=>i.id)).toEqual(['b']);
 });
 it('groups by day, newest first: Today, Yesterday, then dates',()=>{
  const days=byDay(historyRows(rows,'agents').concat(historyRows(rows,'app')),now);
  expect(days.map(d=>d[0]).slice(0,2)).toEqual(['Today','Yesterday']);
  expect(days[0][1].map(i=>i.id)).toEqual(['b','a']);
  expect(days[2][0]).toMatch(/19/);
 });
});
