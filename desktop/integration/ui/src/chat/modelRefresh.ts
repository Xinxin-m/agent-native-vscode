// Text for the POST /api/models/refresh result, shared by Settings and the model picker footer.
export type ToolUpdate={version_before:string;version_after:string;latest:string;update_available:boolean;new_models:{id:string;name:string}[];install?:{ok:boolean;output:string}};
export type ModelRefresh={claude:ToolUpdate;codex:ToolUpdate;checked_at:string;install_skipped?:string;tiers_stale?:string};
const NAMES={claude:'Claude Code',codex:'Codex'} as const;

export function versionLine(tool:keyof typeof NAMES,t:ToolUpdate){
 const name=NAMES[tool];
 if(t.install)return t.install.ok&&t.version_after!==t.version_before?`${name} updated from ${t.version_before} to ${t.version_after}`:`${name} update failed: ${t.install.output.split('\n').filter(Boolean).pop()||'no output'}`;
 if(!t.version_before)return `${name} not found`;
 if(t.update_available)return `${name} ${t.version_before}, ${t.latest} available`;
 return `${name} ${t.version_before}`+(t.latest?', up to date':', latest version unknown');
}

export function describeModelRefresh(r:ModelRefresh){
 const fresh=[...r.claude.new_models,...r.codex.new_models].map(m=>m.name);
 return {
  models:fresh.length?'New models: '+fresh.join(', '):'No new models.',
  versions:[versionLine('claude',r.claude),versionLine('codex',r.codex)],
  // Only the lines worth a glance in the picker: an update available, done, failed or skipped.
  changes:[...(['claude','codex'] as const).filter(t=>r[t].update_available||r[t].install).map(t=>versionLine(t,r[t])),...(r.install_skipped?[r.install_skipped.replace(/\.$/,'')]:[])],
  canInstall:!r.install_skipped&&(['claude','codex'] as const).some(t=>r[t].update_available&&!r[t].install),
  notes:[r.install_skipped,r.tiers_stale&&'Tiers file: '+r.tiers_stale].filter(Boolean) as string[],
 };
}

export const knownModels=(options:any)=>Object.fromEntries(['claude','codex'].map(p=>[p,(options?.[p]?.models||[]).map((m:any)=>m.id)]));
