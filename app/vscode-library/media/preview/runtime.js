'use strict';
(() => {
 const katex = window.katex;
 const renderMath = window.renderMathInElement;
 const delimiters = [
  {left: '$$', right: '$$', display: true},
  {left: '\\[', right: '\\]', display: true},
  {left: '\\(', right: '\\)', display: false},
  {left: '$', right: '$', display: false},
 ];
 function typeset() {
  if (!katex || !renderMath || !document.body) return;
  // Native MathML needs no stylesheet or font downloads. KaTeX's HTML
  // layout requires standards mode, but its MathML works in older fragments.
  const render = katex.render;
  if (document.compatMode === 'BackCompat') {
   katex.render = (tex, node, options) => { node.innerHTML = katex.renderToString(tex, options); };
  }
  try {
   renderMath(document.body, {
    delimiters,
    output: 'mathml',
    ignoredTags: ['script', 'noscript', 'style', 'textarea', 'pre', 'code', 'option', 'math', 'mjx-container'],
    ignoredClasses: ['katex', 'MathJax', 'tex2jax_ignore', 'mathjax_ignore'],
    errorCallback: () => {}, // Keep unsupported or malformed TeX readable as source.
   });
  } finally {
   katex.render = render;
  }
 }
 addEventListener('load', () => {
  const ready = window.MathJax?.startup?.promise;
  if (ready) void Promise.resolve(ready).then(typeset, typeset);
  else typeset();
 }, {once: true});

 // The outer VS Code webview blocks new browser windows. Show local
 // document pop-ups in a modal inside the same isolated preview instead.
 function openPopup(value = '') {
  if (!navigator.userActivation.isActive || !document.body) return null;
  let url;
  try { url = new URL(value || 'about:blank', document.baseURI); }
  catch { return null; }
  if (url.href !== 'about:blank' && url.origin !== location.origin) return null;

  const dialog = document.createElement('dialog');
  dialog.setAttribute('aria-label', 'Document preview pop-up');
  dialog.style.cssText = 'width:85vw;height:80vh;max-width:calc(100% - 2rem);max-height:calc(100% - 2rem);padding:.5rem;border:1px solid GrayText;border-radius:6px;color:CanvasText;background:Canvas;';
  const close = document.createElement('button');
  close.type = 'button';
  close.textContent = 'Close';
  close.setAttribute('aria-label', 'Close preview pop-up');
  close.addEventListener('click', () => dialog.close());
  const frame = document.createElement('iframe');
  frame.title = 'Pop-up content';
  frame.style.cssText = 'display:block;width:100%;height:calc(100% - 2rem);border:0;';
  frame.referrerPolicy = 'no-referrer';
  if (url.href !== 'about:blank') frame.src = url.href;
  dialog.append(close, frame);
  dialog.addEventListener('close', () => dialog.remove(), {once: true});
  document.body.append(dialog);
  dialog.showModal();
  return frame.contentWindow;
 }
 window.open = openPopup;
 addEventListener('click', event => {
  if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
  const link = event.target.closest?.('a[href][target="_blank" i]:not([download])');
  if (link && openPopup(link.href)) event.preventDefault();
 });
})();
