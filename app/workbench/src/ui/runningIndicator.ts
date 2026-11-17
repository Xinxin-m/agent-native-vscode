// The running-chat line animates only while its row is on screen and the page is visible.
export function installRunningIndicator(root:Document){
 const seen=new IntersectionObserver(entries=>{for(const e of entries)(e.target as HTMLElement).toggleAttribute('data-offscreen',!e.isIntersecting)});
 const rows=(node:Node,fn:(row:Element)=>void)=>{if(!(node instanceof Element))return;if(node.matches('.sidebar-chat'))fn(node);node.querySelectorAll('.sidebar-chat').forEach(fn)};
 const watch=(node:Node)=>rows(node,row=>seen.observe(row));
 new MutationObserver(records=>{for(const r of records){r.addedNodes.forEach(watch);r.removedNodes.forEach(node=>rows(node,row=>seen.unobserve(row)))}}).observe(root.body,{childList:true,subtree:true});
 const visibility=()=>root.documentElement.toggleAttribute('data-page-hidden',root.hidden);
 root.addEventListener('visibilitychange',visibility);visibility();watch(root.body);
}
