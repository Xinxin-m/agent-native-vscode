/* Shared presentation boundary: provider wire formats never become UI text. */
(function(root,factory){const api=factory();if(typeof module==='object'&&module.exports)module.exports=api;else root.SessionContent=api;})(globalThis,function(){
 function normalize(value){return String(value??'')
  .replace(/\uE200[^\uE201]*(?:\uE201|$)/g,'')
  .replace(/<\/?WritingBlock\b[^>]*(?:>|$)/gi,'')
  .replace(/&lt;\/?WritingBlock\b[\s\S]*?(?:&gt;|$)/gi,'')
  .replace(/<!--\s*(?:END|CONTINUE|ChatGPT capture|prompt:)[\s\S]*?(?:-->|$)/gi,'')
  .replace(/[\uE200-\uE205]/g,'');}
 function create(md,sanitize){
  const isProse=t=>/^(?:markdown|md)\b/i.test(t.info.trim())||(!t.info.trim()&&/^\s*(?:#{1,6}\s|\*\*\S|\|.+\|)/.test(t.content));
  const fence=md.renderer.rules.fence,code=md.renderer.rules.code_block;
  md.renderer.rules.text=(tokens,i)=>md.utils.escapeHtml(tokens[i].content.replace(/`{3,}([a-zA-Z]+)?/g,' $1 ').replace(/(?:\*\*|__|~~)/g,''));
  md.renderer.rules.fence=(tokens,i,options,env={},renderer)=>{
   const t=tokens[i];if(isProse(t)&&(env.proseDepth||0)<8)return md.render(normalize(t.content),{...env,proseDepth:(env.proseDepth||0)+1});
   return '<details class="code-content"><summary>Code attachment'+(t.info?' · '+md.utils.escapeHtml(t.info.split(/\s/)[0]):'')+'</summary><button class="copy-code" data-copy-code="true">Copy code</button>'+fence(tokens,i,options,env,renderer)+'</details>';
  };
  md.renderer.rules.code_block=(tokens,i,options,env,renderer)=>'<details class="code-content"><summary>Code attachment</summary><button class="copy-code" data-copy-code="true">Copy code</button>'+code(tokens,i,options,env,renderer)+'</details>';
  function plain(value,compact=true,depth=0){
   if(depth>8)return '';
   const tokens=md.parse(normalize(value),{});let parts=[];
   function inline(items){return(items||[]).map(t=>t.children?inline(t.children):['text','code_inline','image'].includes(t.type)?t.content:['softbreak','hardbreak'].includes(t.type)?'\n':'').join('');}
   for(const t of tokens){
    if(t.type==='inline')parts.push(inline(t.children));
    else if(t.type==='fence')parts.push(isProse(t)?plain(t.content,compact,depth+1):'Code attachment');
    else if(t.type==='code_block')parts.push('Code attachment');
   }
   const text=parts.join(compact?' ':'\n\n').replace(/(?:\*\*|__|~~)/g,'').replace(/`+/g,'').trim();
   return compact?text.replace(/\s+/g,' '):text;
  }
  function event(e){
   const labels={submitted:'Request sent',start:'Working',complete:'Reply ready',retrieved:'Answer saved',unavailable:'Activity unavailable',failed:'Failed',interrupted:'Stopped'};
   if(labels[e.kind])return labels[e.kind];
   if(e.kind==='tool')return 'Tool activity';
   return plain(e.label)||({assistant:'Reply recorded',user:'Request recorded'}[e.kind]||'Activity recorded');
  }
  return {html:value=>sanitize(md.render(normalize(value),{})),plain,event};
 }
 return {normalize,create};
});
