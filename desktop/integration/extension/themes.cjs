'use strict';
const names={beige:'Library Warm beige',sage:'Library Soft sage',light:'Default Light Modern',dark:'Default Dark Modern'};
async function apply(vscode,theme){
 const config=vscode.workspace.getConfiguration('workbench');
 if(theme==='system'){
  await vscode.workspace.getConfiguration('window').update('autoDetectColorScheme',true,vscode.ConfigurationTarget.Global);
  await config.update('preferredLightColorTheme','Default Light Modern',vscode.ConfigurationTarget.Global);
  await config.update('preferredDarkColorTheme','Default Dark Modern',vscode.ConfigurationTarget.Global);
  const dark=vscode.window.activeColorTheme?.kind===vscode.ColorThemeKind.Dark||vscode.window.activeColorTheme?.kind===vscode.ColorThemeKind.HighContrast;
  await config.update('colorTheme',dark?names.dark:names.light,vscode.ConfigurationTarget.Global);
 }else if(names[theme]){
  await vscode.workspace.getConfiguration('window').update('autoDetectColorScheme',false,vscode.ConfigurationTarget.Global);
  await config.update('colorTheme',names[theme],vscode.ConfigurationTarget.Global);
 }
}
module.exports={apply,names};
