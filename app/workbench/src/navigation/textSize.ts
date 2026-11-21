export const textSizes = [{value:12,label:'Smallest'},{value:13,label:'Small'},{value:14,label:'Medium'},{value:16,label:'Large'},{value:18,label:'Largest'}] as const;
export function nearestTextSize(value:number):number { if(!Number.isFinite(value))return 14; return textSizes.reduce((best,item)=>Math.abs(item.value-value)<Math.abs(best-value)?item.value:best,14 as number); }
export function textScale(value:number):number { return nearestTextSize(value)/14; }
