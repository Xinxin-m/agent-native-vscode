import {describe, expect, it} from 'vitest';
import {encodeResource, parseRenderedLink} from './links';
import type {Resource} from '../state/types';

const endpoint = 'http://127.0.0.1:8744';
const report = '/workspace/reports/weekly/activity.html';
const file: Resource = {kind:'file', host_id:'sc', path:report, mode:'render'};
const parse = (href:string) => parseRenderedLink(href,'/workspace/local','mac',{endpoint,chatKey:'codex-current'});
const href = (resource: Resource, origin = endpoint) => origin + '/workbench' + encodeResource(resource);

describe('native chat links', () => {
 it('opens the exact first-party SC report as a native file with its encoded host and mode', () => {
  expect(parse(href(file))).toEqual({...file, meta:{workbench_link:true}});
  expect(parse('/workbench/' + encodeResource(file))).toEqual({...file, meta:{workbench_link:true}});
 });
 it('preserves durable file identity and view position without importing unrelated metadata', () => {
  expect(parse(href({...file, root_id:'sc-reports', page:3, anchor:'figure-1', line:12,
   meta:{chat_key:'codex-source', useNewWindow:true}}))).toEqual({...file, root_id:'sc-reports', page:3,
   anchor:'figure-1', line:12, meta:{chat_key:'codex-source',workbench_link:true}});
 });
 it('leaves other origins and routes as web links', () => {
  const other = href(file,'http://127.0.0.1:8745');
  expect(parse(other)).toEqual({kind:'url',url:other,title:'127.0.0.1'});
  const differentPath = href(file).replace('/workbench','/other');
  expect(parse(differentPath)).toEqual({kind:'url',url:differentPath,title:'127.0.0.1'});
 });
 it('rejects file identities with an invalid host, path or view mode', () => {
  for(const bad of [{host_id:'sc/other'},{path:'relative.html'},{path:'/workspace/../escape.html'},{path:'//sc/r.html'},
   {path:'/workspace/unsafe\u0000.html'},{mode:'invalid-mode'}]){
   expect(parse(href({...file,...bad}))).toBeNull();
  }
 });
 it('retains current conversation evidence for an ordinary relative SC chat file link', () => {
  expect(parseRenderedLink('reports/sibling.html','/workspace/project','sc',{endpoint,chatKey:'codex-current'})).toMatchObject({
   kind:'file',host_id:'sc',path:'/workspace/project/reports/sibling.html',meta:{chat_key:'codex-current'}
  });
 });
});
