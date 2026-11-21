const {test}=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {apply}=require('../themes.cjs');
test('palette changes update the whole workbench, preserving unrelated customization',async()=>{
 const writes=[];const vscode={ConfigurationTarget:{Global:1},ColorThemeKind:{Dark:2,HighContrast:3},window:{activeColorTheme:{kind:2}},workspace:{getConfiguration:section=>({update:async(key,value)=>writes.push([section,key,value])})}};
 await apply(vscode,'beige');assert.deepEqual(writes,[['window','autoDetectColorScheme',false],['workbench','colorTheme','Library Warm beige']]);
 writes.length=0;await apply(vscode,'sage');assert.equal(writes[1][2],'Library Soft sage');
 for(const name of ['beige','sage']){const theme=JSON.parse(fs.readFileSync(path.join(__dirname,'../themes',name+'.json')));for(const surface of ['editor.background','sideBar.background','activityBar.background','titleBar.activeBackground','statusBar.background','editorGroupHeader.tabsBackground','input.background','menu.background'])assert(theme.colors[surface],surface)}
 writes.length=0;await apply(vscode,'system');assert(writes.some(w=>w[1]==='autoDetectColorScheme'&&w[2]===true));assert(writes.some(w=>w[1]==='colorTheme'&&w[2]==='Default Dark Modern'));
});
