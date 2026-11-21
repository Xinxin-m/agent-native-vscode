export function toggleSelection(current:ReadonlySet<string>,key:string):Set<string>{
 const next=new Set(current);if(next.has(key))next.delete(key);else next.add(key);return next;
}
export function selectRange(order:readonly string[],anchor:number,target:number):Set<string>{
 if(anchor<0||target<0)return new Set();return new Set(order.slice(Math.min(anchor,target),Math.max(anchor,target)+1));
}
export async function applyToEach(keys:readonly string[],run:(key:string)=>Promise<unknown>){
 const succeeded:string[]=[],failed:{key:string;message:string}[]=[];
 for(const key of keys){try{await run(key);succeeded.push(key);}catch(error){failed.push({key,message:error instanceof Error?error.message:String(error)});}}
 return {succeeded,failed};
}
export function createdProjectId(projects:{id:string;name:string}[],before:ReadonlySet<string>,name:string):string{
 const matches=projects.filter(p=>p.name===name&&!before.has(p.id));if(matches.length!==1)throw Error('Project was created, but its identity could not be confirmed. Refresh before moving conversations.');return matches[0].id;
}
