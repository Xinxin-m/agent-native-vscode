"use strict";
const {execFile}=require('node:child_process');
const {homedir}=require('node:os');
const {join}=require('node:path');
const {promisify}=require('node:util');
const run=promisify(execFile);
const chromiumBrowsers=['com.google.chrome','org.chromium.chromium','com.microsoft.edgemac','com.brave.browser','com.vivaldi.vivaldi','com.operasoftware.opera'];
const safariWindow=`on run argv
 tell application id "com.apple.Safari"
  make new document with properties {URL:item 1 of argv}
  activate
 end tell
end run`;

async function defaultBrowser(){
 try {
  const preferences=join(homedir(),'Library/Preferences/com.apple.LaunchServices/com.apple.launchservices.secure.plist');
  const {stdout}=await run('/usr/bin/plutil',['-convert','json','-o','-',preferences],{timeout:5000});
  const handlers=JSON.parse(stdout).LSHandlers||[];
  for(const scheme of ['https','http']){
   const bundle=handlers.find(record=>record.LSHandlerURLScheme===scheme&&record.LSHandlerRoleAll)?.LSHandlerRoleAll;
   if(bundle)return bundle;
  }
 }catch{}
 const script='ObjC.import("AppKit"); var app=$.NSWorkspace.sharedWorkspace.URLForApplicationToOpenURL($.NSURL.URLWithString("https://example.com")); ObjC.unwrap($.NSBundle.bundleWithURL(app).bundleIdentifier)';
 const {stdout}=await run('/usr/bin/osascript',['-l','JavaScript','-e',script],{timeout:5000});
 const bundle=stdout.trim();
 if(!/^[a-z\d.-]+$/i.test(bundle))throw Error('Default browser could not be resolved');
 return bundle;
}

function browserWindowCommand(uri,bundle){
 const browser=bundle.toLowerCase();
 if(chromiumBrowsers.some(id=>browser.startsWith(id)))return ['/usr/bin/open',['-n','-b',bundle,'--args','--new-window',uri]];
 if(browser.startsWith('org.mozilla.firefox'))return ['/usr/bin/open',['-n','-b',bundle,'--args','-new-window',uri]];
 if(browser==='com.apple.safari')return ['/usr/bin/osascript',['-e',safariWindow,'--',uri]];
 throw Error('Opening a separate window is unavailable for the current default browser');
}

async function openExternal(uri,browser=true,newWindow=false){
 if(process.platform!=='darwin')return false;
 const url=new URL(uri);
 if(!['http:','https:','file:'].includes(url.protocol)||url.username||url.password)throw Error('Unsupported external link');
 if(newWindow){
  const [command,args]=browserWindowCommand(uri,await defaultBrowser());
  await run(command,args,{timeout:10000});return true;
 }
 let args=[uri];
 if(browser&&url.protocol==='file:')args=['-b',await defaultBrowser(),uri];
 await run('/usr/bin/open',args,{timeout:10000});return true;
}
module.exports={openExternal,browserWindowCommand};
