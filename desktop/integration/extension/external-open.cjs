"use strict";
const {execFile}=require('node:child_process'),{promisify}=require('node:util');const run=promisify(execFile);
async function openExternal(uri,browser=true){
 if(process.platform!=='darwin')return false;
 if(!['http:','https:','file:'].includes(new URL(uri).protocol))throw Error('Unsupported external link');
 let args=[uri];
 if(browser&&uri.startsWith('file:')){
  const script='ObjC.import("AppKit"); $.NSWorkspace.sharedWorkspace.URLForApplicationToOpenURL($.NSURL.URLWithString("https://example.com")).path.js';
  const {stdout}=await run('/usr/bin/osascript',['-l','JavaScript','-e',script],{timeout:5000});
  const application=stdout.trim();if(!application.startsWith('/')||!application.endsWith('.app'))throw Error('Default browser could not be resolved');args=['-a',application,uri];
 }
 await run('/usr/bin/open',args,{timeout:10000});return true;
}
module.exports={openExternal};
