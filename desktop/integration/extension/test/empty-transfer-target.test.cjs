'use strict';
const test=require('node:test'),assert=require('node:assert/strict');
const {libraryEmptyDropTarget}=require('../../../vscode/src/vs/workbench/contrib/files/common/libraryDropTarget.ts');
const roots=[{id:'local-first'},{id:'local-selected'},{id:'remote-last'}],identity=root=>root.id;
test('empty filtered Explorer drop targets selected local root, never global last remote root',()=>{assert.equal(libraryEmptyDropTarget(roots,'local-selected',identity),roots[1]);assert.equal(libraryEmptyDropTarget(roots,'remote-last',identity),roots[2]);});
test('ambiguous All-workspaces and stale selected root refuse implicit destination',()=>{assert.equal(libraryEmptyDropTarget(roots,'',identity),undefined);assert.equal(libraryEmptyDropTarget(roots,'closed-root',identity),undefined);assert.equal(libraryEmptyDropTarget([roots[0]],'',identity),roots[0]);assert.equal(libraryEmptyDropTarget([],'',identity),undefined);});
