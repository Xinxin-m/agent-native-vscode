/* Library's four clients share the same inbox interactions and server receipts. */
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
 let value, view = 'inbox', reading = false, pending = false, disposed = false, requested = false;
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
 const tabs = el('div'), status = el('p'), list = el('ul');
 tabs.className = 'notification-tabs';
 status.setAttribute('role', 'status');
 const inbox = button('Inbox', () => show('inbox'));
 const read = button('Read', () => show('read'));
 const archive = button('Archive', () => show('archive'));
 tabs.append(inbox, read, archive);
 panel.append(heading, tabs, status, list);
 host.append(bell);
 document.body.append(panel);
 const rows = new Map();
 function show(nextView) {
  view = nextView;
  status.textContent = '';
  render();
 }
 function render() {
  const archived = view === 'archive';
  const activeTab = view === 'inbox' ? inbox : view === 'read' ? read : archive;
  inbox.setAttribute('aria-pressed', String(view === 'inbox'));
  read.setAttribute('aria-pressed', String(view === 'read'));
  archive.setAttribute('aria-pressed', String(archived));
  if (!value) return;
  const visible = value.notices.filter(row => archived ? Boolean(row.archived_at) : !row.archived_at && (view === 'read' ? Boolean(row.read_at) : !row.read_at));
  const ids = new Set(visible.map(row => row.key));
  for (const [key, item] of rows) if (!ids.has(key)) {
   if (item.node.contains(document.activeElement)) activeTab.focus();
   item.node.remove();
   rows.delete(key);
  }
  let after = null;
  for (const row of visible) {
   const signature = JSON.stringify(row);
   let item = rows.get(row.key);
   if (!item || item.signature !== signature) {
    const node = el('li'), body = el('div'), actions = el('div');
    node.className = 'notification-row';
    node.setAttribute('aria-label', row.message);
    body.className = 'notification-body';
    actions.className = 'notification-actions';
    const account = accounts.find(a => a.id === row.account_id);
    if (!archived && account && !row.resolved_at && row.kind !== 'other' &&
        (!row.family?.includes(':usage:') || !row.at || row.at * 1000 > Date.now())) {
     const current = account.warnings?.find(w => w.key === row.key);
     root.LibraryRecovery.mountWarning(body, {
      account, accounts, request: options.request, disabled: options.disabled?.(),
      popupWarning: {...current, key: row.key, kind: row.kind, message: row.message,
       expires_at: row.family?.endsWith(':login') ? row.at : undefined,
       renew_requires_idle: row.family?.endsWith(':login'),
       resets_at: row.family?.includes(':usage:') ? row.at : undefined},
      onDismiss: () => change('archive', row.key),
      onAccounts: options.onAccounts,
      hideClose: true,
      showAccountLabel: ![account.email, account.label, account.id].some(label => label && row.message?.includes(label))
     });
    } else {
     body.append(el('p', row.message));
    }
    if ((row.chat_key || row.run_id) && (options.canOpenChat?.(row) ?? true)) actions.append(button('Open failed chat', () => {
     options.openChat?.(row);
     close(false);
    }));
    if (!archived && row.official && options.officialRecovery) actions.append(button('Recovery options', async () => {
     try { await options.officialRecovery(row); }
     catch (error) { status.textContent = error.message; }
    }));
    if (!archived && !row.read_at) actions.append(button('Mark as read', () => change('read', row.key)));
    if (!archived) {
     const remove = button('×', () => change('archive', row.key), 'Archive notification');
     remove.title = 'Archive notification';
     remove.className = 'notification-archive';
     node.append(remove);
    }
    node.append(body, actions);
    if (item?.node.contains(document.activeElement)) {
     item.node.replaceWith(node);
     (node.querySelector('.notification-archive') || activeTab).focus();
    } else item?.node.remove();
    item = {node, signature};
    rows.set(row.key, item);
   }
   if ((after ? after.nextElementSibling : list.firstElementChild) !== item.node) {
    list.insertBefore(item.node, after ? after.nextElementSibling : list.firstElementChild);
   }
   after = item.node;
  }
  const empty = view === 'archive' ? 'No archived notifications.' : view === 'read' ? 'No read notifications.' : 'You’re all caught up.';
  if (!visible.length) status.textContent = empty;
  else if (['You’re all caught up.', 'No read notifications.', 'No archived notifications.'].includes(status.textContent)) status.textContent = '';
 }
 function adopt(reply) {
  if (!Array.isArray(reply?.notices)) throw Error('Notifications are unavailable.');
  if (reply.notice_center && !observe(reply)) {requested = true; return;}
  value = reply;
  if (status.textContent === 'Loading notifications…') status.textContent = '';
  render();
 }
 async function load() {
  if (reading || pending || disposed) {requested = true; return;}
  reading = true;
  try { adopt(await options.request('/api/notices')); }
  catch (error) { status.textContent = error.message; }
  finally {
   reading = false;
   if (requested && !panel.hidden && !pending) {requested = false; void load();}
  }
 }
 async function change(action, key) {
  if (pending) return;
  pending = true;
  status.textContent = '';
  try { adopt(await options.request('/api/notices', {action, key})); }
  catch (error) {status.textContent = error.message;}
  finally {
   pending = false;
   if (requested && !panel.hidden) {requested = false; void load();}
  }
 }
 function open() {
  accounts = options.accounts?.() || [];
  panel.hidden = false;
  bell.setAttribute('aria-expanded', 'true');
  status.textContent = value ? '' : 'Loading notifications…';
  heading.focus();
  void load();
 }
 function close(restoreFocus) {
  panel.hidden = true;
  bell.setAttribute('aria-expanded', 'false');
  if (restoreFocus) bell.focus();
  for (const {node} of rows.values()) node.remove();
  rows.clear();
 }
 const outside = event => {if (!panel.hidden && !host.contains(event.target) && !panel.contains(event.target)) close(false);};
 const escape = event => {
  if (!panel.hidden && event.key === 'Escape') {
   event.stopPropagation();
   close(true);
  }
 };
 const update = next => {
  const unread = next?.unread || 0;
  dot.hidden = unread === 0;
  bell.setAttribute('aria-label', unread ? `Notifications (${unread} unread)` : 'Notifications');
  if (!panel.hidden && !reading && !pending && value?.notice_center?.revision !== next?.revision) void load();
 };
 document.addEventListener('pointerdown', outside, true);
 document.addEventListener('keydown', escape, true);
 listeners.add(update);
 update(summary);
 return () => {
  disposed = true;
  listeners.delete(update);
  document.removeEventListener('pointerdown', outside, true);
  document.removeEventListener('keydown', escape, true);
  host.replaceChildren();
  panel.remove();
 };
}
root.LibraryNotifications = {mount, observe};
})(typeof window !== 'undefined' ? window : globalThis);
