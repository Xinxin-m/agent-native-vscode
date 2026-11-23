import {describe,it,expect} from 'vitest';
import {isWaiting,isFailed} from './feed';
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
