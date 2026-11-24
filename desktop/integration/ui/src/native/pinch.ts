// Same handler the backend preview server appends to previewed pages (chat-dashboard/preview_server.py PINCH_JS); tests/pinch.test.ts keeps them equal.
export const PINCH_JS=`(function(){
var d=document.documentElement,s=1,tx=0,ty=0,g=1,saved=null;
function clamp(z){return Math.min(4,Math.max(.25,z));}
function apply(){
if(s===1&&!tx&&!ty){if(saved){d.style.transform=saved[0];d.style.transformOrigin=saved[1];if(!d.getAttribute('style'))d.removeAttribute('style');saved=null;}return;}
if(!saved)saved=[d.style.transform,d.style.transformOrigin];
d.style.transformOrigin='0 0';d.style.transform='translate('+tx+'px,'+ty+'px) scale('+s+')';}
function zoomAt(z,x,y){
z=clamp(z);if(Math.abs(z-1)<.001)z=1;
var lx=(x+scrollX-tx)/s,ly=(y+scrollY-ty)/s;
s=z;tx=0;ty=0;apply();
scrollTo({left:z*lx-x,top:z*ly-y,behavior:'instant'});
tx=x+scrollX-z*lx;ty=y+scrollY-z*ly;
if(z===1){tx=0;ty=0;}
apply();}
function scrolls(axis,delta){
var o=getComputedStyle(d)['overflow'+axis];if(o==='visible')o=getComputedStyle(document.body)['overflow'+axis];
if(o==='hidden')return false;
var x=axis==='X',pos=x?scrollX:scrollY,max=x?d.scrollWidth-innerWidth:d.scrollHeight-innerHeight;
return delta>0?pos<max-1:pos>0;}
addEventListener('wheel',function(e){
if(e.ctrlKey){e.preventDefault();zoomAt(s*Math.exp(-e.deltaY/100),e.clientX,e.clientY);return;}
if(s<=1)return;
var w=Math.max(d.offsetWidth,document.body.scrollWidth),h=Math.max(d.offsetHeight,document.body.scrollHeight),nx=tx,ny=ty;
if(e.deltaX&&!scrolls('X',e.deltaX))nx=Math.min(0,Math.max(Math.min(0,innerWidth-s*w),tx-e.deltaX));
if(e.deltaY&&!scrolls('Y',e.deltaY))ny=Math.min(0,Math.max(Math.min(0,innerHeight-s*h),ty-e.deltaY));
if(nx!==tx||ny!==ty){e.preventDefault();tx=nx;ty=ny;apply();}
},{passive:false,capture:true});
addEventListener('gesturestart',function(e){e.preventDefault();g=s;},{passive:false,capture:true});
addEventListener('gesturechange',function(e){e.preventDefault();zoomAt(g*e.scale,e.clientX,e.clientY);},{passive:false,capture:true});
addEventListener('gestureend',function(e){e.preventDefault();},{passive:false,capture:true});
})();`;
