import { buildApp } from '../src/app.js';
import { loadConfig } from '../src/config.js';
import { createPool } from '../src/db/pool.js';
import { migrate } from '../src/db/migrate.js';
import type { Providers } from '../src/providers/types.js';

export const SERVICE_KEY = 'test-service-key';
const DB = process.env.TEST_DATABASE_URL ?? 'postgres://frontdesk:frontdesk@localhost:5432/frontdesk_test';

/** Test doubles. These exist only in tests; production wiring uses the real (or loudly unconfigured) providers. */
export function makeProviders(over: Partial<Providers> = {}) {
  const codes: Record<string, string> = {};
  const calls: unknown[] = [];
  const providers: Providers = {
    billing: {
      startTrial: async (i) => ({ customerId: `cus_${i.organizationId}`, subscriptionId: `sub_${i.organizationId}`, trialEndsAt: new Date(Date.now() + 7 * 864e5) }),
      cancelSubscription: async () => {},
    },
    voice: {
      provisionAgent: async (i) => ({ agentId: `agent_${i.organizationId}` }),
      placeTestCall: async (i) => { calls.push(i); return { providerCallId: 'pc_1' }; },
    },
    notifier: { sendLoginCode: async (i) => { codes[(i.email ?? i.phone)!] = i.code; } },
    ...over,
  };
  return { providers, codes, calls };
}

export async function setup(over: Partial<Providers> = {}) {
  const config = { ...loadConfig({ NODE_ENV: 'test', DATABASE_URL: DB, SERVICE_API_KEY: SERVICE_KEY }) };
  const pool = createPool(DB);
  await pool.query('DROP SCHEMA public CASCADE; CREATE SCHEMA public;');
  await migrate(pool);
  const p = makeProviders(over);
  const app = buildApp({ config, pool, providers: p.providers });
  await app.ready();
  return { app, pool, ...p };
}

export const accountBody = (n: number, extra: Record<string, unknown> = {}) => ({
  externalRef: `lead-${n}`,
  entryPath: 'outbound',
  business: { name: `Acme Plumbing ${n}`, niche: 'plumber' },
  owner: { email: `owner${n}@example.com`, phone: `+1555000${String(1000 + n)}` },
  paymentMethodId: `pm_test_${n}`,
  ...extra,
});
