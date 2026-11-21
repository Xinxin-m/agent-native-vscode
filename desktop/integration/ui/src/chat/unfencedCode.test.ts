import {describe,it,expect} from 'vitest';
import MarkdownIt from 'markdown-it';
import {formatUnfencedCode} from './unfencedCode';
const ticks=String.fromCharCode(96).repeat(3);
describe('unfenced code presentation',()=>{
 it('formats complete JSON between prose and preserves the code',()=>{
  const source='Here is the result:\n\n{\n  "count": 2,\n\n  "items": ["a"]\n}\n\nContinue.';
  const result=formatUnfencedCode(source);expect(result).toContain(ticks+'json\n{\n  "count": 2,\n\n  "items": ["a"]\n}\n'+ticks);expect(result).toContain('Continue.');
 });
 it('preserves existing fences and ordinary prose',()=>{
  const source=ticks+'text\n{"literal": true}\n'+ticks+'\n\nA < B and B > C. [Reference](https://example.org)\n\n{unfinished';
  expect(formatUnfencedCode(source)).toBe(source);
 });
 it('renders raw HTML as escaped code, never active markup',()=>{
  const source='<script>\nalert("hello");\n</script>';
  const html=new MarkdownIt({html:false}).render(formatUnfencedCode(source));
  expect(html).toContain('language-html');expect(html).toContain('&lt;script&gt;');expect(html).not.toContain('<script>');
 });
 it('recognizes multiline functions while leaving informal instructions alone',()=>{
  expect(formatUnfencedCode('function sum(a, b) {\n  return a + b;\n}')).toContain('javascript');
  expect(formatUnfencedCode('import your history\nand keep working')).toBe('import your history\nand keep working');
 });
 it('keeps inline snippets, markdown autolinks and indented code intact',()=>{
  const source='Use {"x": 1} here.\n\n<https://example.org>\n\n    {"indented": true}';
  expect(formatUnfencedCode(source)).toBe(source);
 });
 it('keeps trailing prose outside unfenced functions',()=>{
  const js=formatUnfencedCode('function sum(a, b) {\n  return a+b;\n}\nThis is the explanation.');
  expect(js).toContain('}\n'+ticks+'\n\nThis is the explanation.');
  const py=formatUnfencedCode('def answer():\n    return 42\nThis is the explanation.');
  expect(py).toContain('return 42\n'+ticks+'\n\nThis is the explanation.');
 });
 it('chooses a fence that cannot be closed by HTML content',()=>{
  const value='<div>\n'+ticks+'\n</div>';expect(formatUnfencedCode(value)).toContain(ticks+String.fromCharCode(96)+'html');
 });
});
