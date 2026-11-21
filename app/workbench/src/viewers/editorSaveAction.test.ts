import {describe,it,expect,vi} from 'vitest';
import {installEditorSave} from './editorSaveAction';
function fixture(){const commands=new Set<any>(),listeners=new Set<()=>void>();return {commands,listeners,editor:{addAction:(action:any)=>{commands.add(action);return {dispose:()=>commands.delete(action)}},onDidDispose:(listener:()=>void)=>{listeners.add(listener);return {dispose:()=>listeners.delete(listener)}}},disposeEditor:()=>[...listeners].forEach(listener=>listener())};}
describe('source editor Save lifetime',()=>{
 it('releases the actual command and editor listener on editor disposal',()=>{const f=fixture(),save=vi.fn(),subscription=installEditorSave(f.editor,save,123);const command=[...f.commands][0];expect(command.keybindings).toEqual([123]);command.run();expect(save).toHaveBeenCalledOnce();f.disposeEditor();expect(f.commands.size).toBe(0);expect(f.listeners.size).toBe(0);subscription.dispose();expect(f.commands.size).toBe(0);});
 it('allows explicit unmount cleanup before the editor disposes',()=>{const f=fixture(),subscription=installEditorSave(f.editor,()=>{},123);subscription.dispose();f.disposeEditor();expect(f.commands.size).toBe(0);expect(f.listeners.size).toBe(0);});
 it('does not accumulate closed-editor command handlers during repeated file cycles',()=>{const f=fixture();for(let i=0;i<50;i++){const subscription=installEditorSave(f.editor,()=>{},123);expect(f.commands.size).toBe(1);subscription.dispose();}expect(f.commands.size).toBe(0);expect(f.listeners.size).toBe(0);});
});
