import {describe,it,expect} from 'vitest';
import {creationLocation,initialWorkspace} from './fileCreation';
const roots=[{id:'a',host_id:'mac',access:'write'},{id:'b',host_id:'mac',access:'write'},{id:'remote',host_id:'cluster',access:'read'}];
const rows=[{key:'one',rid:'b',path:'docs/note.md',type:'file'},{key:'folder',rid:'b',path:'docs',type:'directory'},{key:'top',rid:'a',path:'note.md',type:'file'}];
describe('file creation location',()=>{
 it('creates beside the selected file and inside a selected folder',()=>{expect(creationLocation(roots,rows,['one'],'a','a')).toMatchObject({rid:'b',path:'docs',writable:true});expect(creationLocation(roots,rows,['folder'])).toMatchObject({rid:'b',path:'docs'});expect(creationLocation(roots,rows,['top'])).toMatchObject({rid:'a',path:''});});
 it('uses the tab workspace when nothing is selected and refuses remote writes',()=>{expect(creationLocation(roots,rows,[],'b','a')).toMatchObject({rid:'b',path:''});expect(creationLocation(roots,[],[],'remote')).toMatchObject({writable:false});expect(creationLocation([],[],[])).toBeNull();});
 it('right-click targets the clicked row: inside a folder or root, beside a file',()=>{const all=[...rows,{key:'root-b',rid:'b',path:'',type:'root'}];expect(creationLocation(roots,all,['root-b'],'','a')).toMatchObject({rid:'b',path:'',writable:true});expect(creationLocation(roots,all,['folder'],'','a')).toMatchObject({rid:'b',path:'docs'});expect(creationLocation(roots,all,['one'],'','a')).toMatchObject({rid:'b',path:'docs'});});
 it('restores the active workspace and recovers when it is no longer open',()=>{expect(initialWorkspace(roots,'b')?.id).toBe('b');expect(initialWorkspace(roots,'gone')?.id).toBe('a');});
});
