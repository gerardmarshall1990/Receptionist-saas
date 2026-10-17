import { afterAll, beforeAll, describe, expect, it } from 'vitest';
import { withSystem } from '../src/db/pool.js';
import { NotConfiguredError } from '../src/providers/types.js';
import { SERVICE_KEY, accountBody, setup } from './helpers.js';

const headers = { 'x-service-key': SERVICE_KEY };
let ctx: Awaited<ReturnType<typeof setup>>;
beforeAll(async () => { ctx = await setup(); });
afterAll(async () => { await ctx.app.close(); await ctx.pool.end(); });

const post = (payload: unknown, h: Record<string, string> = headers) =>
  ctx.app.inject({ method: 'POST', url: '/v1/accounts', headers: h, payload: payload as object });

describe('POST /v1/accounts', () => {
  it('creates org, owner, niche-seeded receptionist, trial and audit entry', async () => {
    const res = await post(accountBody(1));
    expect(res.statusCode).toBe(201);
    const { organizationId, voice } = res.json();
    expect(voice).toBe('ready');
    await withSystem(ctx.pool, async (db) => {
      const r = (await db.query('SELECT * FROM receptionists WHERE organization_id = $1', [organizationId])).rows[0];
      expect(r.greeting).toContain('Acme Plumbing 1');
      expect(r.voice_id).toBe('voice-warm-1');
      expect(r.services.map((s: { name: string }) => s.name)).toContain('Leak repair');
      expect(r.mode).toBe('message_only'); // no calendar yet
      const s = (await db.query('SELECT * FROM subscriptions WHERE organization_id = $1', [organizationId])).rows[0];
      expect(s).toMatchObject({ status: 'trialing', plan: 'starter', minutes_included: 1200 });
      expect((await db.query('SELECT 1 FROM audit_log WHERE organization_id = $1 AND action = $2', [organizationId, 'account.created'])).rowCount).toBe(1);
    });
  });

  it('is idempotent on externalRef', async () => {
    const a = await post(accountBody(2));
    const b = await post(accountBody(2));
    expect(b.statusCode).toBe(200);
    expect(b.json().organizationId).toBe(a.json().organizationId);
  });

  it('business-specific facts override the niche template', async () => {
    const res = await post(accountBody(3, { business: { name: 'Bob Pipes', niche: 'plumber', services: [{ name: 'Only this' }] } }));
    await withSystem(ctx.pool, async (db) => {
      const r = (await db.query('SELECT services FROM receptionists WHERE organization_id = $1', [res.json().organizationId])).rows[0];
      expect(r.services).toEqual([{ name: 'Only this' }]);
    });
  });

  it('requires the service key', async () => {
    expect((await post(accountBody(4), {})).statusCode).toBe(401);
    expect((await post(accountBody(4), { 'x-service-key': 'wrong' })).statusCode).toBe(401);
  });

  it('rejects unknown niche, raw card fields and client-supplied org ids', async () => {
    expect((await post(accountBody(5, { business: { name: 'X', niche: 'nope' } }))).statusCode).toBe(422);
    expect((await post(accountBody(5, { cardNumber: '4242424242424242' }))).statusCode).toBe(400);
    expect((await post(accountBody(5, { organizationId: '00000000-0000-0000-0000-000000000000' }))).statusCode).toBe(400);
    expect((await post(accountBody(5, { business: { name: 'X', niche: '../../etc/passwd' } }))).statusCode).toBe(422);
  });

  it('rolls everything back when billing fails', async () => {
    const c = await setup({ billing: { startTrial: async () => { throw new NotConfiguredError('Stripe'); }, cancelSubscription: async () => {} } });
    const res = await c.app.inject({ method: 'POST', url: '/v1/accounts', headers, payload: accountBody(6) });
    expect(res.statusCode).toBe(502);
    const n = await withSystem(c.pool, (db) => db.query('SELECT count(*) FROM organizations'));
    expect(n.rows[0].count).toBe('0');
    await c.app.close(); await c.pool.end();
  });

  it('keeps the account and reports voice=pending when voice provisioning fails', async () => {
    const c = await setup({ voice: { provisionAgent: async () => { throw new Error('retell down'); }, placeTestCall: async () => { throw new Error('x'); } } });
    const res = await c.app.inject({ method: 'POST', url: '/v1/accounts', headers, payload: accountBody(7) });
    expect(res.statusCode).toBe(201);
    expect(res.json().voice).toBe('pending');
    await c.app.close(); await c.pool.end();
  });

  it('rejects a second account for the same owner email', async () => {
    await post(accountBody(8));
    const res = await post(accountBody(9, { owner: { email: 'owner8@example.com' } }));
    expect(res.statusCode).toBe(409);
  });
});
