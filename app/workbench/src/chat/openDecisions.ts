import {api,query} from '../api/client';
// Decisions the agent made on its own and questions it would have asked (docs/V2-CONTRACTS.md,
// "Approvals, open decisions"). An older server has no route; then nothing is shown.
export interface Decision {seq:number;kind:string;resolution:string;open:boolean;name?:string;summary?:string;reason?:string;questions?:any[]}
const cache=new Map<string,Decision[]>();
let missing=false;
const unsupported=(e:any)=>[404,405,501].includes(Number(e?.status))||/HTTP 40[45]|Unsupported Library route|not valid JSON|Unexpected token/.test(String(e?.message||''));
// Called once a run has ended, so the answer is final and cached.
export async function openDecisions(runId:string,request=api):Promise<Decision[]>{
 if(!runId||missing)return [];
 const known=cache.get(runId);if(known)return known;
 try{const value=await request(query('/api/run/decisions',{id:runId}));const items=(value?.items||[]).filter((d:Decision)=>d.open);cache.set(runId,items);return items}
 catch(e){if(unsupported(e))missing=true;return []}
}
export function decisionLine(d:Decision){
 const q=d.questions?.[0],question=typeof q==='string'?q:q?.question||q?.header;
 if(question)return (d.resolution==='deferred'?'Decided without asking: ':'Unanswered: ')+question;
 const what=(d.name||d.kind||'request')+(d.summary?': '+d.summary:'');
 return d.resolution==='declined'?'Declined '+what:d.resolution==='pending'?'Unanswered '+what:what;
}
