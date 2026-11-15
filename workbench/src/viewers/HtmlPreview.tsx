import {useEffect,useState} from 'react';
import {api} from '../api/client';
import {isSafePreviewUrl,previewLocation,previewAuthorized} from './helpers';
export function HtmlPreview({host,path,refresh=0,allowNetwork=false,version,registeredRoot=false,onUrl}:{host:string;path:string;refresh?:number;allowNetwork?:boolean;version?:string;registeredRoot?:boolean;onUrl?:(url:string)=>void}){
 const [url,setUrl]=useState(''),[error,setError]=useState(''),[approvedScope,setApprovedScope]=useState('');
 const authorized=previewAuthorized(registeredRoot,approvedScope,host,path),directory=previewLocation(path).artifact_directory;
 useEffect(()=>{let active=true;setUrl('');setError('');onUrl?.('');if(!authorized)return()=>{active=false;};api('/api/previews',{host_id:host,...previewLocation(path),allow_network:allowNetwork}).then(data=>{if(!isSafePreviewUrl(data.url,location.origin))throw new Error('The server returned a preview URL without an isolated origin.');if(active){setUrl(data.url);onUrl?.(data.url);}}).catch(e=>{if(active)setError(e.message);});return()=>{active=false;};},[host,path,refresh,allowNetwork,authorized,version]);
 if(!authorized)return <div className="fv-empty"><p>This website can load scripts, images and data from:</p><code>{directory}</code><button onClick={()=>setApprovedScope(JSON.stringify([host,directory]))}>Open website preview</button></div>;
 if(error)return <div className="fv-empty" role="alert"><p>{error}</p><p>For an application that requires its own server, open its development-server URL in your browser.</p></div>;
 if(!url)return <div className="fv-empty" role="status">Opening website preview…</div>;
 return <iframe className="fv-html" src={url} sandbox="allow-scripts allow-same-origin" referrerPolicy="no-referrer" title={'Website preview: '+path.split('/').pop()}/>;
}
