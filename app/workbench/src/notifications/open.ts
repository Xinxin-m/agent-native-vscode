import type {Item} from './feed';
// A card opens its Library chat in a tab.
export const openItemChat=(wb:any,item:Item)=>{if(!item.chat_key&&item.kind==='waiting')wb.open({kind:'sessions',title:'Sessions'},'tab');else if(item.chat_key)wb.open({kind:'chat',key:item.chat_key,runId:item.run_id,title:item.chat_title||'Conversation'},'tab')};
