import type {Attachment} from '../state/conversations';
import type {RunEvent} from '../state/runs';
export interface SteeringSubmission {requestId:string;runId:string;cursor:number;text:string;message:string;attachments:Attachment[];phase:'sending'|'waiting'|'unconfirmed';error?:string}
export function steeringMessage(text:string,attachments:Attachment[]=[]){
 const parts=[text.trim()];
 for(const attachment of attachments){if(!attachment.path?.startsWith('/'))throw Error('This attachment has not been staged. Remove it and attach it again before steering.');const name=attachment.name.replace(/[\[\]]/g,'');parts.push(`Attached ${attachment.mime.startsWith('image/')?'image':'file'}: [${name}](<${attachment.path}>)`);}
 if(attachments.some(a=>a.mime.startsWith('image/')))parts.push('Open the attached image with your image-viewing tool before responding.');
 return parts.filter(Boolean).join('\n\n');
}
export function steeringOutcome(pending:SteeringSubmission,runId:string,events:RunEvent[]){
 if(pending.runId!==runId)return;
 for(const event of events){if(event.seq<=pending.cursor)continue;
  if(event.event==='steered'&&String(event.data?.message||'').trim()===pending.message.trim())return {accepted:true,event};
  if(event.event==='control_error'&&event.data?.action==='steer')return {accepted:false,error:String(event.data.message||'Codex rejected the steering request'),event};
 }
}
