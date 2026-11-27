/** One list of accounts for the Accounts tab: signed in, signed out (used before) and, apart, unfinished setups. */
export function accountRows(value:any){
 const accounts=(value?.accounts||[]).filter((a:any)=>!a.duplicate_of);
 const pending=(value?.pending_accounts||[]).filter((a:any)=>!a.duplicate_of);
 const order=(a:any,b:any)=>Number(!!b.is_default)-Number(!!a.is_default)||String(a.id).localeCompare(String(b.id));
 const group=(tool:string)=>[...accounts,...pending.filter((a:any)=>a.previously_used)].filter((a:any)=>a.tool===tool).sort(order);
 return {claude:group('claude'),codex:group('codex'),incomplete:pending.filter((a:any)=>!a.previously_used)};
}
/** The name she gave, else the email; the email is shown beside a name so a wrong name is visible. */
export const displayName=(a:any)=>a.label||a.email||a.id;
export const secondaryEmail=(a:any)=>a.label&&a.email&&a.label!==a.email?a.email:'';
/** A usage bar turns red at 90 percent and above. */
export const barHot=(percent:number)=>percent>=90;
export const cleanPercent=(percent:number)=>Math.max(0,Math.min(100,percent));

/** A native session can still exist while Library cannot read its credentials. */
export function needsAccountSignIn(account:{logged_in?:boolean;error?:string}){
 return account.logged_in===true&&account.error==='usage unavailable: credential lookup failed';
}
