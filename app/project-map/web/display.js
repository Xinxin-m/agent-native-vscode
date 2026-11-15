(function(root,factory){const api=factory();if(typeof module==='object'&&module.exports)module.exports=api;else root.GraphDisplay=api;})(globalThis,function(){
 const active=n=>['working','waiting'].includes(n.status?.state);
 function descendants(view,root){const ids=new Set([root]);let changed=true;while(changed){changed=false;for(const n of view.nodes)if(n.kind!=='principal'&&!ids.has(n.id)&&(ids.has(n.parent)||n.root===root||n.parents?.some(p=>ids.has(p))||n.referenced_by?.some(p=>ids.has(p)))){ids.add(n.id);changed=true}}return view.nodes.filter(n=>n.id!==root&&ids.has(n.id));}
 function visible(view,hide){if(!hide)return view;const ids=new Set(view.nodes.filter(active).map(n=>n.id));let changed=true;while(changed){changed=false;for(const n of view.nodes.filter(n=>ids.has(n.id)))for(const p of [n.parent,n.root,...n.parents||[],...n.referenced_by||[]])if(p&&!ids.has(p)){ids.add(p);changed=true}}
 return {...view,nodes:view.nodes.filter(n=>ids.has(n.id)),edges:view.edges.filter(e=>ids.has(e.from)&&ids.has(e.to)),resources:view.resources.map(r=>({...r,links:r.links.filter(l=>ids.has(l.session))})).filter(r=>r.links.length),events:view.events.filter(e=>ids.has(e.node)),other_runs:(view.other_runs||[]).filter(active)};}
 function energized(edge,view,live,connected){return !!(live&&connected&&view.nodes.some(n=>(n.id===edge.from||n.id===edge.to)&&n.status?.state==='working'));}
 return {active,descendants,visible,energized};
});
