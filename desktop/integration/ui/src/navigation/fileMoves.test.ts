import {describe,expect,it} from 'vitest';
import {refusal,moving,confirmation,followMove} from './fileMoves';

const item=(path:string,type='file',rid='a',host='mac')=>({rid,path,name:path.split('/').at(-1)!,type,host});
const target=(path:string,rid='b',host='mac')=>({rid,path,host,label:rid==='a'?'wsA':'wsB'});
const label=(id:string)=>id==='sc'?'SC cluster':id;

describe('file moves',()=>{
 it('refuses moves across this Mac and a remote host, and folders into themselves',()=>{
  expect(refusal([item('x.md')],target('',  'r','sc'),label)).toBe('Moving between this Mac and SC cluster is not supported yet.');
  expect(refusal([item('x.md','file','r','sc')],target('','r','sc'),label)).toBe('Files on SC cluster are read-only.');
  expect(refusal([item('docs','directory')],target('docs/inner','a'),label)).toBe('"docs" cannot be moved into itself.');
  expect(refusal([item('docs','directory')],target('docs2','a'),label)).toBe('');
 });
 it('skips items already in the destination unless copying',()=>{
  expect(moving([item('docs/x.md')],target('docs','a'),false)).toEqual([]);
  expect(moving([item('docs/x.md')],target('docs','a'),true)).toHaveLength(1);
 });
 it('confirms folders and more than five items, naming source and destination',()=>{
  const rootLabel=(rid:string)=>rid==='a'?'wsA':'wsB';
  expect(confirmation([item('x.md')],target(''),rootLabel,false)).toBe('');
  expect(confirmation([item('docs','directory')],target('sub'),rootLabel,false)).toBe('Move the folder "docs" from wsA to wsB/sub?');
  expect(confirmation([1,2,3,4,5,6].map(n=>item('d/'+n+'.md')),target(''),rootLabel,false)).toBe('Move 6 items from wsA/d to wsB?');
  expect(confirmation([item('docs','directory')],target(''),rootLabel,true)).toBe('');
  expect(confirmation([item('x.md')],target(''),rootLabel,false,'always')).toBe('Move the file "x.md" from wsA to wsB?');
  expect(confirmation([item('docs','directory')],target(''),rootLabel,false,'never')).toBe('');
 });
 it('moves an open file tab with its file or with the folder that contains it',()=>{
  const moves=[{from:'/w/a/docs',to:'/w/b/docs copy',root_id:'b'},{from:'/w/a/x.md',to:'/w/b/x.md',root_id:'b'}];
  expect(followMove({kind:'file',path:'/w/a/x.md'},moves)).toEqual({path:'/w/b/x.md',root_id:'b'});
  expect(followMove({kind:'file',path:'/w/a/docs/inner/d.txt'},moves)).toEqual({path:'/w/b/docs copy/inner/d.txt',root_id:'b'});
  expect(followMove({kind:'file',path:'/w/a/docs2/d.txt'},moves)).toBeUndefined();
  expect(followMove({kind:'file',host_id:'sc',path:'/w/a/x.md'},moves)).toBeUndefined();
  expect(followMove({kind:'chat',path:'/w/a/x.md'},moves)).toBeUndefined();
 });
});
