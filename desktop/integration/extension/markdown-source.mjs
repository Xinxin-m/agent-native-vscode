import {stringDiff} from '../ui/node_modules/monaco-editor/esm/vs/base/common/diff/diff.js';

// Translate edits from the rendered editor's normalized baseline into exact
// original-source ranges. Unrepresented source is never serialized away.
export function editMarkdown(original, normalized, edited) {
 if (![original, normalized, edited].every(value => typeof value === 'string' && value.length <= 1000000)) throw Error('Rendered Markdown editing supports documents up to 1 MB.');
 if (normalized === edited) return original;
 const normalization = stringDiff(original, normalized, true);
 const segments = [];
 let sourceOffset = 0, renderedOffset = 0;
 for (const change of normalization) {
  if (change.modifiedStart > renderedOffset) segments.push({start:renderedOffset,end:change.modifiedStart,offset:sourceOffset-renderedOffset});
  sourceOffset = change.originalStart + change.originalLength;
  renderedOffset = change.modifiedStart + change.modifiedLength;
 }
 if (renderedOffset <= normalized.length) segments.push({start:renderedOffset,end:normalized.length,offset:sourceOffset-renderedOffset});
 const edits = stringDiff(normalized, edited, true).map(change => {
  const start = change.originalStart, end = start + change.originalLength;
  let candidates = segments.filter(segment => start >= segment.start && end <= segment.end);
  // At a collapsed hidden-source boundary, typing extends the visible text
  // before the hidden span. Never apply this preference to replacements.
  if (start === end) {
   const preceding = candidates.filter(segment => segment.end === start && segment.start < start);
   if (preceding.length) candidates = preceding;
  }
  if (!candidates.length || new Set(candidates.map(segment => segment.offset)).size !== 1) throw Error('This formatting change crosses markup the rendered editor cannot safely rewrite. Your original file is unchanged; undo the change or recover your draft.');
  const offset = candidates[0].offset;
  return {start:start+offset,end:end+offset,text:edited.slice(change.modifiedStart,change.modifiedStart+change.modifiedLength)};
 });
 let result = original;
 for (const edit of edits.reverse()) result = result.slice(0,edit.start)+edit.text+result.slice(edit.end);
 return result;
}
