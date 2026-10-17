import pg from 'pg';

export type Db = pg.PoolClient;

export function createPool(databaseUrl: string): pg.Pool {
  return new pg.Pool({ connectionString: databaseUrl, max: 10 });
}

async function inTx<T>(pool: pg.Pool, settings: Record<string, string>, fn: (db: Db) => Promise<T>): Promise<T> {
  const client = await pool.connect();
  try {
    await client.query('BEGIN');
    for (const [k, v] of Object.entries(settings)) {
      // is_local = true: setting disappears at COMMIT/ROLLBACK, so it can't leak across pooled connections
      await client.query('SELECT set_config($1, $2, true)', [k, v]);
    }
    const result = await fn(client);
    await client.query('COMMIT');
    return result;
  } catch (err) {
    await client.query('ROLLBACK').catch(() => {});
    throw err;
  } finally {
    client.release();
  }
}

/**
 * Run work as one business. `organizationId` must come from the verified session,
 * never from a request body, header or URL.
 */
export function withTenant<T>(pool: pg.Pool, organizationId: string, fn: (db: Db) => Promise<T>): Promise<T> {
  return inTx(pool, { 'app.current_org': organizationId }, fn);
}

/** Trusted server-side flows only (signup, login lookup, webhooks). Never call with client-chosen scope. */
export function withSystem<T>(pool: pg.Pool, fn: (db: Db) => Promise<T>): Promise<T> {
  return inTx(pool, { 'app.system': 'on' }, fn);
}
