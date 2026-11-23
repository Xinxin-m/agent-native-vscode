/* Library's clients share the same inbox interactions and server receipts.
   Servers with GET /api/notifications give one feed (Agents and App); older ones answer 404 and keep /api/notices. */
(function(root) {
'use strict';
const listeners = new Set();
let summary;
function observe(value) {
 const next = value?.notice_center;
 if (!next || typeof next.revision !== 'string' || !Number.isInteger(next.unread)) return false;
 if (summary && (next.revision.length < summary.revision.length ||
     next.revision.length === summary.revision.length && next.revision < summary.revision)) return false;
 if (summary?.revision !== next.revision || summary.unread !== next.unread) {
  summary = next;
  for (const listener of listeners) listener(next);
 }
 return true;
}
const missing = error => error?.status === 404 || error?.missing === true;
const ago = at => {
 const s = Math.max(0, Date.now() / 1000 - at);
 return s < 60 ? 'just now' : s < 3600 ? Math.floor(s / 60) + ' min ago' : s < 86400 ? Math.floor(s / 3600) + ' h ago' : new Date(at * 1000).toLocaleDateString(undefined, {month: 'short', day: 'numeric'});
};
const firstLine = text => String(text || '').split('\n')[0];
const providerOf = row => {
 if (row.tool === 'claude' || row.tool === 'codex') return row.tool === 'claude' ? 'Claude' : 'Codex';
 const k = row.chat_key || row.account_id || '';
 return k.startsWith('claude') ? 'Claude' : k.startsWith('codex') ? 'Codex' : row.origin === 'monitor' ? 'Monitor' : '';
};
// Blue dot = waiting for her decision, green = finished and not yet seen, red = failed; anything else has no dot.
const FAILED_KINDS = ['limit_reached', 'rate_limited', 'auth_expired', 'not_logged_in', 'other', 'dead', 'blocked', 'stalled'];
const WAITING_KINDS = ['waiting', 'approval', 'question', 'decision', 'input', 'signed_out'];
const markOf = row => {
 if (row.archived || row.resolved_at || row.state && row.state !== 'open') return '';
 if (FAILED_KINDS.includes(row.kind)) return 'failed';
 if (WAITING_KINDS.includes(row.kind) || row.needs_answer) return 'waiting';
 return ['finished', 'done', 'completed'].includes(row.kind) && !(row.seen || row.read_at) ? 'new' : '';
};
const MARK_WORDS = {failed: 'Failed', waiting: 'Waiting for you', new: 'Finished, not yet seen'};
const DEBUG_WORDS = {queued: 'Debug queued', running: 'Debug running', fixed: 'Debug: fixed', needs_review: 'Debug: needs review', needs_signin: 'Debug: needs sign-in', needs_approval: 'Debug: needs approval', failed: 'Debug failed'};
// One row shape for both servers.
const fromNotice = n => ({...n, group: 'app', archived: Boolean(n.archived_at)});
const fromItem = i => ({
 key: i.id, id: i.id, group: i.source === 'agent' ? 'agents' : 'app', feed: true,
 noticeKey: i.id.startsWith('notice:') ? i.id.slice(7) : '',
 kind: i.kind, title: i.title, body: i.body, at: i.at, origin: i.origin, chat_key: i.chat_key, chat_title: i.chat_title,
 link: i.link, options: i.options || [], allow_text: Boolean(i.allow_text), needs_answer: Boolean(i.needs_answer),
 state: i.state, answer: i.answer, account_id: i.account_id, run_id: i.run_id, seen: i.seen, tool: i.tool, live_id: i.live_id,
 debuggable: i.debuggable === true, debug: i.debug || null,
 archived: i.state === 'archived',
 message: i.source === 'app' && i.body ? i.body : i.title
});
let nextId = 0;
function mount(host, options) {
 const el = (tag, text) => {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  return node;
 };
 const button = (text, run, label) => {
  const node = el('button', text);
  node.type = 'button';
  if (label) node.setAttribute('aria-label', label);
  node.onclick = run;
  return node;
 };
 host.classList.add('library-notifications');
 const panel = el('section'), heading = el('h2', 'Notifications');
 panel.id = 'library-notifications-' + ++nextId;
 heading.id = panel.id + '-title';
 heading.tabIndex = -1;
 let value, view = 'inbox', reading = false, pending = false, disposed = false, requested = false, marked = false, timer;
 let mode = 'unknown', unseen = 0, openCount = null;
 let accounts = [];
 const bell = button('', () => panel.hidden ? open() : close(true));
 const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
 svg.setAttribute('viewBox', '0 0 24 24');
 svg.setAttribute('aria-hidden', 'true');
 const path = document.createElementNS(svg.namespaceURI, 'path');
 path.setAttribute('d', 'M18 8a6 6 0 0 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9M10 21h4');
 svg.append(path);
 const dot = el('i');
 dot.setAttribute('aria-hidden', 'true');
 bell.append(svg, dot);
 bell.className = 'notification-bell';
 bell.title = 'Notifications';
 bell.setAttribute('aria-controls', panel.id);
 bell.setAttribute('aria-expanded', 'false');
 panel.className = 'notification-panel' + (host.closest('header') ? ' notification-panel-top' : '');
 panel.setAttribute('role', 'dialog');
 panel.setAttribute('aria-labelledby', heading.id);
 panel.hidden = true;
 const tabs = el('div'), status = el('p');
 const groups = {agents: {head: el('h3', 'Agents'), list: el('ul')}, app: {head: el('h3', 'App'), list: el('ul')}};
 tabs.className = 'notification-tabs';
 status.setAttribute('role', 'status');
 const inbox = button('Inbox', () => show('inbox'));
 const archive = button('Archive', () => show('archive'));
 tabs.append(inbox, archive);
 panel.append(heading, tabs, status);
 for (const group of Object.values(groups)) {
  group.head.className = 'notification-group';
  panel.append(group.head, group.list);
 }
 host.append(bell);
 document.body.append(panel);
 const rows = new Map();
 const empties = ['You’re all caught up.', 'No archived notifications.'];
 function clearRows() {
  for (const {node} of rows.values()) node.remove();
  rows.clear();
 }
 function show(nextView) {
  view = nextView;
  status.textContent = '';
  if (mode === 'feed') {
   clearRows();
   value = undefined;
   status.textContent = 'Loading notifications…';
   void load();
  }
  render();
 }
 function render() {
  const archived = view === 'archive';
  const activeTab = archived ? archive : inbox;
  inbox.setAttribute('aria-pressed', String(!archived));
  archive.setAttribute('aria-pressed', String(archived));
  if (!value) return;
  const visible = value.rows.filter(row => archived ? row.archived : !row.archived);
  const ids = new Set(visible.map(row => row.key));
  for (const [key, item] of rows) if (!ids.has(key)) {
   if (item.node.contains(document.activeElement)) activeTab.focus();
   item.node.remove();
   rows.delete(key);
  }
  const disabled = options.disabled?.();
  for (const [name, group] of Object.entries(groups)) {
   const mine = visible.filter(row => row.group === name);
   group.head.hidden = mode !== 'feed' || !mine.length;
   let after = null;
   for (const row of mine) {
    const signature = JSON.stringify([row, disabled]);
    let item = rows.get(row.key);
    if (!item || item.signature !== signature) {
     const node = build(row, archived, disabled);
     if (item?.node.contains(document.activeElement)) {
      item.node.replaceWith(node);
      (node.querySelector('.notification-archive') || activeTab).focus();
     } else item?.node.remove();
     item = {node, signature};
     rows.set(row.key, item);
    }
    if ((after ? after.nextElementSibling : group.list.firstElementChild) !== item.node) {
     group.list.insertBefore(item.node, after ? after.nextElementSibling : group.list.firstElementChild);
    }
    after = item.node;
   }
  }
  const empty = archived ? 'No archived notifications.' : 'You’re all caught up.';
  if (!visible.length) status.textContent = empty;
  else if (empties.includes(status.textContent) || status.textContent === 'Loading notifications…') status.textContent = '';
 }
 function build(row, archived, disabled) {
  const node = el('li'), body = el('div'), actions = el('div');
  node.className = 'notification-row';
  node.setAttribute('aria-label', row.message);
  body.className = 'notification-body';
  actions.className = 'notification-actions';
  const account = accounts.find(a => a.id === row.account_id);
  const current = account?.warnings?.find(w => w.key === (row.noticeKey || row.key));
  // A failed-run item carries its own recovery options (answered through act), so the warning card stays out of its way.
  const recover = row.feed ? Boolean(row.noticeKey && current && !row.options.length) :
   account && !row.resolved_at && row.kind !== 'other' && (!row.family?.includes(':usage:') || !row.at || row.at * 1000 > Date.now());
  if (!archived && recover) {
   const warning = row.feed ? {...current, key: current.key, kind: row.kind, message: row.message} :
    {...current, key: row.key, kind: row.kind, message: row.message,
     expires_at: row.family?.endsWith(':login') ? row.at : undefined,
     renew_requires_idle: row.family?.endsWith(':login'),
     resets_at: row.family?.includes(':usage:') ? row.at : undefined};
   root.LibraryRecovery.mountWarning(body, {
    account, accounts, request: options.request, disabled,
    popupWarning: warning,
    onDismiss: () => change('archive', row),
    onAccounts: options.onAccounts,
    hideClose: true,
    showAccountLabel: ![account.email, account.label, account.id].some(label => label && row.message?.includes(label))
   });
  } else if (row.feed && row.group === 'agents' || row.feed && row.options.length) {
   const title = el('p', row.message);
   title.className = 'notification-title';
   if (row.kind === 'approval' && row.body) {
    const code = el('code', firstLine(row.body));
    title.append(' ', code);
   }
   body.append(title);
   const reason = !row.chat_title && row.kind !== 'approval' && row.body && firstLine(row.body) !== row.message ? firstLine(row.body) : '';
   const context = [providerOf(row), row.at ? ago(row.at) : ''].filter(Boolean).join(' · ') + (row.chat_title ? ' · ' + row.chat_title : reason ? ' · ' + reason : '');
   if (context) {
    const line = el('p', context);
    line.className = 'notification-context';
    body.append(line);
   }
  } else {
   body.append(el('p', row.message));
  }
  if (row.feed && row.debug && row.debug.state) {
   const line = el('p', (DEBUG_WORDS[row.debug.state] || 'Debug ' + row.debug.state) + (row.debug.text ? ': ' + row.debug.text : ''));
   line.className = 'notification-context notification-debug';
   body.append(line);
  }
  const mark = markOf(row), lead = body.querySelector('p:not(.notification-debug)');
  if (mark && lead) {
   const dot = el('i');
   dot.className = 'notification-dot ' + mark;
   dot.setAttribute('role', 'img');
   dot.setAttribute('aria-label', MARK_WORDS[mark]);
   lead.prepend(dot);
  }
  if (row.feed && row.state !== 'open' && (row.answer?.choice || row.answer?.text)) {
   const done = el('p', 'You chose ' + (row.answer.choice || row.answer.text));
   done.className = 'notification-context';
   body.append(done);
  }
  const live = row.feed && (row.state === 'open' || row.state === 'answered' && row.kind === 'signed_out');
  if (live && !archived) for (const choice of row.options) {
   const run = button(choice, () => answer(row, choice));
   run.disabled = Boolean(disabled);
   actions.append(run);
  }
  if (row.feed && row.debuggable && !archived) {
   const active = ['queued', 'running'].includes(row.debug?.state);
   const run = button(active ? 'Debugging' : 'Debug', () => debug(row), 'Debug: ' + row.message);
   run.className = 'notification-debug-button';
   run.disabled = active || Boolean(disabled);
   actions.append(run);
  }
  if (row.kind === 'waiting' && !row.chat_key && row.link && options.openLive) actions.append(button('Open Live', () => {
   options.openLive(row);
   close(false);
  }));
  if ((row.chat_key || row.run_id) && (options.canOpenChat?.(row) ?? true)) actions.append(button(row.feed ? 'Open chat' : 'Open failed chat', () => {
   options.openChat?.(row);
   close(false);
  }));
  if (!archived && row.official && options.officialRecovery) actions.append(button('Recovery options', async () => {
   try { await options.officialRecovery(row); }
   catch (error) { status.textContent = error.message; }
  }));
  let fieldNode;
  if (live && !archived && row.allow_text && options.allowText !== false) {
   const field = el('input');
   field.className = 'notification-field';
   field.placeholder = row.options.length ? 'Or type an answer' : 'Type an answer';
   field.setAttribute('aria-label', 'Answer: ' + row.message);
   field.disabled = Boolean(disabled);
   field.onkeydown = event => {
    if (event.key === 'Enter' && !event.isComposing && field.value.trim()) {
     event.preventDefault();
     void answer(row, undefined, field.value.trim());
    }
   };
   fieldNode = field;
  }
  if (!archived) {
   const remove = button('×', () => change('archive', row), 'Archive notification');
   remove.title = 'Archive notification';
   remove.className = 'notification-archive';
   node.prepend(remove);
  }
  node.append(body, actions);
  if (fieldNode) node.append(fieldNode);
  return node;
 }
 function adopt(reply) {
  if (!Array.isArray(reply?.notices)) throw Error('Notifications are unavailable.');
  if (reply.notice_center && !observe(reply)) {requested = true; return;}
  value = {rows: reply.notices.map(fromNotice)};
  if (status.textContent === 'Loading notifications…') status.textContent = '';
  render();
 }
 function adoptFeed(reply) {
  if (!Array.isArray(reply?.items)) throw Error('Notifications are unavailable.');
  mode = 'feed';
  unseen = reply.counts?.unseen || 0;
  openCount = Number.isInteger(reply.counts?.open) ? reply.counts.open : null;
  paint();
  if (!panel.hidden) {
   if (status.textContent === 'Loading notifications…') status.textContent = '';
   value = {rows: reply.items.map(fromItem), revision: reply.revision};
   render();
  }
 }
 const querying = () => '/api/notifications' + (view === 'archive' ? '?archived=1' : '');
 async function load() {
  if (reading || pending || disposed) {requested = true; return;}
  reading = true;
  try {
   if (mode !== 'legacy') {
    try {
     adoptFeed(await options.request(querying()));
     markSeen();
     return;
    } catch (error) {
     if (!missing(error)) throw error;
     mode = 'legacy';
     clearInterval(timer);
     paint();
    }
   }
   if (!panel.hidden) {
    adopt(await options.request('/api/notices'));
    markSeen();
   }
  }
  catch (error) { if (!panel.hidden) status.textContent = error.message; }
  finally {
   reading = false;
   if (requested && !panel.hidden && !pending) {requested = false; void load();}
  }
 }
 // Opening the list is what marks its items read; there is no separate button.
 function markSeen() {
  if (marked || panel.hidden || !value) return;
  marked = true;
  const mine = value.rows.filter(row => !row.archived);
  if (mode === 'feed') {
   const ids = mine.filter(row => row.state === 'open' && !row.seen).map(row => row.id);
   if (ids.length) options.request('/api/notifications/act', {action: 'seen', ids}).then(() => load(), () => {});
  } else {
   const keys = mine.filter(row => !row.read_at).map(row => row.key);
   if (keys.length) options.request('/api/notices', {action: 'read', keys}).then(adoptQuietly, () => {});
  }
 }
 function adoptQuietly(reply) {
  if (Array.isArray(reply?.notices)) observe(reply);
 }
 async function change(action, row) {
  if (pending) return;
  pending = true;
  status.textContent = '';
  try {
   if (row.feed) {
    await options.request('/api/notifications/act', {action, id: row.id});
    pending = false;
    await load();
   } else adopt(await options.request('/api/notices', {action, key: row.key}));
  }
  catch (error) {status.textContent = error.message;}
  finally {
   pending = false;
   if (requested && !panel.hidden) {requested = false; void load();}
  }
 }
 async function answer(row, choice, text) {
  if (pending) return;
  pending = true;
  status.textContent = '';
  try {
   await options.request('/api/notifications/act', {id: row.id, action: 'answer', choice, text});
   pending = false;
   await load();
  }
  catch (error) { inlineError(row, error); }
  finally {pending = false;}
 }
 function inlineError(row, error) {
  status.textContent = error.message;
  const node = rows.get(row.key)?.node;
  if (node) {
   node.querySelector('.notification-error')?.remove();
   const note = el('p', error.message);
   note.className = 'notification-context notification-error';
   note.setAttribute('role', 'alert');
   node.querySelector('.notification-body').append(note);
  }
 }
 async function debug(row) {
  if (pending) return;
  pending = true;
  status.textContent = '';
  try {
   await options.request('/api/notifications/act', {id: row.id, action: 'debug'});
   pending = false;
   await load();
  }
  catch (error) { inlineError(row, error.message === 'debug_running' ? Error('A debug is already running for this item.') : error); }
  finally {pending = false;}
 }
 function open() {
  accounts = options.accounts?.() || [];
  panel.hidden = false;
  marked = false;
  bell.setAttribute('aria-expanded', 'true');
  status.textContent = value ? '' : 'Loading notifications…';
  heading.focus();
  void load();
 }
 function close(restoreFocus) {
  panel.hidden = true;
  bell.setAttribute('aria-expanded', 'false');
  if (restoreFocus) bell.focus();
  clearRows();
  value = undefined;
 }
 const outside = event => {if (!panel.hidden && !host.contains(event.target) && !panel.contains(event.target)) close(false);};
 const escape = event => {
  if (!panel.hidden && event.key === 'Escape') {
   event.stopPropagation();
   close(true);
  }
 };
 function paint() {
  // A feed that reports counts.open is counted by open items, so reading the list does not clear the badge.
  const count = mode === 'feed' ? openCount ?? unseen : summary?.unread || 0;
  dot.hidden = count === 0;
  bell.setAttribute('aria-label', count ? `Notifications (${count} ${mode === 'feed' && openCount !== null ? 'open' : 'unread'})` : 'Notifications');
 }
 const update = next => {
  if (mode === 'feed') return;
  paint();
  if (!panel.hidden && !reading && !pending && mode === 'legacy') void load();
 };
 // The feed has no summary riding on other replies, so it is polled; a 404 on the first read ends polling.
 const tick = () => {if (document.visibilityState === 'visible' && !disposed && !pending) void load();};
 document.addEventListener('pointerdown', outside, true);
 document.addEventListener('keydown', escape, true);
 listeners.add(update);
 paint();
 void load();
 timer = setInterval(tick, 8000);
 return () => {
  disposed = true;
  clearInterval(timer);
  listeners.delete(update);
  document.removeEventListener('pointerdown', outside, true);
  document.removeEventListener('keydown', escape, true);
  host.replaceChildren();
  panel.remove();
 };
}
root.LibraryNotifications = {mount, observe};
})(typeof window !== 'undefined' ? window : globalThis);
