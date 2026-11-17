import {describe,it,expect} from 'vitest';
import {MarkdownManager} from '@tiptap/markdown';
import StarterKit from '@tiptap/starter-kit';
import {InlineMathWithParens,BlockMathWithBrackets} from './mathExtensions';
import {editMarkdown} from './markdownEdit';
const manager=new MarkdownManager({extensions:[StarterKit,InlineMathWithParens,BlockMathWithBrackets]});
function collect(node:any,type:string):any[]{return [...(node.type===type?[node]:[]),...(node.content||[]).flatMap((child:any)=>collect(child,type))]}
describe('rendered Markdown math delimiters',()=>{
 it.each([['$x^2$','inlineMath'],['\\(x^2\\)','inlineMath'],['$$x^2$$','blockMath'],['\\[x^2\\]','blockMath']])('parses %s as %s',(source,type)=>{const nodes=collect(manager.parse(source),type);expect(nodes).toHaveLength(1);expect(nodes[0].attrs.latex).toBe('x^2')});
 it('preserves original math delimiters when adjacent text is edited',()=>{const source='See \\(x^2\\) now.';const normalized=manager.serialize(manager.parse(source)).trim();expect(editMarkdown(source,normalized,normalized.replace('now','later'))).toBe('See \\(x^2\\) later.')});
});
