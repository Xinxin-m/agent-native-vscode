'use strict';
const SIZES=Object.freeze([12,13,14,16,18]);
function zoomLevel(size){if(!SIZES.includes(size))throw Error('Choose one of the five text sizes.');return Math.log(size/14)/Math.log(1.2)}
async function apply(vscode,size){const level=zoomLevel(size),config=vscode.workspace.getConfiguration('window');if(Math.abs(Number(config.get('zoomLevel')||0)-level)>0.0001)await config.update('zoomLevel',level,vscode.ConfigurationTarget.Global);}
module.exports={SIZES,zoomLevel,apply};
