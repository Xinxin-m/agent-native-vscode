import './sessionReferenceMenu.js';
import {api} from '../api/client';
export function sessionReferenceMenu(event:any,key:string|undefined,notify:(text:string)=>void){if(key)(window as any).LibrarySessionReference.open(event,key,{request:api,notify})}
