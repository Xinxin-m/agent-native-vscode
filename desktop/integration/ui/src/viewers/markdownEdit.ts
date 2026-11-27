// Monaco's range diff matches the native Markdown editor's preservation algorithm.
import {stringDiff} from 'monaco-editor/base/common/diff/diff.js';
type Diff={originalStart:number;originalLength:number;modifiedStart:number;modifiedLength:number};
export function frontmatter(text:string){return text.match(/^(?:\uFEFF)?---\r?\n[\s\S]*?\r?\n---(?:\r?\n|$)/)?.[0]||'';}
export function editMarkdown(original:string,normalized:string,edited:string){
 if([original,normalized,edited].some(value=>typeof value!=='string'||value.length>1000000))throw Error('Rendered Markdown editing supports documents up to 1 MB.');
 if(normalized===edited)return original;
 const segments:{start:number;end:number;offset:number}[]=[];let sourceOffset=0,renderedOffset=0;
 for(const change of stringDiff(original,normalized,true) as Diff[]){if(change.modifiedStart>renderedOffset)segments.push({start:renderedOffset,end:change.modifiedStart,offset:sourceOffset-renderedOffset});sourceOffset=change.originalStart+change.originalLength;renderedOffset=change.modifiedStart+change.modifiedLength;}
 if(renderedOffset<=normalized.length)segments.push({start:renderedOffset,end:normalized.length,offset:sourceOffset-renderedOffset});
 const edits=(stringDiff(normalized,edited,true) as Diff[]).map(change=>{const start=change.originalStart,end=start+change.originalLength;let candidates=segments.filter(segment=>start>=segment.start&&end<=segment.end);if(start===end){const preceding=candidates.filter(segment=>segment.end===start&&segment.start<start);if(preceding.length)candidates=preceding;}if(!candidates.length||new Set(candidates.map(segment=>segment.offset)).size!==1)throw Error('This edit crosses hidden or unsupported Markdown. The original is unchanged; copy your rendered draft or undo the edit.');const offset=candidates[0].offset;return {start:start+offset,end:end+offset,text:edited.slice(change.modifiedStart,change.modifiedStart+change.modifiedLength)};});
 let result=original;for(const edit of edits.reverse())result=result.slice(0,edit.start)+edit.text+result.slice(edit.end);return result;
}
export function imageUrl(src:string,rootId:string,relativePath:string){
 if(/^https?:\/\//i.test(src))return src;
 if(!rootId||!src||/^(?:[a-z][\w+.-]*:|\/|#)/i.test(src))return '';
 const parts=relativePath.split('/').slice(0,-1);for(const part of src.split('/')){if(!part||part==='.')continue;if(part==='..'){if(!parts.length)return '';parts.pop();}else parts.push(part);}
 return '/api/files/content?'+new URLSearchParams({root_id:rootId,path:parts.join('/')});
}

export const renderedRecoveryKey=(host:string,path:string)=>'library.rendered-markdown-recovery:'+JSON.stringify([host,path]);
export function findRanges(root:Node,term:string):Range[]{
 if(!term.trim())return [];const ranges:Range[]=[],walker=document.createTreeWalker(root,NodeFilter.SHOW_TEXT),query=term.toLocaleLowerCase();
 while(walker.nextNode()){const node=walker.currentNode as Text,lower=node.data.toLocaleLowerCase();let at=lower.indexOf(query);while(at>=0){const range=document.createRange();range.setStart(node,at);range.setEnd(node,at+term.length);ranges.push(range);at=lower.indexOf(query,at+term.length);}}
 return ranges;
}

export function readRenderedRecovery(storage:Pick<Storage,'getItem'>,key:string){const raw=storage.getItem(key);if(!raw)return '';try{const value=JSON.parse(raw);return typeof value?.text==='string'?value.text:raw}catch{return raw}}
export function saveRenderedRecovery(storage:Pick<Storage,'getItem'|'setItem'>,key:string,value:string,owner:string){const previous=storage.getItem(key);if(previous){let record:any;try{record=JSON.parse(previous)}catch{}if(record?.owner!==owner)throw Error('An earlier recovery draft already exists. Download it before replacing it.');}storage.setItem(key,JSON.stringify({owner,text:value}));}

export function renderedEditorFind(mode:string,editing:boolean){return editing&&(mode==='render'||mode==='split');}
