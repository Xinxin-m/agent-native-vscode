/*---------------------------------------------------------------------------------------------
 *  Copyright (c) Microsoft Corporation. All rights reserved.
 *  Licensed under the MIT License. See License.txt in the project root for license information.
 *--------------------------------------------------------------------------------------------*/

export interface FileLinkRect {
 readonly left: number;
 readonly top: number;
 readonly right: number;
 readonly bottom: number;
}

/** Per-window state for Library-origin drags. Times are absolute performance timestamps. */
export class LibraryDragSourceTracker {
 private lastEnd = -Infinity;
 private started = -Infinity;
 private fileLinkRects: readonly FileLinkRect[] = [];
 public source: HTMLElement | undefined;

 // The original link has a precise cancel area. Its surrounding iframe can yield to the editor.
 get dockingSource(): HTMLElement | undefined {
  return this.fileLinkRects.length ? undefined : this.source;
 }

 begin(source: HTMLElement, at: number, linkRects?: readonly FileLinkRect[]): boolean {
  if (!source || !Number.isFinite(at) || at <= this.lastEnd || at < this.started) { return false; }
  this.started = at;
  this.source = source;
  this.fileLinkRects = Array.isArray(linkRects) ? linkRects.filter(rect => rect && [rect.left, rect.top, rect.right, rect.bottom].every(Number.isFinite)) : [];
  return true;
 }

 isFileLinkSource(clientX: number, clientY: number): boolean {
  if (!this.source || !this.fileLinkRects.length) { return false; }
  const frame = this.source.getBoundingClientRect();
  if (!frame.width || !frame.height) { return false; }
  const x = (clientX - frame.left) * this.source.clientWidth / frame.width;
  const y = (clientY - frame.top) * this.source.clientHeight / frame.height;
  return this.fileLinkRects.some(rect => x >= rect.left && x <= rect.right && y >= rect.top && y <= rect.bottom);
 }

 accept(at: number): boolean { return Number.isFinite(at) && at > this.lastEnd; }
 end(at: number): boolean {
  if (!Number.isFinite(at) || at < this.started) { return false; }
  this.lastEnd = Math.max(this.lastEnd, at);
  this.source = undefined;
  this.fileLinkRects = [];
  return true;
 }
}
const trackers = new WeakMap<object, LibraryDragSourceTracker>();
export function libraryDragSourceFor(window: object): LibraryDragSourceTracker {
 let tracker = trackers.get(window);
 if (!tracker) { tracker = new LibraryDragSourceTracker(); trackers.set(window, tracker); }
 return tracker;
}
