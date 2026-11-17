// Settings sections in page order. Resources and Analytics are pages, not rows.
export const SECTIONS=['Appearance','Chats','Agents','Panes and tabs','Files','Sessions','Resources','Analytics','Advanced'];
export const PAGES=['Resources','Analytics'];
export type SettingRow={section:string;label:string;hint:string;keywords?:string};

// Every word of the query must appear in the row's section, label, hint or keywords.
export function matches(row:SettingRow,query:string){
 const text=[row.section,row.label,row.hint,row.keywords||''].join(' ').toLowerCase();
 return query.toLowerCase().split(/\s+/).filter(Boolean).every(word=>text.includes(word));
}
// Sections that have something to show; a section with no rows is left out.
export const shownSections=(rows:SettingRow[])=>SECTIONS.filter(s=>PAGES.includes(s)||rows.some(r=>r.section===s));
