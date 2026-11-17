/*---------------------------------------------------------------------------------------------
 *  Copyright (c) Microsoft Corporation. All rights reserved.
 *  Licensed under the MIT License. See License.txt in the project root for license information.
 *--------------------------------------------------------------------------------------------*/

/** Per-window state for Library-origin drags. Times are absolute performance timestamps. */
export class LibraryDragSourceTracker {
 private lastEnd = -Infinity;
 private started = -Infinity;
 public source: HTMLElement | undefined;
 begin(source: HTMLElement, at: number): boolean {
  if (!source || !Number.isFinite(at) || at <= this.lastEnd || at < this.started) { return false; }
  this.started = at;
  this.source = source;
  return true;
 }
 accept(at: number): boolean { return Number.isFinite(at) && at > this.lastEnd; }
 end(at: number): boolean {
  if (!Number.isFinite(at) || at < this.started) { return false; }
  this.lastEnd = Math.max(this.lastEnd, at);
  this.source = undefined;
  return true;
 }
}
const trackers = new WeakMap<object, LibraryDragSourceTracker>();
export function libraryDragSourceFor(window: object): LibraryDragSourceTracker {
 let tracker = trackers.get(window);
 if (!tracker) { tracker = new LibraryDragSourceTracker(); trackers.set(window, tracker); }
 return tracker;
}
