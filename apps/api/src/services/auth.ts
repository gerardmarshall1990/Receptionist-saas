import { createHmac, randomBytes, randomInt, timingSafeEqual } from 'node:crypto';
import type pg from 'pg';
import { withSystem } from '../db/pool.js';
import type { Notifier } from '../providers/types.js';
import { audit } from './audit.js';

const CODE_TTL_MIN = 10;
const MAX_ATTEMPTS = 5;

const hash = (secret: string, v: string) => createHmac('sha256', secret).update(v).digest('hex');
const eq = (a: string, b: string) => a.length === b.length && timingSafeEqual(Buffer.from(a), Buffer.from(b));

export type Identifier = { email: string } | { phone: string };
const lookup = (id: Identifier) => ('email' in id ? ['email', id.email.toLowerCase()] as const : ['phone', id.phone] as const);

/** Always resolves the same way whether or not the user exists, so it can't be used to enumerate accounts. */
export async function requestLoginCode(pool: pg.Pool, notifier: Notifier, secret: string, id: Identifier): Promise<void> {
  const [col, val] = lookup(id);
  const user = await withSystem(pool, async (db) => {
    const u = (await db.query(`SELECT id FROM users WHERE ${col} = $1`, [val])).rows[0];
    if (!u) return null;
    const code = String(randomInt(0, 1_000_000)).padStart(6, '0');
    await db.query('UPDATE login_codes SET consumed_at = now() WHERE user_id = $1 AND consumed_at IS NULL', [u.id]);
    await db.query(`INSERT INTO login_codes (user_id, code_hash, expires_at) VALUES ($1,$2, now() + make_interval(mins => $3))`,
      [u.id, hash(secret, `${u.id}:${code}`), CODE_TTL_MIN]);
    return { code };
  });
  if (user) await notifier.sendLoginCode({ ...(col === 'email' ? { email: val } : { phone: val }), code: user.code });
}

/** Returns a session token, or null for any failure (no detail on why). */
export async function verifyLoginCode(pool: pg.Pool, secret: string, id: Identifier, code: string): Promise<string | null> {
  const [col, val] = lookup(id);
  return withSystem(pool, async (db) => {
    const u = (await db.query(`SELECT id, organization_id FROM users WHERE ${col} = $1`, [val])).rows[0];
    if (!u) return null;
    const lc = (await db.query(
      `SELECT id, code_hash, attempts FROM login_codes
       WHERE user_id = $1 AND consumed_at IS NULL AND expires_at > now() ORDER BY created_at DESC LIMIT 1 FOR UPDATE`, [u.id])).rows[0];
    if (!lc || lc.attempts >= MAX_ATTEMPTS) return null;
    if (!eq(lc.code_hash, hash(secret, `${u.id}:${code}`))) {
      await db.query('UPDATE login_codes SET attempts = attempts + 1 WHERE id = $1', [lc.id]);
      return null;
    }
    await db.query('UPDATE login_codes SET consumed_at = now() WHERE id = $1', [lc.id]);
    const token = randomBytes(32).toString('base64url');
    await db.query('INSERT INTO sessions (user_id, organization_id, token_hash) VALUES ($1,$2,$3)', [u.id, u.organization_id, hash(secret, token)]);
    await audit(db, { organizationId: u.organization_id, actorType: 'user', actorId: u.id, action: 'auth.login' });
    return token;
  });
}

/** Sessions last until explicit logout (spec: device stays signed in). */
export async function resolveSession(pool: pg.Pool, secret: string, token: string): Promise<{ userId: string; organizationId: string } | null> {
  return withSystem(pool, async (db) => {
    const s = (await db.query('SELECT user_id, organization_id FROM sessions WHERE token_hash = $1 AND revoked_at IS NULL', [hash(secret, token)])).rows[0];
    return s ? { userId: s.user_id, organizationId: s.organization_id } : null;
  });
}

export async function revokeSession(pool: pg.Pool, secret: string, token: string): Promise<void> {
  await withSystem(pool, (db) => db.query('UPDATE sessions SET revoked_at = now() WHERE token_hash = $1', [hash(secret, token)]));
}
