const {execFileSync}=require('node:child_process');
function doubleClickInterval(platform=process.platform,read=execFileSync){
 if(platform==='darwin'){try{const seconds=Number(String(read('/usr/bin/defaults',['read','-g','com.apple.mouse.doubleClickThreshold'],{encoding:'utf8',timeout:1500,stdio:['ignore','pipe','ignore']})).trim());if(Number.isFinite(seconds)&&seconds>0&&seconds<=5)return Math.ceil(seconds*1000)+50;}catch{}}
 return 550;
}
module.exports={doubleClickInterval};
