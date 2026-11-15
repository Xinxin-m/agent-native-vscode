export type Destination = 'tab'|'preview'|'right'|'below'|'browser-tab'|'browser-window'|'browser-current'|'default-browser'|'system'|'editor';
export interface Resource {
  kind:'chat'|'file'|'welcome'|'settings'|'overview'|'url'|'project';
  projectId?:string; url?:string; key?:string; draftId?:string; runId?:string; title?:string; meta?:any;
  host_id?:string; path?:string; root_id?:string; file_id?:string; cwd?:string; line?:number; page?:number; anchor?:string; mode?:string;
}
export interface Preferences {
  theme:string; fontSize:number; lineHeight:number; readingWidth:number; openDefault:Destination; maxAutoPanes:number;
}
export interface WorkbenchContextValue {
  data:any; attention?:any[]; liveSessions?:any[]; accounts:any[]; hosts:any[]; runs:any[]; preferences:Preferences;
  open:(resource:Resource,destination?:Destination,groupId?:string)=>void;
  notify:(message:string)=>void; refresh:()=>Promise<void>;
  setPreferences:(patch:Partial<Preferences>)=>void;
  updateTitle:(resource:Resource,title:string)=>void;
}
