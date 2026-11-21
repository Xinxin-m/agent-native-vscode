import {it,expect} from 'vitest';
import {createConversationStore} from './conversations';
it('clears an accepted compact command, preserving files and a newer draft',()=>{
 const data=new Map<string,string>(),s=createConversationStore({getItem:k=>data.get(k)||null,setItem:(k,v)=>{data.set(k,v)}});s.get('a');s.patch('a',{text:'/compact',attachments:[{id:'f',name:'f',mime:'text/plain',size:1}]});s.prepare('a',{message:'/compact',operation:'compact'});s.accepted('a',{id:'r'});expect(s.get('a').text).toBe('');expect(s.get('a').attachments).toHaveLength(1);s.patch('a',{text:'/compact'});s.prepare('a',{message:'/compact',operation:'compact'});s.patch('a',{text:'next draft'});s.accepted('a',{id:'r2'});expect(s.get('a').text).toBe('next draft');
});
