/* Container-sized geometry; compact spacing never relies on shrinking text. */
(function(root,factory){const api=factory();if(typeof module==='object'&&module.exports)module.exports=api;else root.ProjectLayout=api;})(globalThis,function(){
 const clamp=(v,lo,hi)=>Math.max(lo,Math.min(hi,v));
 function pageSize(width,height){return width<700?1:clamp(Math.floor((height-190)/140),2,8)}
 function arrange({width,height,top=110,roots=[],inputs=[],outputs=[],children={},layout='flow'}){
  const narrow=width<700,center=width/2,span=Math.min(width-32,980),groupWidth=narrow?Math.min(164,(span-12)/2):156,reach=Math.min(300,(span-groupWidth)/2),nodes=[],positions={},labels=[];
  const put=(n,x,y,w)=>{if(positions[n.id])return;positions[n.id]=[x,y];nodes.push({...n,x,y,width:w})};
  if(layout==='orbit'){
   // Conversations form the inner orbit; context and artifacts sit on the outer orbit.
   const rings=[roots,...roots.map(n=>children[n.id]||[]).filter(a=>a.length),[...inputs,...outputs]].filter(a=>a.length);
   let radiusX=0,radiusY=0;
   for(const [ring,items] of rings.entries()){
    radiusX=Math.min((width-200)/2,ring===0?Math.max(0,(width-200)/3):radiusX+100);
    radiusY=Math.max(radiusY+110,items.length*(ring===0?105:80)/(2*Math.PI));
   }
   const cy=top+radiusY+65;let rx=0,ry=0;
   for(const [ring,items] of rings.entries()){
    rx=Math.min((width-200)/2,ring===0?Math.max(0,(width-200)/3):rx+100);
    ry=Math.max(ry+110,items.length*(ring===0?105:80)/(2*Math.PI));
    items.forEach((n,i)=>{const angle=-Math.PI/2+i*2*Math.PI/items.length;put(n,center+rx*Math.cos(angle),cy+ry*Math.sin(angle),ring===0?184:Math.min(140,width-32))});
   }
   const bottom=Math.max(top,...nodes.map(n=>n.y+60));
   return {positions,nodes,labels,width,height:Math.max(height,bottom+65),contentBottom:bottom,narrow,nodeFont:12};
  }
  const rowGap=clamp((height-top-80)/Math.max(roots.length,1),128,148);let y=top+60;
  for(const [i,n] of roots.entries()){
   const x=center;put(n,x,y,184);
   const kids=(children[n.id]||[]).filter(c=>!positions[c.id]);kids.forEach((c,j)=>put(c,x+(kids.length===1?0:(j%2?73:-73)),y+132+Math.floor(j/2)*94,140));
   y+=rowGap+Math.ceil(kids.length/2)*94;
  }
  const agentBottom=nodes.length?Math.max(...nodes.map(n=>n.y+56)):top+30;
  if(narrow){
   let start=agentBottom+76;labels.push({x:16,y:start-38,text:'CONTEXT & OUTPUTS'});
   [...inputs,...outputs].forEach((n,i)=>put(n,center+(i%2?1:-1)*(groupWidth+12)/2,start+Math.floor(i/2)*60,groupWidth));
  }else{
   labels.push({x:center-reach-groupWidth/2,y:top-8,text:'INPUTS'},{x:center-30,y:top-8,text:'AGENTS'},{x:center+reach-groupWidth/2,y:top-8,text:'OUTPUTS'});
   inputs.forEach((n,i)=>put(n,center-reach,top+55+i*64,groupWidth));
   outputs.forEach((n,i)=>put(n,center+reach,top+55+i*64,groupWidth));
  }
  const bottom=Math.max(top,...nodes.map(n=>n.y+(roots.some(r=>r.id===n.id)?60:40)));
  return {positions,nodes,labels,width,height:Math.max(height,bottom+65),contentBottom:bottom,narrow,nodeFont:12};
 }
 return {arrange,pageSize};
});
