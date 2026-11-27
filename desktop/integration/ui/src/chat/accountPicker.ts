/** Entries of GET /api/accounts/picker: the Accounts tab's connected rows, one per verified identity. */
export type PickerAccount={id:string,tool:string,label:string,email:string,logged_in?:boolean};
export type Picker={pool_labels?:Record<string,string>,accounts:PickerAccount[],auto?:Record<string,{account_id:string,label:string,email:string}|null>};
export const pickerText=(a:{id?:string,label?:string,email?:string})=>{const name=a.label&&a.label!==a.email?a.label:'';return name&&a.email?name+' · '+a.email:name||a.email||a.id||'';};
export const pickerOptions=(picker:Picker|null|undefined,tool:string)=>(picker?.accounts||[]).filter(a=>a.tool===tool);
/** Auto names the account it resolves to now; without one it stays plain "Auto". */
export function autoText(picker:Picker|null|undefined,tool:string){const pool=picker?.pool_labels?.[tool];if(pool)return 'Auto · '+pool;const a=picker?.auto?.[tool];return a?'Auto · '+pickerText(a):'Auto';}
