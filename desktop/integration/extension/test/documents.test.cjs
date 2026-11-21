const {test}=require('node:test');const assert=require('node:assert/strict');
let doc;try{doc=require('../html-source.cjs')}catch{}
test('visible HTML text edits preserve scripts, styles and untouched markup byte for byte',()=>{
 assert.ok(doc,'HTML source mapper exists');
 const source='<!doctype html><html><head><style>p { color:red }</style></head><body><p class="keep">Hello &amp; <strong>world</strong>!</p><script>window.example="untouched";</script></body></html>';
 const model=doc.inspectHtml(source);assert.deepEqual(model.nodes.map(n=>n.text),['Hello & ','world','!']);
 const result=doc.editHtml(source,model,[{id:model.nodes[1].id,text:'朋友 < three'}]);
 assert.equal(result,source.replace('>world<','>朋友 &lt; three<'));
});
test('HTML rejects stale snapshots and forged node IDs',()=>{
 assert.ok(doc,'HTML source mapper exists');const s='<p>Keep me</p>',m=doc.inspectHtml(s);
 assert.throws(()=>doc.editHtml(s+' ',m,[{id:m.nodes[0].id,text:'bad'}]),/changed/);
 assert.throws(()=>doc.editHtml(s,m,[{id:'unknown',text:'bad'}]),/Unknown/);
 assert.equal(doc.editHtml(s,m,[]),s);
});
test('HTML edit model excludes script, style, templates and hidden metadata',()=>{
 assert.ok(doc,'HTML source mapper exists');const m=doc.inspectHtml('<title>Title</title><p>Visible</p><template><p>Hidden</p></template><script>danger()</script><style>body{}</style>');assert.deepEqual(m.nodes.map(n=>n.text),['Visible']);
});
test('HTML preview allows only the injected bridge nonce and keeps authored scripts inert without shifting paths',()=>{
 const html='<p>Hello</p><script nonce="abc123">window.fixture=1</script>',nonce='abc123';
 const preview=doc.previewHtml(html,nonce);assert.match(preview,/<head><meta http-equiv="Content-Security-Policy"/);assert.match(preview,/<script type="application\/x-library-inert">window.fixture=1<\/script>/);assert.match(preview,/script-src-elem 'nonce-abc123'/);assert.match(preview,/script-src-attr 'none'/);assert.doesNotMatch(preview,/script-src-elem 'unsafe-inline'/);assert.match(preview,/connect-src 'none'/);assert.deepEqual(doc.inspectHtml(preview).nodes.map(n=>n.path),doc.inspectHtml(html).nodes.map(n=>n.path));assert.equal(doc.editHtml(html,doc.inspectHtml(html),[]),html);
});
test('inline, external, module and SVG scripts preserve their text and node slots but lose execution attributes',()=>{
 const html='<!doctype html><html><head><style>p{color:red}</style><script src="./head.js" nonce="abc123">head()</script></head><body><p>Before</p><script>inline()</script><script type="module" nonce="abc123">moduleCode()</script><svg><script href="./svg.js">svgCode()</script></svg><p onclick="handler()">After <a href="javascript:danger()">link</a></p></body></html>';
 const preview=doc.previewHtml(html,'abc123'),scripts=[...preview.matchAll(/<script\b([^>]*)>([\s\S]*?)<\/script>/g)];
 assert.equal(scripts.length,4);for(const [,attrs]of scripts){assert.match(attrs,/type="application\/x-library-inert"/);assert.doesNotMatch(attrs,/\b(?:src|href|nonce)=/)}
 assert.deepEqual(scripts.map(s=>s[2]),['head()','inline()','moduleCode()','svgCode()']);
 assert.match(preview,/<style>p\{color:red\}<\/style>/);assert.deepEqual(doc.inspectHtml(preview).nodes.map(n=>({path:n.path,text:n.text})),doc.inspectHtml(html).nodes.map(n=>({path:n.path,text:n.text})));
 const model=doc.inspectHtml(html);assert.equal(doc.editHtml(html,model,[{id:model.nodes[0].id,text:'Edited'}]),html.replace('>Before<','>Edited<'));
});
test('preview rejects malformed nonce input',()=>{assert.throws(()=>doc.previewHtml('<p>Text</p>',"bad'nonce"),/Invalid preview nonce/)});
