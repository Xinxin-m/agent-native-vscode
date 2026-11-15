/** Only transient menus belong here; output, recovery and evidence disclosures stay open. */
const popupSelector='details.chat-settings[open], details.fv-menu[open], details.chat-scope-menu[open], .context-menu details[open]';
export function dismissPopupDetails(menus:Iterable<{open:boolean;contains:(target:any)=>boolean}>,target?:EventTarget|null){
 for(const menu of menus)if(!target||!menu.contains(target))menu.open=false;
}
export function installPopupDismissal(doc:Document){
 const outside=(event:PointerEvent)=>dismissPopupDetails(doc.querySelectorAll<HTMLDetailsElement>(popupSelector),event.target);
 const escape=(event:KeyboardEvent)=>{if(event.key==='Escape')dismissPopupDetails(doc.querySelectorAll<HTMLDetailsElement>(popupSelector));};
 // Delay action dismissal until React has dispatched the chosen action.
 const selected=(event:MouseEvent)=>{const target=event.target as Element;if(!target?.closest)return;const action=target.closest('button,a');const menu=action?.closest<HTMLDetailsElement>('details.fv-menu, .context-menu details');if(menu)queueMicrotask(()=>{menu.open=false;});};
 doc.addEventListener('pointerdown',outside,true);doc.addEventListener('keydown',escape,true);doc.addEventListener('click',selected,true);
 return()=>{doc.removeEventListener('pointerdown',outside,true);doc.removeEventListener('keydown',escape,true);doc.removeEventListener('click',selected,true);};
}
