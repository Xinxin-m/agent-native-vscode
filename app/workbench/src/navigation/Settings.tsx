import {modelDisplay} from '../chat/modelDisplay';
import {textSizes,nearestTextSize} from './textSize';
import "./settings-polish.css";
import React,{useState,useEffect} from 'react';
import {useWorkbench} from '../state/context';
import {api} from '../api/client';
import {Check,Search} from 'lucide-react';
import {AccountsPage} from './AccountsPanel';
import {useNotices,NoticeToggle,NoticeDuration} from './Notices';
import {Insights} from './Insights';
import {SessionCleanup} from './SessionCleanup';
import {SECTIONS,matches,shownSections,type SettingRow} from './settingsSearch';

type Row=SettingRow&{control:React.ReactNode;wide?:boolean};
const themes=[['system','System'],['light','Light'],['dark','Dark'],['beige','Warm beige'],['sage','Soft sage']];
const choose=(label:string,value:any,options:any[][],onChange:(value:string)=>void)=><select aria-label={label} value={value} onChange={e=>onChange(e.target.value)}>{options.map(([v,l])=><option key={v} value={v}>{l}</option>)}</select>;

// Agent settings stored in the server's config.json. Shown only when the server says it can save them.
function useServerSettings(data:any,refresh:()=>Promise<void>){
 const [supported,setSupported]=useState<string[]>([]),[local,setLocal]=useState<Record<string,any>>({}),[status,setStatus]=useState<Record<string,string>>({});
 useEffect(()=>{let live=true;api('/api/capabilities').then(c=>{if(live)setSupported(c.settings||[])}).catch(()=>{});return()=>{live=false}},[]);
 async function set(key:string,value:any){setLocal(l=>({...l,[key]:value}));setStatus(s=>({...s,[key]:'Saving…'}));try{await api('/api/config',{[key]:value});await refresh();setStatus(s=>({...s,[key]:'Saved'}));}catch(e:any){setLocal(l=>{const next={...l};delete next[key];return next});setStatus(s=>({...s,[key]:'Not saved: '+e.message}));}}
 const value=(key:string,fallback:any)=>local[key]??data?.config?.[key]??fallback;
 // Checked stores `on`, unchecked `off`; `fallback` is the server's default when the key is unset.
 const toggle=(key:string,fallback:any=false,on:any=true,off:any=false)=><span className="setting-control"><small role="status">{status[key]}</small><input type="checkbox" checked={value(key,fallback)===on} onChange={e=>void set(key,e.target.checked?on:off)}/></span>;
 return {supported,toggle,set,value,status};
}

// Hosts the session cleanup rule reaches: this Mac, plus SSH hosts that are set up and allow stopping.
export const cleanupCoverage=(hosts:any[]=[])=>{const remote=hosts.filter(h=>h.transport==='ssh'&&h.configured&&(!h.capabilities||h.capabilities.includes('stop'))).map(h=>h.label||h.id);return 'Covers this Mac'+(remote.length?' and '+remote.join(', ')+' when reachable':' only')+'.';};

