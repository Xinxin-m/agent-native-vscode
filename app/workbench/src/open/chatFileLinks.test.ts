import {describe,expect,it} from 'vitest';
import {chatFileLink,withChatFileSequence} from './chatFileLinks';

function chatFixture(){
 const reading={querySelectorAll:()=>links};
 const otherReading={};
 const markdown={dataset:{cwd:'/workspace',host:'cluster',chatKey:'codex-example'}};
 function link(href:string,{embedded=false,image=false}={}){
  return {
   getAttribute:(name:string)=>name===(image?'data-local-image':'href')?href:null,
   closest:(selector:string)=>selector==='.chat-reading'?(embedded?otherReading:reading):markdown
  } as unknown as Element;
 }
 const first=link('report.pdf#page=3'),duplicate=link('report.pdf#page=3'),image=link('plots/result.svg',{image:true});
 const links=[first,link('https://example.test'),link('embedded.pdf',{embedded:true}),duplicate,image];
 return {first,duplicate,image};
}

describe('files linked in a chat',()=>{
 it('collects links in chat order with host and chat reference, skipping websites, duplicates and embedded chats',()=>{
  const {first,duplicate,image}=chatFixture();
  const original=chatFileLink(duplicate)!;
  const sequence=withChatFileSequence(duplicate,original).meta.fileSequence;
  expect(sequence.index).toBe(0);
  expect(sequence.files).toEqual([
   {...chatFileLink(first),meta:{chat_key:'codex-example'}},
   {...chatFileLink(image),meta:{chat_key:'codex-example'}}
  ]);
  expect(sequence.files[0]).toMatchObject({host_id:'cluster',path:'/workspace/report.pdf',page:3});
  expect(withChatFileSequence(image,chatFileLink(image)!).meta.fileSequence.index).toBe(1);
 });
});
