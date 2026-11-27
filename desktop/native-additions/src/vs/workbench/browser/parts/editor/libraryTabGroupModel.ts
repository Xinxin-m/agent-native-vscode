/* Persistent membership for named groups inside an editor pane. */
export const libraryGroupColors = {Blue:'#467bb1', Green:'#40815d', Purple:'#8264a8', Orange:'#a96630', Pink:'#a25983', Cyan:'#357e88', Red:'#ac5656', Yellow:'#917222', Grey:'#686d79'};
export type LibraryGroupColor = keyof typeof libraryGroupColors;
export interface LibraryTabGroup { id: string; name: string; collapsed: boolean; color?: LibraryGroupColor; keys: string[] }
export class TabGroupModel {
 private panes: Record<string, LibraryTabGroup[]> = {};
 constructor(serialized?: string) {
  if (serialized) { try {
   const value: unknown = JSON.parse(serialized);
   if (value && typeof value === 'object' && !Array.isArray(value)) {
    for (const [pane, groups] of Object.entries(value)) {
     if (Array.isArray(groups)) this.panes[pane] = groups.filter(g => g && typeof g.id === 'string' && typeof g.name === 'string' && Array.isArray(g.keys)).map(g => ({id:g.id,name:g.name,color:Object.hasOwn(libraryGroupColors,g.color)?g.color:undefined,collapsed:!!g.collapsed,keys:g.keys.filter((key: unknown) => typeof key === 'string')}));
    }
   }
  } catch { /* Ignore corrupt saved state. */ } }
 }
 serialize(): string { return JSON.stringify(this.panes); }
 groups(pane: string): readonly LibraryTabGroup[] { return this.panes[pane] || []; }
 groupFor(pane: string, key: string): LibraryTabGroup | undefined { return this.groups(pane).find(group => group.keys.includes(key)); }
 remove(pane: string, keys: readonly string[]): void {
  const selected = new Set(keys); this.panes[pane] = this.groups(pane).map(g => ({...g,keys:g.keys.filter(key => !selected.has(key))})).filter(g => g.keys.length);
 }
 assign(pane: string, id: string, name: string, keys: readonly string[]): void {
  if (!keys.length) return;
  const existing = this.groups(pane).find(g => g.id === id); const collapsed = existing?.collapsed || false;
  const combined = [...new Set([...(existing?.keys || []),...keys])];
  this.remove(pane, combined);
  (this.panes[pane] ||= []).push({id,name,collapsed,color:existing?.color,keys:combined});
 }
 reconcileMove(pane: string, key: string, order: readonly (string | undefined)[], allowRemove = true): boolean {
  const index = order.indexOf(key); if (index < 0) return false;
  const own = this.groupFor(pane, key);
  const before = index > 0 && order[index - 1] ? this.groupFor(pane, order[index - 1]!) : undefined;
  const after = index + 1 < order.length && order[index + 1] ? this.groupFor(pane, order[index + 1]!) : undefined;
  if (before && after && before.id === after.id && own?.id !== before.id) {
   this.assign(pane, before.id, before.name, [key]); return true;
  }
  if (allowRemove && own && own.keys.length > 1 && before?.id !== own.id && after?.id !== own.id) {
   this.remove(pane, [key]); return true;
  }
  return false;
 }
 transfer(source: string, target: string, key: string): void {
  if (source === target) return;
  const group = this.groupFor(source, key); if (!group) return;
  const targetHasGroup = this.groups(target).some(candidate => candidate.id === group.id);
  this.remove(source, [key]); this.assign(target, group.id, group.name, [key]);
  if (!targetHasGroup) { this.update(target,group.id,{color:group.color}); if (group.collapsed) this.toggle(target, group.id); }
 }
 replaceAutomatic(pane: string, groups: readonly {id: string; name: string; keys: string[]}[]): void {
  const previous = new Map(this.groups(pane).filter(group => group.id.startsWith('project:')).map(group => [group.id, group]));
  this.panes[pane] = this.groups(pane).filter(group => !group.id.startsWith('project:'));
  const manual = new Set(this.groups(pane).flatMap(group=>group.keys));
  for (const group of groups) {
   const old=previous.get(group.id),keys=group.keys.filter(key=>!manual.has(key));
   if(!keys.length)continue;
   this.assign(pane, group.id, old?.name||group.name, keys);
   if (old?.color) this.update(pane, group.id, {color:old.color});
   if (old?.collapsed) this.toggle(pane, group.id);
  }
 }
 update(pane: string, id: string, changes: {name?: string; color?: LibraryGroupColor}): void {
  const group = this.groups(pane).find(g => g.id === id);
  if (group) Object.assign(group,changes);
 }
 toggle(pane: string, id: string): void { const group = this.groups(pane).find(g => g.id === id); if (group) group.collapsed = !group.collapsed; }
}
