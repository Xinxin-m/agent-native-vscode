"use strict";
// Reaches her when no chat is visible. This poller lives in the extension host, so it runs whichever tab is open.
// Each new item that needs an answer (and each new app item) becomes one native toast whose buttons are the item's options.
// The cross only hides the toast; the item stays open on the Notifications page.
const {randomUUID}=require('node:crypto');
const BUTTONS=4;       // the toast shows a few buttons: options, "Type an answer" and "Open" share these
const EVERY=4000,SLOW=60000;
const OPEN={title:'Open'},TYPE={title:'Type an answer'};

const first=text=>String(text||'').split('\n')[0];
function message(item){
 const generic=item.title==='Notice'&&item.body;
 const line=generic?item.body:item.title;
 const extra=item.kind==='approval'?first(item.body):generic?'':item.chat_title;
 return [line,extra].filter(Boolean).join(' · ').slice(0,240);
}
// Which options get their own button; the rest go behind "More".
function buttonsFor(item,canAct){
 if(!canAct)return {options:[],more:false,text:false};
 const text=Boolean(item.allow_text),slots=BUTTONS-1-(text?1:0),options=item.options||[];
 return options.length>slots?{options:options.slice(0,Math.max(slots-1,0)),more:true,text}:{options,more:false,text};
}

function create({vscode,api,canAct=()=>true,every=EVERY,slow=SLOW,claimant='host-'+randomUUID(),setTimer=setTimeout,clearTimer=clearTimeout}){
 const handled=new Set();let timer,stopped=false,busy=false,old=false,shownCount;
 // The bell above the account icon (core globalCompositeBar.ts) shows this context key as its badge: open items (counts.open), so seen never clears it.
 const badge=n=>{if(n===shownCount)return;shownCount=n;void Promise.resolve(vscode.commands.executeCommand('setContext','library.notificationsOpen',n)).catch(()=>{});};
 const act=body=>api('/api/notifications/act',{claimant,...body});
 const report=e=>void vscode.window.showWarningMessage(e.message||String(e));
 const openPage=()=>vscode.commands.executeCommand('library.notifications');
 async function answer(item,body){try{await act({id:item.id,action:'answer',...body});}catch(e){report(e);}}
 async function typed(item){
  const text=await vscode.window.showInputBox({title:message(item),prompt:'Your answer',placeHolder:item.options?.length?'Or type an answer':'Type an answer',ignoreFocusOut:true});
  if(text&&text.trim())await answer(item,{text:text.trim()});
 }
 async function more(item,options){
  const pick=await vscode.window.showQuickPick(options.map(label=>({label})),{title:message(item),placeHolder:'Choose an answer'});
  if(pick)await answer(item,{choice:pick.label});
 }
 async function toast(item){
  const act_=canAct(),{options,more:hasMore,text}=buttonsFor(item,act_&&item.needs_answer);
  const choices=options.map(title=>({title,option:true})),extra=hasMore?{title:'More'}:null;
  const items=[...choices,...(extra?[extra]:[]),...(text?[TYPE]:[]),OPEN];
  const show=item.needs_answer&&item.kind==='approval'?vscode.window.showWarningMessage:vscode.window.showInformationMessage;
  const picked=await show.call(vscode.window,message(item),{},...items);
  if(!picked){try{await act({id:item.id,action:'shown'});}catch{}return;}   // the cross: hidden here, still open in Notifications
  if(picked===OPEN)return openPage();
  if(picked===TYPE)return typed(item);
  if(picked===extra)return more(item,item.options);
  return answer(item,{choice:picked.title});
 }
 async function tick(){
  const value=await api('/api/notifications',undefined,{fresh:true});
  old=false;
  const items=value.items||[],open=new Set(items.filter(i=>i.state==='open').map(i=>i.id));
  badge(Number(value.counts?.open??items.filter(i=>i.state==='open'&&(i.needs_answer||i.source==='app'||i.kind==='waiting')).length)||0);
  for(const id of handled)if(!open.has(id))handled.delete(id);
  const fresh=items.filter(i=>i.state==='open'&&(i.needs_answer||(i.source==='app'&&!i.shown))&&!handled.has(i.id));
  if(!fresh.length)return;
  fresh.forEach(i=>handled.add(i.id));
  // One toast per item across windows: only the client the server names as claimant shows it.
  let claim;try{claim=await act({ids:fresh.map(i=>i.id),action:'shown'});}catch(e){fresh.forEach(i=>handled.delete(i.id));throw e;}
  const mine=new Set(claim.claimed||[]);
  for(const item of fresh)if(mine.has(item.id))void toast(item).catch(report);
 }
 async function loop(){
  if(stopped||busy)return;busy=true;
  try{await tick();}catch(e){if(e&&e.status===404)old=true;}   // an old server has no feed: stay silent
  finally{busy=false;if(!stopped)timer=setTimer(loop,old?slow:every);}
 }
 return {start(){stopped=false;timer=setTimer(loop,0);},stop(){stopped=true;clearTimer(timer);},tick,handled};
}
module.exports={create,buttonsFor,message};
