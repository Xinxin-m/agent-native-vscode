"use strict";
// A chat tab carries the chat's full current title (the sidebar row's `title`); a chat with no title yet is "New chat". The core tab bar (multiEditorTabsControl.ts) shows
// TAB_TITLE_MAX characters plus an ellipsis, the same rule as the UI's cutTitle, and keeps the full title in the tooltip.
const TAB_TITLE_MAX=24;
function tabTitle(title,fallback='New chat'){return String(title||'').replace(/\s+/g,' ').trim().slice(0,150)||fallback;}
// The chat's status mark (the sidebar row's priority) as the tab icon; no status keeps the chat icon.
// Running: blue turning circle; waiting for her decision: blue dot; finished, not seen: green dot; failed: red dot. Idle has no mark.
const MARKS={working:['loading~spin','charts.blue'],waiting:['circle-filled','charts.blue'],new:['circle-filled','charts.green'],failed:['circle-filled','charts.red']};
function tabIcon(vscode,status){const mark=MARKS[status];return mark?new vscode.ThemeIcon(mark[0],new vscode.ThemeColor(mark[1])):new vscode.ThemeIcon('comment-discussion');}
module.exports={TAB_TITLE_MAX,tabTitle,tabIcon};
