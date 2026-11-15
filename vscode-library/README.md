# Library Chat for VS Code

Open **Library: Open Chat** in ordinary desktop VS Code. The panel uses your existing Library service at `http://127.0.0.1:8744`; change the application setting `library.endpoint` for another loopback port. The extension never launches a broker, agent CLI, or provider session itself. Its HTTP requests use the same broker as browser Library.

Choose/search an existing conversation. Its provider, account, host, model, effort and folder come from `/api/conversation/defaults`; execution settings remain bound to that conversation. Type a message and click Send, or use Command/Ctrl+Enter. Shift/ordinary Enter adds a newline. Attach file stages local files up to 20 MB each through the broker. PNG, JPEG, GIF and WebP attachments are identified from their file signatures and uploaded with image MIME types. Other files use binary MIME. Remote attachments are unavailable. Pending approvals require an explicit Allow once/Deny; questions preserve answer drafts in the webview. Stop run requests the broker's interrupt action.

The panel follows the latest run for the exact conversation via `/api/conversation/run`, including browser-started runs and explicit browser handoffs. While a new native session awaits indexing, live events and exact-run approval/interrupt controls remain available using the broker run bindings. Sending waits for indexed native defaults; metadata failures do not block run controls. After a handoff, sending uses the new native session and its provider/account/host defaults while drafts stay under the selected Library identity. An unconfirmed request targeting the old native session is preserved and blocked for explicit reconciliation, never silently retargeted. Visible panels poll serially every 1.7 seconds, fetching incremental events and reloading saved messages when the Library revision or run status changes. Closing/hiding the panel does not stop runs. Reopening resumes from broker journals. Drafts and unconfirmed send payloads/request IDs persist in VS Code global storage. Retry same request sends the original unconfirmed payload, even if you have edited a later draft. A conflicting active send retains your draft. A failed run offers Append failed message and Copy failed message. Appending preserves the current draft; recovery never automatically sends.

Markdown and fenced code render through bundled markdown-it and DOMPurify, with HTML and images disabled. Copy works per message. Clicking local file links opens the native VS Code editor beside chat, including `:line` and `#Lline`. Remote file links cannot open locally. HTTPS/HTTP links open the default browser. No command links, raw HTML, iframe or webview network access are enabled. Sending, file attachment and run control require a trusted workspace. Read-only history remains available in an untrusted workspace.

An external native Codex pane may still own a thread. Release that owner before continuing it in Library, or create an explicit linked branch in browser Library. This extension never disables another extension, kills an external session, or migrates provider ownership automatically.

## Build and local installation

From this directory:

```
node --test test/*.test.js
python3 package.py
```

The packager prints a timestamped VSIX path. It packages only the explicit source allowlist, including the already-installed Markdown vendor bundles and their licenses. It needs no network, npm dependencies, user data, credentials or chat history. Install the resulting VSIX through VS Code's **Extensions: Install from VSIX**. It is a local private extension, not a Marketplace publication.

Open the panel through `vscode://local.library-workspace/open`; optional `?key=<URL-encoded conversation key>` selects a conversation. The extension runs on the UI machine (`extensionKind: ui`) even when the editor connects to a remote workspace.

## Current limits

Create new conversations, change provider/model/account/host, fork or compact, manage projects, preview images, in browser Library. This first panel continues existing conversations, including native remote conversations through the broker. No terminal or filesystem execution is added by the panel. Transcript availability still depends on Library indexing; polling is not an indexing freshness guarantee. Drafts are private to this VS Code installation, while accepted prompts, runs and decisions are shared through the broker. Use one Library panel per VS Code window; concurrent windows rely on broker conflict/idempotency guards. A service outage preserves drafts and shows an error; Refresh/retry reconnects.

Coverage: GOAL-02/03/04, NAV-13, ORG-08/11, native account binding, shared run discovery and visible pending decisions. This is a focused additional chat client, not full Workspace UI parity.


## Project maps

Projects opens a searchable picker and a native project map tab. `Library: Open Project Map` is also available in the Command Palette. Reopening the same project reuses its tab; a conversation can open from its map inspection panel. Map settings choose Live, 3, 7 (default), 14 or 30 days, with optional hiding of inactive sessions.

The map uses the same deterministic graph service and bundled presentation assets as browser Library and Library Code. The local read-only companion runs at `127.0.0.1:53962`. Its absence produces a visible connection error; the extension never starts provider work. The webview has no direct network access; the extension host permits only four bounded graph GET routes. Hidden map tabs suspend polling.

`bundle_project_map.py` copies the explicit shared asset allowlist, verifies every local HTML script/style reference and writes SHA-256 hashes to `media/project-map/bundle.json`. `package.py` invokes it before packaging. The same host adapter and assets are staged into Library Code by its build script. No transcripts, configuration, credentials or project-specific data are copied into either extension.
