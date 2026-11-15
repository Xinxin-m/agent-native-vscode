export interface SlashCommand {name:string;description:string;insert:string;kind?:string;operation?:string;argumentHint?:string;aliases?:string[];native?:boolean;disabled?:string}
export function slashQuery(text:string):string|null {if(!text.startsWith('/'))return null;const value=text.slice(1).toLowerCase();return !/\s/.test(value)||'remote control'.startsWith(value)?value:null;}
export function commandName(text:string){const normalized=text.trim().replace(/^\/remote\s+control(?=\s|$)/i,'/remote-control');const match=normalized.match(/^\/([\w.:-]+)(?:\s+([\s\S]*))?$/);return match?{name:match[1].toLowerCase(),args:match[2]?.trim()||''}:null;}
export function commandCatalog(provider:string,discovered:SlashCommand[],native:SlashCommand[]=[]):SlashCommand[]{
 const core:SlashCommand[]=[
 {name:'compact',insert:'/compact',description:'Free context in this native conversation',operation:'compact'},
 {name:'model',insert:'/model',description:'Choose the agent, model and thinking effort',operation:'settings'},
 {name:'new',insert:'/new',description:'Open a new conversation',operation:'new'},
 {name:'help',insert:'/help',description:'Browse available commands',operation:'help'},
 ...(provider==='claude'?[{name:'remote-control',insert:'/remote-control',aliases:['rc','remote control'],description:'Connect this conversation to the Claude app',operation:'remote_control'}]:[{name:'goal',insert:'/goal ',description:'Set a goal for this conversation',argumentHint:'<objective>',operation:'goal'}])];
 const result=new Map(core.map(c=>[c.name,c]));
 for(const c of [...native,...discovered])if(c.name&&!result.has(c.name))result.set(c.name,c);
 return [...result.values()];
}
export function matchingCommands(catalog:SlashCommand[],query:string){return catalog.filter(c=>[c.name,...(c.aliases||[])].some(n=>n.toLowerCase().includes(query))||c.description.toLowerCase().includes(query)).sort((a,b)=>Number(b.name.startsWith(query))-Number(a.name.startsWith(query)));}
