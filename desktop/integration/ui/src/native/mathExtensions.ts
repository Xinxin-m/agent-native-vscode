import {InlineMath,BlockMath} from '@tiptap/extension-mathematics';
function firstPosition(source:string,needles:string[]){const positions=needles.map(value=>source.indexOf(value)).filter(value=>value>=0);return positions.length?Math.min(...positions):-1;}
// Preserve the existing dollar math and add the delimiters supported by the
// normal workbench renderer. Source range mapping keeps original delimiters.
export const InlineMathWithParens=InlineMath.extend({markdownTokenizer:{name:'inlineMath',level:'inline',start:(src:string)=>firstPosition(src,['$','\\(']),tokenize:(src:string)=>{const match=src.match(/^\\\(([^\n]+?)\\\)/)||src.match(/^\$([^$]+)\$(?!\$)/);return match?{type:'inlineMath',raw:match[0],latex:match[1].trim()}:undefined;}}});
export const BlockMathWithBrackets=BlockMath.extend({markdownTokenizer:{name:'blockMath',level:'block',start:(src:string)=>firstPosition(src,['$$','\\[']),tokenize:(src:string)=>{const match=src.match(/^\\\[([\s\S]+?)\\\]/)||src.match(/^\$\$([^$]+)\$\$/);return match?{type:'blockMath',raw:match[0],latex:match[1].trim()}:undefined;}}});
