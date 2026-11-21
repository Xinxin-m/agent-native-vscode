"use strict";
// The VS Code configuration keys the Settings page shows. Only these can be read,
// and only those marked true can be written from the page.
const keys=Object.freeze({'library.iconBar':true,'library.chatEngine':true,'workbench.statusBar.visible':true,'editor.fontSize':true,'library.files.tabWorkspace':true,'library.cleanWorkspace':true,'library.endpoint':false});
const properties=()=>require('./package.json').contributes.configuration.properties||{};
function check(key,value){
 if(keys[key]!==true)throw Error('This setting cannot be changed here.');
 const schema=properties()[key];
 if(schema?.enum&&!schema.enum.includes(value))throw Error('Unknown value for '+key);
 if(key==='workbench.statusBar.visible'&&typeof value!=='boolean')throw Error('Status bar must be on or off.');
 if(key==='editor.fontSize'&&!(Number.isInteger(value)&&value>=8&&value<=32))throw Error('Choose a code font size between 8 and 32.');
}
function read(vscode){
 const values={},schemas={},all=properties(),config=vscode.workspace.getConfiguration();
 for(const key of Object.keys(keys)){
  values[key]=config.get(key);
  const s=all[key];
  if(s)schemas[key]={default:s.default,enum:s.enum,labels:s.enumItemLabels,descriptions:s.enumDescriptions,description:s.description||s.markdownDescription};
 }
 return {values,schemas};
}
async function handle(vscode,message){
 if(message.key!==undefined){check(message.key,message.value);await vscode.workspace.getConfiguration().update(message.key,message.value,vscode.ConfigurationTarget.Global);}
 return read(vscode);
}
module.exports={keys,check,read,handle};
