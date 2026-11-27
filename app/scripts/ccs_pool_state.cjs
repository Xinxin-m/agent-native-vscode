#!/usr/bin/env node
// Print the CCS CLIProxy Claude pool as JSON: the functions `ccs cliproxy quota --provider claude`
// renders (quota per account, drain order, available/cooling/paused). Reads only; never routes a request.
// Usage: node ccs_pool_state.cjs [path to @kaitranntt/ccs/dist]
const path = require('path');
const dist = process.argv[2] || '/opt/homebrew/lib/node_modules/@kaitranntt/ccs/dist';
const load = (rel) => require(path.join(dist, rel));

async function main() {
  const { getProviderAccounts } = load('cliproxy/accounts/query');
  const { resolvePoolStateWithProxyCooldowns } = load('cliproxy/accounts/pool-state');
  const { readPoolRoutingSettings } = load('commands/cliproxy/pool-state-renderer');
  const { fetchAllClaudeQuotas } = load('cliproxy/quota/quota-fetcher-claude');
  const accounts = getProviderAccounts('claude');
  const pool = await resolvePoolStateWithProxyCooldowns({ provider: 'claude', settings: readPoolRoutingSettings() });
  const quotas = accounts.length ? await fetchAllClaudeQuotas(false) : [];
  const byId = new Map(quotas.map((q) => [q.account, q.quota]));
  const states = new Map(pool.states.map((s) => [s.accountId, s]));
  process.stdout.write(JSON.stringify({
    order: pool.drainOrder.order,
    order_mode: pool.drainOrder.mode,
    accounts: accounts.map((a) => {
      const state = states.get(a.id) || {};
      const quota = byId.get(a.id) || {};
      return {
        id: a.id, email: a.email || null, nickname: a.nickname || null,
        state: state.state || null, cooldown_until: state.cooldownUntil || null,
        quota_ok: quota.success === true, quota_error: quota.error || null, needs_reauth: !!quota.needsReauth,
        windows: (quota.windows || []).map((w) => ({
          type: w.rateLimitType, label: w.label, cadence: w.cadence || null, scope: w.scope || null,
          used_percent: w.usedPercent, reset_at: w.resetAt,
        })),
      };
    }),
  }));
}

main().catch((error) => {
  process.stderr.write(`${error && error.name}: ${error && error.message}\n`);
  process.exit(1);
});
