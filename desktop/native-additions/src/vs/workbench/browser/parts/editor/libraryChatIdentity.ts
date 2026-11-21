/*---------------------------------------------------------------------------------------------
 *  Copyright (c) Microsoft Corporation. All rights reserved.
 *  Licensed under the MIT License. See License.txt in the project root for license information.
 *--------------------------------------------------------------------------------------------*/

// Read only the providers' persisted session identity, never their transcript or draft.
// Claude Code 2.1.325 webview/index.js @6044279 persists sessionID on active-session changes.
export function libraryChatIdentity(input: unknown): { isChat: boolean; key?: string; provider?: string } {
	const editor = input as { viewType?: string; providerId?: string; resource?: { scheme?: string; path?: string }; webview?: { state?: string } };
	const viewType = String(editor.providerId || editor.viewType || '').replace(/^mainThreadWebview-/, '');
	const provider = viewType === 'claudeVSCodePanel' ? 'claude' : viewType === 'chatgpt.conversationEditor' || editor.resource?.scheme === 'openai-codex' ? 'codex' : undefined;
	let state: { sessionID?: unknown; resource?: { kind?: string; key?: unknown } } = {};
	try { state = JSON.parse(editor.webview?.state || '{}'); } catch { /* An unknown vendor state is unavailable, never guessed from a title. */ }
	const isChat = !!provider || viewType === 'library.conversation' && state?.resource?.kind === 'chat';
	if (!isChat) { return { isChat: false }; }
	const valid = (value: unknown): value is string => typeof value === 'string' && /^[A-Za-z0-9_-]{1,200}$/.test(value);
	if (provider === 'claude' && valid(state?.sessionID)) { return { isChat, provider, key: 'claude-' + state.sessionID }; }
	if (provider === 'codex') {
		const id = editor.resource?.path?.match(/^\/local\/([A-Za-z0-9_-]+)$/)?.[1];
		if (valid(id)) { return { isChat, provider, key: 'codex-' + id }; }
	}
	if (!provider && state?.resource?.kind === 'chat' && typeof state.resource.key === 'string' && /^(claude|codex)-[A-Za-z0-9_-]{1,200}$/.test(state.resource.key)) {
		return { isChat, key: state.resource.key, provider: state.resource.key.split('-')[0] };
	}
	return { isChat, provider };
}
