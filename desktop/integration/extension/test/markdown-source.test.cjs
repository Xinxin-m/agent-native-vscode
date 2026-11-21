const {test}=require('node:test');const assert=require('node:assert/strict');const {editMarkdown}=require('../markdown-source.cjs');
test('rendered edits preserve comments, frontmatter and reference definitions',()=>{
 const source='---\ntitle: Keep\n---\n# Hello\n\n<!-- preserve me -->\n\nOriginal paragraph.\n\n[ref]: https://example.test \"Keep\"\n';
 const normalized='# Hello\n\nOriginal paragraph.';
 assert.equal(editMarkdown(source,normalized,'# Hello\n\nRevised paragraph.'),source.replace('Original paragraph.','Revised paragraph.'));
 assert.equal(editMarkdown(source,normalized,'# Changed\n\nRevised paragraph.'),source.replace('# Hello','# Changed').replace('Original paragraph.','Revised paragraph.'));
});
test('preserves CRLF and marker style outside edited ranges',()=>{
 const source='Title\r\n=====\r\n\r\n* First item\r\n* Second item\r\n';
 const normalized='# Title\n\n- First item\n- Second item';
 assert.equal(editMarkdown(source,normalized,normalized.replace('First','Updated')),source.replace('First','Updated'));
});
test('refuses deleting across unrepresented markup instead of losing it',()=>{
 assert.throws(()=>editMarkdown('Before\n\n<!-- retain -->\n\nAfter','Before\n\nAfter','Merged'),/cannot safely rewrite/);
});
test('allows insertions into an empty document and preserves no-op source',()=>{
 assert.equal(editMarkdown('','','New'),'New');assert.equal(editMarkdown('x\n','x','x'),'x\n');
});

test('preserves reference definitions after a trailing paragraph with shared letters',()=>{
 const original='---\ntitle: Keep metadata\n---\n# Preservation test\n\n<!-- Keep this hidden comment. -->\n\nOriginal paragraph.\n\n[reference]: https://example.test "Keep definition"\n';
 const normalized='---\ntitle: Keep metadata\n---\n# Preservation test\n\n\n\nOriginal paragraph.';
 assert.equal(editMarkdown(original,normalized,normalized.replace('Original paragraph.','Verified rendered edit.')),original.replace('Original paragraph.','Verified rendered edit.'));
});

test('insertion at a visible boundary retains hidden trailing markup',()=>{
 const source='Before\n\n[ref]: https://example.test\n';
 assert.equal(editMarkdown(source,'Before','Before added'),'Before added\n\n[ref]: https://example.test\n');
 assert.equal(editMarkdown(source,'Before','Added Before'),'Added Before\n\n[ref]: https://example.test\n');
 assert.throws(()=>editMarkdown('Before\n\n<!-- retain -->\n\nAfter','Before\n\nAfter','Merged'),/cannot safely rewrite/);
});
