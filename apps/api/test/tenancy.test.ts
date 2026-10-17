import { afterAll, beforeAll, describe, expect, it } from 'vitest';
import { withSystem, withTenant } from '../src/db/pool.js';
import { SERVICE_KEY, accountBody, setup } from './helpers.js';

let ctx: Awaited<ReturnType<typeof setup>>;
let a: { orgId: string; token: string };
let b: { orgId: string; token: string };

async function signUpAndLogin(n: number) {
  const res = await ctx.app.inject({ method: 'POST', url: '/v1/accounts', headers: { 'x-service-key': SERVICE_KEY }, payload: accountBody(n) });
  const email = `owner${n}@example.com`;
  await ctx.app.inject({ method: 'POST', url: '/v1/auth/request-code', payload: { email } });
  const v = await ctx.app.inject({ method: 'POST', url: '/v1/auth/verify-code', payload: { email, code: ctx.codes[email] } });
  return { orgId: res.json().organizationId as string, token: v.json().token as string };
}
const auth = (t: string) => ({ authorization: `Bearer ${t}` });

beforeAll(async () => {
  ctx = await setup();
  a = await signUpAndLogin(1);
  b = await signUpAndLogin(2);
});
afterAll(async () => { await ctx.app.close(); await ctx.pool.end(); });

describe('auth', () => {
  it('rejects missing/bad tokens', async () => {
    expect((await ctx.app.inject({ method: 'GET', url: '/v1/me' })).statusCode).toBe(401);
    expect((await ctx.app.inject({ method: 'GET', url: '/v1/me', headers: auth('nope') })).statusCode).toBe(401);
  });

  it('wrong code fails, and the code locks after 5 attempts even if later guessed right', async () => {
    const email = 'owner1@example.com';
    await ctx.app.inject({ method: 'POST', url: '/v1/auth/request-code', payload: { email } });
    const real = ctx.codes[email]!;
    const wrong = real === '000000' ? '111111' : '000000';
    for (let i = 0; i < 5; i++) {
      expect((await ctx.app.inject({ method: 'POST', url: '/v1/auth/verify-code', payload: { email, code: wrong } })).statusCode).toBe(401);
    }
    expect((await ctx.app.inject({ method: 'POST', url: '/v1/auth/verify-code', payload: { email, code: real } })).statusCode).toBe(401);
  });

  it('request-code gives the same response for unknown users (no enumeration)', async () => {
    const res = await ctx.app.inject({ method: 'POST', url: '/v1/auth/request-code', payload: { email: 'ghost@example.com' } });
    expect(res.statusCode).toBe(202);
    expect(ctx.codes['ghost@example.com']).toBeUndefined();
  });

  it('logout revokes the session', async () => {
    const c = await signUpAndLogin(3);
    expect((await ctx.app.inject({ method: 'POST', url: '/v1/auth/logout', headers: auth(c.token) })).statusCode).toBe(204);
    expect((await ctx.app.inject({ method: 'GET', url: '/v1/me', headers: auth(c.token) })).statusCode).toBe(401);
  });
});

describe('tenant isolation', () => {
  it('each token sees only its own business', async () => {
    const meA = (await ctx.app.inject({ method: 'GET', url: '/v1/me', headers: auth(a.token) })).json();
    const meB = (await ctx.app.inject({ method: 'GET', url: '/v1/me', headers: auth(b.token) })).json();
    expect(meA.id).toBe(a.orgId);
    expect(meB.id).toBe(b.orgId);
    expect(meA.name).toBe('Acme Plumbing 1');
  });

  it('a client-supplied organization id is rejected, not honoured', async () => {
    const res = await ctx.app.inject({ method: 'PATCH', url: '/v1/receptionist', headers: auth(a.token), payload: { organizationId: b.orgId, greeting: 'pwned' } });
    expect(res.statusCode).toBe(400);
    const viaQuery = await ctx.app.inject({ method: 'GET', url: `/v1/receptionist?organizationId=${b.orgId}`, headers: auth(a.token) });
    expect(viaQuery.json().greeting).toContain('Acme Plumbing 1');
  });

  it("editing A's receptionist never touches B's", async () => {
    const res = await ctx.app.inject({ method: 'PATCH', url: '/v1/receptionist', headers: auth(a.token), payload: { greeting: 'Hi from A' } });
    expect(res.json().greeting).toBe('Hi from A');
    const b2 = await ctx.app.inject({ method: 'GET', url: '/v1/receptionist', headers: auth(b.token) });
    expect(b2.json().greeting).toContain('Acme Plumbing 2');
  });

  it('row-level security hides other tenants even when the query has no WHERE clause', async () => {
    const rows = await withTenant(ctx.pool, a.orgId, (db) => db.query('SELECT organization_id FROM receptionists'));
    expect(rows.rows.map((r) => r.organization_id)).toEqual([a.orgId]);
    const none = await ctx.pool.query('SELECT * FROM receptionists'); // no tenant set, not system
    expect(none.rowCount).toBe(0);
  });

  it('RLS blocks writing a row into another tenant', async () => {
    await expect(withTenant(ctx.pool, a.orgId, (db) =>
      db.query(`INSERT INTO calls (organization_id, direction) VALUES ($1, 'inbound')`, [b.orgId]))).rejects.toThrow(/row-level security/);
  });

  it('tenant-scoped call list excludes other tenants', async () => {
    await withSystem(ctx.pool, (db) => db.query(`INSERT INTO calls (organization_id, caller_number) VALUES ($1,'+15551110000')`, [b.orgId]));
    const res = await ctx.app.inject({ method: 'GET', url: '/v1/calls', headers: auth(a.token) });
    expect(res.json()).toEqual([]);
  });
});

describe('test call', () => {
  it('rings the owner via the voice provider and logs it', async () => {
    const res = await ctx.app.inject({ method: 'POST', url: '/v1/receptionist/test-call', headers: auth(a.token) });
    expect(res.statusCode).toBe(202);
    expect(ctx.calls).toHaveLength(1);
    const list = await ctx.app.inject({ method: 'GET', url: '/v1/calls', headers: auth(a.token) });
    expect(list.json()[0].direction).toBe('test');
  });
});
