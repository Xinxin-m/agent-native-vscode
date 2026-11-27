import React from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {describe,expect,it} from 'vitest';
import {activeSubagents,SubagentActivity} from './SubagentActivity';
import {emptyRun} from '../state/runs';

describe('active subagents',()=>{
 const legacy={
  key:'selected',
  nodes:[
   {key:'selected',state:'working',model_main:'parent-model'},
   {key:'child',is_child:true,agent_id:'a1',title:'Juniper',activity:'running',model_main:'child-model'},
   {key:'nested',is_child:true,agent_id:'a2',title:'Linden',state:'waiting',models:{'nested-model':2}},
   {key:'finished',is_child:true,agent_id:'a3',title:'Finished reviewer',state:'finished',activity:'running'},
   {key:'sibling',is_child:true,agent_id:'s',title:'Other branch',state:'working'},
  ],
  edges:[{parent_key:'selected',child_key:'child'},{parent_key:'child',child_key:'nested'},
   {parent_key:'selected',child_key:'finished'},{parent_key:'root',child_key:'sibling'}],
 };
 it('renders only active descendants from a legacy live server',()=>{
  const rows=activeSubagents(emptyRun(''),legacy);
  expect(rows.map(row=>[row.title,row.state,row.model])).toEqual([
   ['Juniper','working','child-model'],['Linden','waiting','nested-model'],
  ]);
  const html=renderToStaticMarkup(<SubagentActivity>{rows}</SubagentActivity>);
  expect(html).toContain('Juniper</strong><span> · Running · child-model');
  expect(html).toContain('Linden</strong><span> · Waiting · nested-model');
  expect(html).not.toContain('parent-model');
 });
 it('takes recent provider lifecycle evidence over an old tree response',()=>{
  const run={...emptyRun('r'),run:{status:'running',last_seq:1,
   subagents:[{id:'a1',state:'working',title:'Juniper'}]},
   events:[{seq:2,event:'state',data:{subagents:[
    {id:'a1',state:'finished'},{id:'a4',title:'Cedar',state:'starting'},
   ]}}]};
  expect(activeSubagents(run,legacy).map(r=>r.title)).toEqual(['Linden','Cedar']);
 });
 it('drops active snapshot rows when the broker run terminates',()=>{
  const run={...emptyRun('r'),run:{status:'completed',subagents:[{id:'a1',state:'working'}]}};
  expect(activeSubagents(run,legacy).map(r=>r.title)).toEqual(['Linden']);
 });
});
