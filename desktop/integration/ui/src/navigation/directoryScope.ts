import {isRootChat,uniqueChats} from './chats';
import {projectMembers} from './projectTree';
import {inWorkspace,type WorkspaceRoot} from './workspaceScope';

type DirectoryScope={roots:WorkspaceRoot[];choice:string};
const newestFirst=(a:any,b:any)=>String(b.ts_last||'').localeCompare(String(a.ts_last||''));

export function directoryChats(chats:any[],{roots,choice}:DirectoryScope){
 return uniqueChats(chats).filter(c=>isRootChat(c)&&!c.automation&&!c.archived&&inWorkspace(c,roots,choice)).sort(newestFirst);
}

export function directoryProjects(projects:any[],chats:any[],assignments:Record<string,string>,scope:DirectoryScope){
 const byProject=new Map<string,any[]>();
 for(const chat of directoryChats(chats,scope)){
  const id=assignments[chat.key];
  if(!id)continue;
  if(!byProject.has(id))byProject.set(id,[]);
  byProject.get(id)!.push(chat);
 }
 return projects.map(project=>{
  const members=[...projectMembers(projects,project.id)].flatMap(id=>byProject.get(id)||[]).sort(newestFirst);
  return {...project,chats:members,last:members[0]?.ts_last||''};
 }).filter(project=>scope.choice==='entire'||project.chats.length>0)
  .sort((a,b)=>String(b.last).localeCompare(String(a.last))||a.name.localeCompare(b.name));
}
