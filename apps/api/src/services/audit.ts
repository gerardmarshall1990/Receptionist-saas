import type { Db } from '../db/pool.js';

export async function audit(db: Db, e: {
  organizationId: string | null; actorType: 'user' | 'service' | 'system'; actorId?: string;
  action: string; entity?: string; entityId?: string; metadata?: Record<string, unknown>;
}): Promise<void> {
  await db.query(
    'INSERT INTO audit_log (organization_id, actor_type, actor_id, action, entity, entity_id, metadata) VALUES ($1,$2,$3,$4,$5,$6,$7)',
    [e.organizationId, e.actorType, e.actorId ?? null, e.action, e.entity ?? null, e.entityId ?? null, JSON.stringify(e.metadata ?? {})]);
}
