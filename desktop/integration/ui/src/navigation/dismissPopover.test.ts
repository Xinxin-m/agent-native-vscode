import {describe,it,expect} from 'vitest';
import {dismissPopover} from './dismissPopover';
describe('project popover dismissal',()=>{
 it('keeps inside actions usable, closes outside, and removes its listeners',()=>{
  const doc=new EventTarget(),win=new EventTarget();let inside=true,closed=0;const flags:boolean[]=[];
  const add=doc.addEventListener.bind(doc);doc.addEventListener=((type,fn,options)=>{if(type==='pointerdown')flags.push(options===true||Boolean(typeof options==='object'&&options?.capture));add(type,fn,options)}) as any;
  const remove=dismissPopover(()=>({contains:()=>inside}) as any,()=>closed++,doc as any,win as any);
  doc.dispatchEvent(new Event('pointerdown'));expect(closed).toBe(0);inside=false;doc.dispatchEvent(new Event('pointerdown'));expect(closed).toBe(1);expect(flags).toEqual([true]);
  remove();doc.dispatchEvent(new Event('pointerdown'));win.dispatchEvent(new Event('blur'));expect(closed).toBe(1);
 });
 it('closes on Escape, pane dismissal, blur and hidden visibility but not ordinary keys/visible tabs',()=>{
  const doc=new EventTarget(),win=new EventTarget();let closed=0;Object.assign(doc,{hidden:false});const remove=dismissPopover(()=>null,()=>closed++,doc as any,win as any);
  doc.dispatchEvent(new Event('visibilitychange'));expect(closed).toBe(0);
  const key=new Event('keydown');Object.assign(key,{key:'Escape'});win.dispatchEvent(key);win.dispatchEvent(new Event('workbench-dismiss-popovers'));win.dispatchEvent(new Event('blur'));Object.assign(doc,{hidden:true});doc.dispatchEvent(new Event('visibilitychange'));expect(closed).toBe(4);remove();
 });
});
