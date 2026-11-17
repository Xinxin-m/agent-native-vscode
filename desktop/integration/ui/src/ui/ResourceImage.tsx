import {useEffect,useState,type ImgHTMLAttributes} from 'react';
export async function imageSource(path:string):Promise<string>{
 const rpc=(window as any).libraryRpc;
 return rpc&&/^\/api\/(attachment|files\/content)\?/.test(path)?rpc('imageResource',{path}):path;
}
export function ResourceImage({src,alt,...props}:ImgHTMLAttributes<HTMLImageElement>){
 const [resolved,setResolved]=useState<string>(),[error,setError]=useState(false);
 useEffect(()=>{let active=true;setResolved(undefined);setError(false);if(src)void imageSource(src).then(value=>{if(active)setResolved(value)}).catch(()=>{if(active)setError(true)});return()=>{active=false}},[src]);
 return <img {...props} src={resolved} alt={error?(alt||'Image')+' (preview unavailable)':alt}/>;
}
