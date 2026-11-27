// Only count time while the popup is available to read without interaction.
export function startPopupTimer(node: HTMLElement, seconds: number, onExpire: () => void) {
 if (!seconds) return () => {};
 let remaining = seconds * 1000;
 let started = 0;
 let timer: ReturnType<typeof setTimeout> | undefined;

 function pause() {
  if (timer === undefined) return;
  clearTimeout(timer);
  timer = undefined;
  remaining = Math.max(0, remaining - (performance.now() - started));
 }
 function resume() {
  if (timer !== undefined) return;
  started = performance.now();
  timer = setTimeout(onExpire, remaining);
 }
 function update() {
  if (node.matches(':hover') || node.contains(document.activeElement)) pause();
  else resume();
 }
 function blur(event: FocusEvent) {
  if (!node.contains(event.relatedTarget as Node) && !node.matches(':hover')) resume();
 }
 node.addEventListener('pointerenter', pause);
 node.addEventListener('pointerleave', update);
 node.addEventListener('focusin', pause);
 node.addEventListener('focusout', blur);
 update();
 return () => {
  clearTimeout(timer);
  node.removeEventListener('pointerenter', pause);
  node.removeEventListener('pointerleave', update);
  node.removeEventListener('focusin', pause);
  node.removeEventListener('focusout', blur);
 };
}
