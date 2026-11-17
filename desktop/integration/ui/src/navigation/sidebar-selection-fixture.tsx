// @ts-nocheck
import React from 'react';import{createRoot}from'react-dom/client';import{Sidebar}from'/src/navigation/Sidebar.tsx';import{WorkbenchContext}from'/src/state/context.tsx';import'/src/native/native.css';
 const chats=['a','b','c'].map((key,i)=>({key,title:'Chat '+key,source:'claude',ts_last:new Date(Date.now()-i*1000).toISOString()}));
 const data={chats,projects:{projects:[{id:'p',name:'Project',pinned:true}],assignments:{a:'p',b:'p',c:'p'}}};
 window.calls=[];window.opened=[];window.confirmed=true;window.failB=false;
 window.libraryRpc=async(action,args)=>{if(action!=='api')return;window.calls.push([args.path,args.body]);if(window.failB&&args.body?.keys?.includes('b'))throw Error('denied');if(args.path==='/api/projects')return{ok:true,projects:{projects:[...data.projects.projects,{id:'new',name:args.body.name}]}};return {ok:true};};
 const value={data,runs:[],attention:[],liveSessions:[],hosts:[],open:r=>window.opened.push(r.key),refresh:async()=>{},notify:s=>window.notice=s,confirm:async()=>{window.confirmCount=(window.confirmCount||0)+1;return window.confirmed}};
 localStorage.setItem('workbench-projects',JSON.stringify({p:true}));createRoot(document.getElementById('root')).render(React.createElement(WorkbenchContext.Provider,{value},React.createElement(Sidebar,{mode:'chats',setMode:()=>{},onContext:()=>{},hideFiles:true})));
