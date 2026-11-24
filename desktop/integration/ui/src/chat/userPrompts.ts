// Her own prompts in the main chat, for the sticky prompt line and the My prompts list.
export interface UserPrompt {text:string;ts?:string}
export const isPrompt=(turn:any)=>turn?.role==='user'&&Boolean(String(turn.text||'').trim());
// Transcript prompts first, then messages she sent that the transcript does not have yet; oldest first.
// The order matches the data-user-prompt numbers Conversation puts on the rendered prompts.
export function userPrompts(turns:any[],submitted:any[]):UserPrompt[]{
 return [...turns.filter(isPrompt).map(t=>({text:String(t.text),ts:t.ts})),...submitted.map(s=>({text:String(s.text),ts:s.sentAt||s.ts}))];
}
export function firstLine(text:string,max=160){
 const line=String(text||'').split('\n').map(l=>l.replace(/^\s*(#{1,6}\s+|[-*+]\s+|>\s*|\d+[.)]\s+)/,'').trim()).find(Boolean)||'';
 return line.length>max?line.slice(0,max-1)+'…':line;
}
// Prompt boxes in reading order, relative to the scroll viewport. The prompt the reading position
// belongs to is the last one that starts above the viewport top; it sticks only once it is entirely above.
export function stickyPromptIndex(boxes:{top:number;bottom:number}[],viewTop:number){
 let index=-1;
 for(let i=0;i<boxes.length&&boxes[i].top<viewTop;i++)index=i;
 return index>=0&&boxes[index].bottom<=viewTop?index:-1;
}
