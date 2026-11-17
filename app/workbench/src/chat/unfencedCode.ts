/** Presentation only; the saved message and its copy action retain the original text. */
export function formatUnfencedCode(text:string):string{
 if(!text||text.length>500_000)return text;
 const lines=text.split('\n'),out:string[]=[],tick=String.fromCharCode(96),fences=new RegExp('^ {0,3}('+tick+'{3,}|~{3,})');
 let fence:{char:string;size:number}|undefined;
 const wrap=(value:string,language:string)=>{
  const longest=Math.max(0,...(value.match(new RegExp(tick+'+','g'))||[]).map(v=>v.length));
  const marker=tick.repeat(Math.max(3,longest+1));
  return marker+language+'\n'+value+'\n'+marker;
 };
 for(let i=0;i<lines.length;i++){
  const line=lines[i],mark=fences.exec(line);
  if(fence){out.push(line);if(mark&&mark[1][0]===fence.char&&mark[1].length>=fence.size&&line.slice(mark[0].length).trim()==='')fence=undefined;continue}
  if(mark){fence={char:mark[1][0],size:mark[1].length};out.push(line);continue}
  if(/^(?: {4}|\t)/.test(line)){out.push(line);continue}
  const first=line.trim();let code='',end=i,language='';
  if(first.startsWith('{')||first.startsWith('[')){
   let candidate='';
   for(let j=i;j<Math.min(lines.length,i+300);j++){
    candidate+=(j>i?'\n':'')+lines[j];if(candidate.length>150_000)break;
    try{const value=JSON.parse(candidate);if(value!==null&&typeof value==='object'){code=candidate;end=j;language='json';break}}catch{}
   }
  }
  if(!code&&/^<(?:!doctype\s+html\b|(?:html|head|body|div|span|p|section|article|main|header|footer|nav|ul|ol|li|table|form|button|script|style|svg|h[1-6])(?:\s|>))/i.test(first)){
   const tag=/^<([a-z][\w:-]*)\b/i.exec(first)?.[1]||'html',closing=new RegExp('</'+tag+'\\s*>\\s*$','i');
   let candidate='';
   for(let j=i;j<Math.min(lines.length,i+500);j++){
    candidate+=(j>i?'\n':'')+lines[j];if(candidate.length>250_000)break;
    if(closing.test(candidate)){code=candidate;end=j;language='html';break}
   }
  }
  if(!code&&/^(?:(?:export\s+)?(?:async\s+)?function\s+\w+\s*\(|(?:const|let|var)\s+\w+\s*=|(?:async\s+)?def\s+\w+\s*\([^)]*\)\s*:|from\s+[\w.]+\s+import\s+|import\s+.+\s+from\s+['"])/.test(first)){
   const block=[line],python=/^(?:(?:async\s+)?def\s|from\s)/.test(first);let j=i+1;
   while(j<lines.length&&lines[j].trim()&&block.length<150){
    const next=lines[j];if(fences.test(next)||(python&&!/^\s/.test(next)))break;
    if(!python&&!/^\s/.test(next)&&!/[;{}()[\]=]/.test(next))break;
    block.push(next);j++;if(!python&&/^[}\]];?\s*$/.test(next))break;
   }
   if(block.length>1&&(python?block.slice(1).some(v=>/^\s+(?:return|print\(|\w+\s*=)/.test(v)):block.slice(1).some(v=>/^\s*(?:return\b|[}\]];?\s*$|(?:const|let)\b)/.test(v)))){
    code=block.join('\n');end=j-1;language=python?'python':'javascript';
   }
  }
  if(code){out.push('',wrap(code,language),'');i=end}else out.push(line);
 }
 return out.join('\n');
}
