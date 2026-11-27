import {afterEach,beforeEach,expect,it,vi} from 'vitest';
import {startPopupTimer} from './popupTimer';

beforeEach(()=>vi.useFakeTimers());
afterEach(()=>{vi.useRealTimers();vi.unstubAllGlobals()});
function fixture(){
 const node=new EventTarget(),state={hover:false};
 vi.stubGlobal('document',{activeElement:null});
 return {node:Object.assign(node,{matches:()=>state.hover,contains:()=>false}) as unknown as HTMLElement,state};
}
it('expires after the displayed time and retains the remainder while hovered',()=>{
 const {node,state}=fixture(),expire=vi.fn();
 const stop=startPopupTimer(node,4,expire);
 vi.advanceTimersByTime(3000);
 state.hover=true;node.dispatchEvent(new Event('pointerenter'));
 vi.advanceTimersByTime(10000);expect(expire).not.toHaveBeenCalled();
 state.hover=false;node.dispatchEvent(new Event('pointerleave'));
 vi.advanceTimersByTime(999);expect(expire).not.toHaveBeenCalled();
 vi.advanceTimersByTime(1);expect(expire).toHaveBeenCalledTimes(1);
 stop();
});
it('keeps a popup open with a zero timeout and cancels the timer when dismissed',()=>{
 const {node}=fixture(),expire=vi.fn();
 startPopupTimer(node,0,expire);
 const stop=startPopupTimer(node,4,expire);
 vi.advanceTimersByTime(3999);stop();vi.advanceTimersByTime(60000);
 expect(expire).not.toHaveBeenCalled();
});
