// Empty-space drops in a filtered Explorer belong to its selected root. All-roots views must choose a folder explicitly.
export function libraryEmptyDropTarget<T>(roots: readonly T[], selected: string, identity: (item: T) => string): T | undefined {
 return selected ? roots.find(item => identity(item) === selected) : roots.length === 1 ? roots[0] : undefined;
}
