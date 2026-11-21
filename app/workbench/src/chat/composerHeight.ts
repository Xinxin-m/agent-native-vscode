export function composerHeightLimit(line:number,padding:number,paneHeight:number,choice?:number|null):number {
 const minimum=2*line+padding;
 const cap=typeof choice==='number'&&Number.isFinite(choice)&&choice>0?paneHeight*choice:Math.min(8*line+padding,paneHeight*.25,240);
 return Math.max(minimum,cap);
}
