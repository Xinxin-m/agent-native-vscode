"use strict";
const commands=Object.freeze({
 settings:'workbench.action.openSettings',
 keybindings:'workbench.action.openGlobalKeybindings',
 extensions:'workbench.view.extensions',
 terminal:'workbench.action.terminal.toggleTerminal',
 statusBar:'workbench.action.toggleStatusbarVisibility',
});
function run(vscode,tool){
 if(typeof tool!=='string'||!Object.hasOwn(commands,tool))throw Error('Unknown editor tool');
 return vscode.commands.executeCommand(commands[tool]);
}
module.exports={run};
