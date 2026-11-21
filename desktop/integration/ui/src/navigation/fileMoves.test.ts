import {describe,expect,it} from 'vitest';
import {refusal,moving,confirmation,followMove,deletedBy} from './fileMoves';

const item=(path:string,type='file',rid='a',host='mac')=>({rid,path,name:path.split('/').at(-1)!,type,host});
const target=(path:string,rid='b',host='mac')=>({rid,path,host,label:rid==='a'?'wsA':'wsB'});
const label=(id:string)=>id==='cluster'?'Cluster':id;

describe('file moves',()=>{
 it('allows cross-host transfer while refusing unsupported remote-only moves and self moves',()=>{
  expect(refusal([item('x.md')],target('',  'r','cluster'),label)).toBe('');
  expect(refusal([item('x.md','file','r','cluster')],target('','r','cluster'),label)).toBe('Files on Cluster are read-only within the remote workspace.');
  expect(refusal([item('docs','directory')],target('docs/inner','a'),label)).toBe('"docs" cannot be moved into itself.');
  expect(refusal([item('docs','directory')],target('docs2','a'),label)).toBe('');
 });
 it('supports both transfer directions and refuses mixed-source directions',()=>{
  expect(refusal([item('remote.md','file','remote','cluster')],target('','local','mac'),label)).toBe('');
  expect(refusal([item('local.md'),item('remote.md','file','remote','cluster')],target('','local','mac'),label)).toContain('one direction');
  const moved=[{from:'/same/file.md',to:'/dest/file.md',root_id:'remote',from_host:'mac',to_host:'cluster'}];
  expect(followMove({kind:'file',host_id:'mac',path:'/same/file.md'},moved)).toEqual({path:'/dest/file.md',root_id:'remote',host_id:'cluster'});
  expect(followMove({kind:'file',host_id:'cluster',path:'/same/file.md'},moved)).toBeUndefined();
 });
 it('matches tabs of a deleted file or of files inside a deleted folder on the same host',()=>{
  const deleted=[{path:'/ws/docs',host:'mac'},{path:'/ws/note.md'}];
  expect(deletedBy({kind:'file',path:'/ws/note.md'},deleted)).toBe(true);
  expect(deletedBy({kind:'file',host_id:'mac',path:'/ws/docs/inner/deep.txt'},deleted)).toBe(true);
  expect(deletedBy({kind:'file',path:'/ws/docs2/x.md'},deleted)).toBe(false);
  expect(deletedBy({kind:'file',host_id:'cluster',path:'/ws/note.md'},deleted)).toBe(false);
  expect(deletedBy({kind:'chat',path:'/ws/note.md'},deleted)).toBe(false);
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
  expect(followMove({kind:'file',host_id:'cluster',path:'/w/a/x.md'},moves)).toBeUndefined();
  expect(followMove({kind:'chat',path:'/w/a/x.md'},moves)).toBeUndefined();
 });
});
