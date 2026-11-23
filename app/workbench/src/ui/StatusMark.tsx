import React from 'react';
import {statusWords,type RowStatus} from '../navigation/chats';
// One mark in a 28 px column so every title shares one left edge. Meaning is in words (tooltip and accessible name), never color alone.
export function StatusMark({status,label,compact}:{status:RowStatus;label?:string;compact?:boolean}){
 const words=status?label||statusWords[status]:'';
 return <span className={'mk-slot'+(compact?' compact':'')}>{status&&<i className={'mk '+({waiting:'wait',working:'work',failed:'fail',new:'new'} as const)[status]} role="img" aria-label={words} title={words}/>}</span>;
}
