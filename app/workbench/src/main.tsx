import React from 'react';
import {createRoot} from 'react-dom/client';
import App from './App';
import {WorkspaceScopeProvider} from './navigation/WorkspaceSelector';
import {installTooltips} from './ui/tooltips';
import {installPopupDismissal} from './ui/dismiss';
import {installRunningIndicator} from './ui/runningIndicator';
installPopupDismissal(document);
installTooltips(document);
installRunningIndicator(document);
class WorkspaceRecovery extends React.Component<React.PropsWithChildren,{error?:string;details?:string}> {
 state:{error?:string;details?:string}={};
 static getDerivedStateFromError(error:unknown){return {error:error instanceof Error?error.message:String(error)};}
 componentDidCatch(error:Error,info:React.ErrorInfo){this.setState({details:[error.stack,info.componentStack].filter(Boolean).join('\n')});}
 render(){
  if(this.state.error!==undefined)return <main className="viewer-error" role="alert"><h1>Library could not display this workspace</h1><p>{this.state.error}</p><button onClick={()=>location.reload()}>Reload workspace</button><button onClick={()=>{
   const url=new URL(location.href);url.search='';url.hash='';url.searchParams.set('window',crypto.randomUUID());location.assign(url);
  }}>Open an empty workspace</button>{this.state.details&&<details><summary>Error details</summary><pre>{this.state.details}</pre></details>}</main>;
  return this.props.children;
 }
}
createRoot(document.getElementById('root')!).render(<WorkspaceRecovery><WorkspaceScopeProvider><App/></WorkspaceScopeProvider></WorkspaceRecovery>);
