// Settings sections in page order. Resources is a page, not rows. Panes and tabs, and Files, are groups inside Appearance.
export const SECTIONS=['Appearance','Chats','Agents','Notifications','Sessions','Resources','Advanced'];
export const PAGES=['Resources'];
export type SettingRow={section:string;group?:string;label:string;hint:string;keywords?:string};

// Every word of the query must appear in the row's section, label, hint or keywords.
export function matches(row:SettingRow,query:string){
 const text=[row.section,row.group||'',row.label,row.hint,row.keywords||''].join(' ').toLowerCase();
 return query.toLowerCase().split(/\s+/).filter(Boolean).every(word=>text.includes(word));
}
// Sections that have something to show; a section with no rows is left out.
export const shownSections=(rows:SettingRow[])=>SECTIONS.filter(s=>PAGES.includes(s)||rows.some(r=>r.section===s));
