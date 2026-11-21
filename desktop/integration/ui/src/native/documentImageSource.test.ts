import {MarkdownManager} from '@tiptap/markdown';
import StarterKit from '@tiptap/starter-kit';
import Image from '@tiptap/extension-image';
import {describe,it,expect} from 'vitest';
import {documentImageSource} from './documentImageSource';
describe('rendered Markdown image source',()=>{
 it('renders the approved data URL while leaving the source attribute available for Markdown serialization',()=>{
  const manager=new MarkdownManager({extensions:[StarterKit,Image]});
  const source='![Plot](../plot.png)',model=manager.parse(source),assets={'../plot.png':'data:image/png;base64,dGVzdA=='};
  const image=model.content?.find(node=>node.type==='image');
  expect(image?.attrs?.src).toBe('../plot.png');
  expect(documentImageSource(image!.attrs!.src,assets)).toBe(assets['../plot.png']);
  expect(manager.serialize(model).trim()).toBe(source);
 });
 it('blocks unapproved paths and network URLs, including inherited object properties',()=>{
  for(const src of ['../secret.png','https://outside.test/x.png','javascript:alert(1)','__proto__'])expect(documentImageSource(src,{})).toBe('');
  expect(documentImageSource('data:image/png;base64,dGVzdA==')).toBe('data:image/png;base64,dGVzdA==');
 });
});