export function Settings({initialTab='Appearance'}:{initialTab?:string}){
 const {data,hosts,refresh,preferences:p,setPreferences}=useWorkbench();
 const [tab,setTab]=useState(SECTIONS.includes(initialTab)?initialTab:'Appearance'),[query,setQuery]=useState('');
 useEffect(()=>{if(SECTIONS.includes(initialTab))setTab(initialTab);},[initialTab]);
 const server=useServerSettings(data,refresh),notices=useNotices();
 if(initialTab==='Accounts')return <AccountsPage/>;
 const official=false,on=(value:boolean,change:(v:boolean)=>void)=><input type="checkbox" checked={value} onChange={e=>change(e.target.checked)}/>;
 const models=(provider:string)=>{const list=[['','Last used model'],...(data?.compose_options?.[provider]?.models||[]).filter((m:any)=>m.id).map((m:any)=>[m.id,modelDisplay(m,data?.compose_options?.[provider]?.models||[])])],v=p.defaultModels?.[provider]||'';return v&&!list.some(o=>o[0]===v)?[...list,[v,v]]:list;};
 const model=(provider:string,label:string)=>choose(label,p.defaultModels?.[provider]||'',models(provider),x=>setPreferences({defaultModels:{...p.defaultModels,[provider]:x}}));
 const compact={enabled:true,threshold:.5,idle_minutes:0,...server.value('auto_compact',{})},setCompact=(patch:any)=>void server.set('auto_compact',{...compact,...patch});
 const rows:Row[]=[
  {section:'Appearance',label:'Theme',hint:'Warm beige and Soft sage are softer palettes for long reading.',keywords:'color dark light eye',wide:true,control:<div className="theme-choices">{themes.map(([id,name])=><button className={'theme-choice '+(p.theme===id?'selected':'')} key={id} onClick={()=>setPreferences({theme:id})}><span className={'theme-swatch '+id}><i/><i/></span><span>{name}</span>{p.theme===id&&<Check size={13}/>}</button>)}</div>},
  {section:'Appearance',label:'Text size',hint:'Scales the whole interface.',keywords:'font zoom',control:choose('Text size',nearestTextSize(p.fontSize),textSizes.map(t=>[t.value,t.label]),x=>setPreferences({fontSize:+x}))},
  {section:'Appearance',label:'Code font size',hint:'Font size in the code editor.',keywords:'font monaco',control:choose('Code font size',p.codeFontSize||14,[12,13,14,15,16,18].map(n=>[n,n+' px']),x=>setPreferences({codeFontSize:+x}))},
  {section:'Appearance',label:'Reading width',hint:'Widest line of conversation text.',control:choose('Reading width',p.readingWidth,[[640,'Narrow'],[780,'Comfortable'],[960,'Wide'],[1200,'Extra wide']],x=>setPreferences({readingWidth:+x}))},
  {section:'Appearance',label:'Line spacing',hint:'Space between lines in conversations and documents.',control:choose('Line spacing',p.lineHeight,[[1.45,'Compact'],[1.65,'Standard'],[1.85,'Relaxed'],[2.05,'Spacious']],x=>setPreferences({lineHeight:+x}))},
  {section:'Chats',label:'Default provider',hint:'For a new chat when the current tab is not a chat.',keywords:'claude codex agent new chat plus',control:choose('Default provider',p.defaultProvider||'ask',[['claude','Claude Code'],['codex','Codex'],['ask','Ask each time']],x=>setPreferences({defaultProvider:x as any}))},
  ...(official?[]:[
  {section:'Chats',label:'Claude Code model',hint:'Model for new Claude Code chats.',keywords:'default model opus sonnet',control:model('claude','Claude Code model')},
  {section:'Chats',label:'Codex model',hint:'Model for new Codex chats.',keywords:'default model gpt',control:model('codex','Codex model')},
  {section:'Chats',label:'Thinking',hint:'Model reasoning in conversations.',keywords:'reasoning',control:choose('Thinking',p.thinking||'collapsed',[['collapsed','Show collapsed'],['expanded','Show expanded'],['hidden','Hide']],x=>setPreferences({thinking:x as any}))},
  {section:'Chats',label:'Tool output',hint:'Tool calls and their results in a run.',keywords:'activity details',control:choose('Tool output',p.toolOutput||'folded',[['folded','Folded'],['expanded','Expanded']],x=>setPreferences({toolOutput:x as any}))},
  {section:'Chats',label:'Composer height limit',hint:'Automatic fits up to eight lines and a quarter of the pane; choose a percentage to set a different limit.',keywords:'message box input',control:choose('Composer height limit',p.composerMax??'auto',[['auto','Automatic'],[.3,'30%'],[.45,'45%'],[.6,'60%'],[.75,'75%']],x=>setPreferences({composerMax:x==='auto'?null:+x}))},
  {section:'Chats',label:'Fold composer while reading',hint:'Folds the message box to one line when you scroll up.',keywords:'message box collapse',control:on(p.foldComposer!==false,x=>setPreferences({foldComposer:x}))},
  {section:'Chats',label:'Send key',hint:'The key that sends a message. Shift+Enter always adds a new line.',keywords:'enter cmd keyboard',control:choose('Send key',p.sendKey||'enter',[['enter','Enter'],['mod','Cmd+Enter']],x=>setPreferences({sendKey:x as any}))},
  {section:'Chats',label:'While an agent is working, Enter',hint:'Queue waits for the turn to end. Steer delivers now.',keywords:'queue steer running',control:choose('While an agent is working, Enter',p.whileWorking||'queue',[['queue','Queues'],['steer','Steers']],x=>setPreferences({whileWorking:x as any}))},
  {section:'Chats',label:'Open chats at the latest message',hint:'Off: chats open where you left them.',keywords:'scroll bottom',control:on(p.openAtLatest!==false,x=>setPreferences({openAtLatest:x}))}]),
  ...(server.supported.includes('auto_approve_tools')?[{section:'Agents',label:'Approve tool requests automatically',hint:'Accepts command and file-edit approvals in Library runs.',keywords:'auto-approve permission',control:server.toggle('auto_approve_tools',true)}]:[]),
  ...(server.supported.includes('agent_questions')?[{section:'Agents',label:'Answer agent questions automatically',hint:'The agent picks an answer, says so, and lists the question as an open decision.',keywords:'questions ask defer open decisions',control:server.toggle('agent_questions','defer','defer','ask')}]:[]),
  ...(server.supported.includes('attention_sound')?[{section:'Agents',label:'Sound when an agent needs you',hint:'Plays once when a run waits for your answer.',keywords:'chime approval notification',control:server.toggle('attention_sound',true)}]:[]),
  ...(server.supported.includes('auto_compact')?[{section:'Agents',label:'Compact automatically',hint:'When a conversation fills this share of its context window, compact it at the end of the turn.',keywords:'context compaction idle',control:<span className="setting-control"><small role="status">{server.status.auto_compact}</small>{compact.enabled&&<>{choose('Compact at',compact.threshold,[.4,.5,.6,.7].map(n=>[n,Math.round(n*100)+'%']),x=>setCompact({threshold:+x}))}{choose('Compact when idle',compact.idle_minutes,[[0,'Idle: off'],[15,'Idle: 15 min'],[30,'Idle: 30 min'],[60,'Idle: 60 min']],x=>setCompact({idle_minutes:+x}))}</>}<input type="checkbox" aria-label="Compact automatically" checked={compact.enabled!==false} onChange={e=>setCompact({enabled:e.target.checked})}/></span>}]:[]),
  {section:'Agents',label:'Open subagent tree',hint:'How the tree of subagents opens from a chat.',keywords:'agent map',control:choose('Open subagent tree',p.agentMapView||'popup',[['popup','Pop-up'],['inline','Inside the conversation']],x=>setPreferences({agentMapView:x as any}))},
  {section:'Appearance',group:'Panes and tabs',label:'When I click a file',hint:'Where files open from chats and the file tree.',keywords:'open preview split',control:choose('When I click a file',p.openDefault,[['right','Preview beside the conversation'],['preview','Preview in this pane'],['tab','New tab in this pane'],['below','Split below']],x=>setPreferences({openDefault:x as any}))},
  {section:'Appearance',group:'Panes and tabs',label:'Automatic pane limit',hint:'At the limit, files reuse the last pane. Splits you make yourself are not limited.',keywords:'split',control:choose('Automatic pane limit',p.maxAutoPanes,[[1,'1 pane'],[2,'2 panes'],[3,'3 panes'],[4,'4 panes'],[0,'No limit']],x=>setPreferences({maxAutoPanes:+x}))},
  {section:'Appearance',group:'Files',label:'Files tab shows',hint:'What a new Files tab opens to.',keywords:'workspace folder',control:choose('Files tab shows',p.filesTabWorkspace||'ask',[['ask','Ask for a workspace'],['all','All workspaces']],x=>setPreferences({filesTabWorkspace:x as any}))},
  {section:'Appearance',group:'Files',label:'Confirm before moving',hint:'Asks before files or folders move by drag.',keywords:'drag drop move',control:choose('Confirm before moving',p.confirmMoves||'some',[['some','Folders and more than 5 items'],['always','Always'],['never','Never']],x=>setPreferences({confirmMoves:x as any}))},
  {section:'Appearance',group:'Files',label:'Show hidden files',hint:'Names that start with a dot.',keywords:'dotfiles',control:on(p.showHidden===true,x=>setPreferences({showHidden:x}))},
  {section:'Notifications',label:'Usage and limit notices',hint:'Collect usage and limit notices in the inbox.',keywords:'quota weekly alert toast',control:<NoticeToggle notices={notices} name="usage"/>},
  {section:'Notifications',label:'Sign-in expiry notices',hint:'Collect sign-in notices in the inbox.',keywords:'login renew alert toast',control:<NoticeToggle notices={notices} name="signin"/>},
  {section:'Notifications',label:'Recovery prompt duration',hint:'Applies to recovery prompts. The timer pauses while you interact.',keywords:'alert toast timeout keep open dismiss seconds',control:<NoticeDuration notices={notices}/>},
  {section:'Sessions',label:'Live refresh interval',hint:'How often the Live list and Sessions page check for running agents.',keywords:'poll update',control:choose('Live refresh interval',p.liveRefresh??15,[[15,'15 s'],[30,'30 s'],[60,'60 s'],[0,'Manual']],x=>setPreferences({liveRefresh:+x}))},
  {section:'Sessions',label:'Session cleanup',hint:'Stops forgotten agents after a time limit. '+cleanupCoverage(hosts),keywords:'stop idle kill automatic',wide:true,control:<SessionCleanup/>},
 ];
 const shown=shownSections(rows),searching=query.trim()!=='';
 const found=searching?rows.filter(r=>matches(r,query)):rows.filter(r=>r.section===tab);
 const render=(r:Row,i:number,list:Row[])=>[r.group&&r.group!==list[i-1]?.group?<h3 className="setting-group" key={'group '+r.group}>{r.group}</h3>:null,r.wide?<div className="setting-block" key={r.label}><div className="setting-label">{r.label}<small>{r.hint}</small></div>{r.control}</div>:<label className="setting-row" key={r.label}><span>{r.label}<small>{r.hint}</small></span>{r.control}</label>];
 return <div className="settings-page"><header><h1>Settings</h1><div className="settings-search"><Search size={14}/><input type="search" aria-label="Search settings" placeholder="Search settings" value={query} onChange={e=>setQuery(e.target.value)}/></div>{!searching&&<div className="settings-tabs" role="tablist" aria-label="Settings sections">{shown.map(t=><button key={t} role="tab" aria-selected={t===tab} onClick={()=>setTab(t)}>{t}</button>)}</div>}</header>
  {searching?(found.length?shown.filter(s=>found.some(r=>r.section===s)).map(s=><section key={s}><h2>{s}</h2>{found.filter(r=>r.section===s).map(render)}</section>):<p>No settings match “{query.trim()}”.</p>)
  :tab==='Resources'?<Insights mode="resources"/>
  :<section>{found.map(render)}</section>}
 </div>;
}
