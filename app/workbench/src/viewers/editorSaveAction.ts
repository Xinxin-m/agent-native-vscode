type Disposable={dispose:()=>void};
type SaveEditor={addAction:(action:{id:string;label:string;keybindings:number[];run:()=>void})=>Disposable;onDidDispose:(listener:()=>void)=>Disposable};
// Monaco's addCommand leaves a global registration. addAction supplies both an editor scope and a disposable.
export function installEditorSave(editor:SaveEditor,save:()=>void,keybinding:number):Disposable {
 const action=editor.addAction({id:'library.save-file',label:'Save changes',keybindings:[keybinding],run:save});
 let listener:Disposable|undefined,disposed=false;
 const dispose=()=>{if(disposed)return;disposed=true;action.dispose();listener?.dispose();};
 listener=editor.onDidDispose(dispose);if(disposed)listener.dispose();
 return {dispose};
}
