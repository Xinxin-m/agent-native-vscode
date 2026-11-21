import {describe,it,expect} from 'vitest';
import {MarkdownManager} from '@tiptap/markdown';
import StarterKit from '@tiptap/starter-kit';
import {InlineMathWithParens,BlockMathWithBrackets} from './mathExtensions';
import {editMarkdown} from '../../../extension/markdown-source.cjs';

const manager=new MarkdownManager({extensions:[StarterKit,InlineMathWithParens,BlockMathWithBrackets]});
const cases=[['$x^2$','inlineMath'],['\\(x^2\\)','inlineMath'],['$$x^2$$','blockMath'],['\\[x^2\\]','blockMath']];
function collect(node:any,type:string):any[]{return [...(node.type===type?[node]:[]),...(node.content||[]).flatMap((child:any)=>collect(child,type))]}
describe('native rendered Markdown math delimiter parity',()=>{
 it.each(cases)('parses %s as %s without a DOM',(source,type)=>{
  const nodes=collect(manager.parse(source),type);expect(nodes).toHaveLength(1);expect(nodes[0].attrs.latex).toBe('x^2');
 });
 it.each(cases)('preserves original %s delimiters when adjacent text changes',(math,type)=>{
  const source=type==='blockMath'?'Before.\n\n'+math+'\n\nAdjacent now.':'Before '+math+' adjacent now.';
  const normalized=manager.serialize(manager.parse(source)).trim();
  expect(editMarkdown(source,normalized,normalized.replace('now.','later.'))).toBe(source.replace('now.','later.'));
 });
});
