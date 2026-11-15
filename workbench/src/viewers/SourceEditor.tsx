import {useEffect,useRef} from 'react';
import Editor,{DiffEditor,loader} from '@monaco-editor/react';
import * as monaco from 'monaco-editor';
import EditorWorker from 'monaco-editor/editor/editor.worker.js?worker';
import JsonWorker from 'monaco-editor/language/json/json.worker.js?worker';
import CssWorker from 'monaco-editor/language/css/css.worker.js?worker';
import HtmlWorker from 'monaco-editor/language/html/html.worker.js?worker';
import TsWorker from 'monaco-editor/language/typescript/ts.worker.js?worker';
import {languageFor} from './helpers';
(self as any).MonacoEnvironment={getWorker(_module:string,label:string){if(label==='json')return new JsonWorker();if(['css','scss','less'].includes(label))return new CssWorker();if(['html','handlebars','razor'].includes(label))return new HtmlWorker();if(['typescript','javascript'].includes(label))return new TsWorker();return new EditorWorker();}};
loader.config({monaco});
const modelRefs=new Map<string,number>();
export interface SourceProps {identity:string;name:string;text:string;onChange:(text:string)=>void;readOnly?:boolean;line?:number;theme?:string;fontSize?:number;findToken?:number;original?:string;onSave?:()=>void}
export function SourceEditor(p:SourceProps){
 useEffect(()=>{const uri='library-file:///'+encodeURIComponent(p.identity);modelRefs.set(uri,(modelRefs.get(uri)||0)+1);return()=>{const remaining=(modelRefs.get(uri)||1)-1;if(remaining)modelRefs.set(uri,remaining);else{modelRefs.delete(uri);monaco.editor.getModel(monaco.Uri.parse(uri))?.dispose();}};},[p.identity]);
 const ref=useRef<monaco.editor.IStandaloneCodeEditor|null>(null);
 useEffect(()=>{if(p.findToken)ref.current?.getAction('actions.find')?.run();},[p.findToken]);
 useEffect(()=>{if(p.line&&ref.current){ref.current.revealLineInCenter(p.line);ref.current.setPosition({lineNumber:p.line,column:1});}},[p.line]);
 const options:monaco.editor.IStandaloneEditorConstructionOptions={readOnly:p.readOnly,fontSize:p.fontSize||14,minimap:{enabled:false},scrollBeyondLastLine:false,automaticLayout:true,padding:{top:18,bottom:18},wordWrap:'on',renderLineHighlight:'none',overviewRulerLanes:0,hideCursorInOverviewRuler:true,ariaLabel:p.name+' source editor'};
 const theme=p.theme==='dark'||(p.theme==='system'&&matchMedia('(prefers-color-scheme: dark)').matches)?'vs-dark':'vs';
 if(p.original!==undefined)return <DiffEditor height="100%" original={p.original} modified={p.text} language={languageFor(p.name)} theme={theme} options={{...options,readOnly:true,renderSideBySide:true}} />;
 return <Editor height="100%" path={'library-file:///'+encodeURIComponent(p.identity)} language={languageFor(p.name)} value={p.text} theme={theme} options={options} saveViewState keepCurrentModel onChange={value=>p.onChange(value??'')} onMount={editor=>{ref.current=editor;editor.addCommand(monaco.KeyMod.CtrlCmd|monaco.KeyCode.KeyS,()=>p.onSave?.());if(p.line){editor.revealLineInCenter(p.line);editor.setPosition({lineNumber:p.line,column:1});}}} loading={<div className="fv-empty" role="status">Opening source…</div>}/>;
}
