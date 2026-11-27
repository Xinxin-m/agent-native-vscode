const providerName=(p:string)=>p==='codex'?'Codex':'Claude';

export function quotaHandoffNotice(run:any):string {
 const p=run?.provider_switch,from=run?.provider_switch_from;
 if(from) return `Continued in ${providerName(run.provider)} after every ${providerName(from.provider)} account reached its usage limit.`;
 if(p?.status==='offered') return p.automatic?`${p.reason} Continuing this chat in ${providerName(p.to)}.`:`${p.reason} Use Continue in ${providerName(p.to)} below to keep going in this chat.`;
 if(p?.status==='continued') return `Continued this chat in ${providerName(p.to)}.`;
 if(p?.status==='blocked') return `Could not continue in ${providerName(p.to)}: ${p.reason}`;
 const q=run?.quota_failover;
 if(q?.status==='switching') return q.to_account_id?`Switching accounts after the usage limit: ${q.from_account_id||'Claude'} → ${q.to_account_id}.`:'Checking whether an account switch is safe after the usage limit.';
 if(q?.status==='blocked') return `Account switch stopped: ${q.reason||'No safe continuation is available.'}`;
 if(q?.status==='continued') return `Continuation started on ${q.to_account_id||'another account'}.`;
 if(run?.quota_failover_from) return `Continued on ${run.account_id||'another account'} after ${run.quota_failover_from.account_id||'the previous account'} reached its usage limit.`;
 return '';
}

export function showPreviousConversation(chat:any):boolean {
 return Boolean(chat?.quota_failover_from && chat.quota_failover_from.mode !== 'native_resume' && chat.source_key && chat.source_key !== chat.key);
}
