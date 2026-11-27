export type Destination = 'tab'|'preview'|'right'|'below'|'browser-tab'|'browser-window'|'browser-current'|'default-browser'|'default-browser-window'|'system'|'editor';
export interface Resource {
  kind:'project-map'|'chat'|'file'|'welcome'|'settings'|'overview'|'url'|'projects'|'recent'|'sessions'|'files'|'notifications';
  project_id?:string; url?:string; key?:string; draftId?:string; runId?:string; title?:string; meta?:any;
  host_id?:string; path?:string; root_id?:string; file_id?:string; cwd?:string; line?:number; page?:number; anchor?:string; mode?:string;
}
export interface Preferences {
  utilityPopups?:boolean;
  theme:string; fontSize:number; lineHeight:number; readingWidth:number; openDefault:Destination; maxAutoPanes:number; agentMapView?:'popup'|'inline'; defaultProvider?:'claude'|'codex'|'ask'; defaultModels?:Record<string,string>; thinking?:'collapsed'|'expanded'|'hidden'; toolOutput?:'folded'|'expanded'; composerMax?:number|null; foldComposer?:boolean; sendKey?:'enter'|'mod'; whileWorking?:'queue'|'steer'; openAtLatest?:boolean; tabGroups?:'manual'|'project'; groupFiles?:boolean; confirmMoves?:'some'|'always'|'never'; showHidden?:boolean; liveRefresh?:number;
}
export interface WorkbenchContextValue {
  data:any; stateError?:string; connect?:()=>void; attention?:any[]; liveSessions?:any[]; accounts:any[]; pendingNoticeAccounts?:any[]; hosts:any[]; runs:any[]; preferences:Preferences;
  open:(resource:Resource,destination?:Destination,groupId?:string)=>void;
  notify:(message:string)=>void; refresh:()=>Promise<void>;
  setPreferences:(patch:Partial<Preferences>)=>void;
  updateTitle:(resource:Resource,title:string)=>void;
  replace?:(panelId:string|undefined,resource:Resource)=>void;
}
