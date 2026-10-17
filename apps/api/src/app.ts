import { timingSafeEqual } from 'node:crypto';
import Fastify, { type FastifyReply, type FastifyRequest } from 'fastify';
import type pg from 'pg';
import { z, ZodError } from 'zod';
import type { Config } from './config.js';
import { withTenant } from './db/pool.js';
import { businessHoursSchema, faqSchema, serviceSchema } from './knowledge/niche.js';
import { NotConfiguredError, type Providers } from './providers/types.js';
import { AccountError, createAccount, createAccountSchema } from './services/accounts.js';
import { audit } from './services/audit.js';
import { requestLoginCode, resolveSession, revokeSession, verifyLoginCode } from './services/auth.js';

declare module 'fastify' {
  interface FastifyRequest { auth?: { userId: string; organizationId: string; token: string } }
}

const identifier = z.union([
  z.object({ email: z.string().email() }).strict(),
  z.object({ phone: z.string().regex(/^\+[1-9]\d{6,14}$/) }).strict(),
]);

const receptionistPatch = z.object({
  greeting: z.string().min(1).max(500),
  voiceId: z.string().min(1),
  businessHours: businessHoursSchema,
  services: z.array(serviceSchema),
  faqs: z.array(faqSchema),
  callFlow: z.array(z.string()),
  transferRules: z.object({ enabled: z.boolean(), number: z.string().optional(), conditions: z.array(z.string()).optional() }).strict(),
}).partial().strict();

const safeEqual = (a: string, b: string) => a.length === b.length && timingSafeEqual(Buffer.from(a), Buffer.from(b));

export function buildApp(deps: { config: Config; pool: pg.Pool; providers: Providers }) {
  const { config, pool, providers } = deps;
  const app = Fastify({ logger: config.env !== 'test' });

  app.setErrorHandler((err: Error, _req, reply) => {
    if (err instanceof ZodError) return reply.code(400).send({ error: 'invalid_request', issues: err.issues });
    if (err instanceof AccountError) {
      const status = err.code === 'unknown_niche' ? 422 : err.code === 'owner_exists' ? 409 : 502;
      return reply.code(status).send({ error: err.code, message: err.message });
    }
    if (err instanceof NotConfiguredError) return reply.code(503).send({ error: 'not_configured', message: err.message });
    app.log.error(err);
    return reply.code(500).send({ error: 'internal_error' });
  });

  const requireSession = async (req: FastifyRequest, reply: FastifyReply) => {
    const token = req.headers.authorization?.match(/^Bearer (.+)$/)?.[1];
    const session = token ? await resolveSession(pool, config.authSecret, token) : null;
    if (!token || !session) return reply.code(401).send({ error: 'unauthorized' });
    req.auth = { ...session, token };
  };

  const requireServiceKey = async (req: FastifyRequest, reply: FastifyReply) => {
    const key = req.headers['x-service-key'];
    if (!config.serviceApiKey || typeof key !== 'string' || !safeEqual(key, config.serviceApiKey)) {
      return reply.code(401).send({ error: 'unauthorized' });
    }
  };

  app.get('/healthz', async () => ({ ok: true }));

  // Account-creation API: the handoff target for the outbound engine's Card Capture stage.
  app.post('/v1/accounts', { preHandler: requireServiceKey }, async (req, reply) => {
    const body = createAccountSchema.parse(req.body);
    const result = await createAccount(pool, providers, body, 'outbound-engine');
    return reply.code(result.created ? 201 : 200).send(result);
  });

  app.post('/v1/auth/request-code', async (req, reply) => {
    await requestLoginCode(pool, providers.notifier, config.authSecret, identifier.parse(req.body));
    return reply.code(202).send({ ok: true });
  });

  app.post('/v1/auth/verify-code', async (req, reply) => {
    const body = z.intersection(identifier, z.object({ code: z.string().regex(/^\d{6}$/) })).parse(req.body);
    const { code, ...id } = body;
    const token = await verifyLoginCode(pool, config.authSecret, id as { email: string } | { phone: string }, code);
    if (!token) return reply.code(401).send({ error: 'invalid_code' });
    return { token };
  });

  app.post('/v1/auth/logout', { preHandler: requireSession }, async (req, reply) => {
    await revokeSession(pool, config.authSecret, req.auth!.token);
    return reply.code(204).send();
  });

  // Every handler below scopes by the organization on the verified session. No route takes an org id from the client.
  app.get('/v1/me', { preHandler: requireSession }, async (req) =>
    withTenant(pool, req.auth!.organizationId, async (db) => {
      const r = await db.query(
        `SELECT o.id, o.name, o.niche_slug, s.plan, s.status, s.trial_ends_at
         FROM organizations o JOIN subscriptions s ON s.organization_id = o.id WHERE o.id = $1`, [req.auth!.organizationId]);
      return r.rows[0];
    }));

  const receptionistCols = `id, greeting, voice_id AS "voiceId", business_hours AS "businessHours", services, faqs,
    call_flow AS "callFlow", transfer_rules AS "transferRules", mode, forwarding_status AS "forwardingStatus",
    (voice_agent_id IS NOT NULL) AS "voiceReady"`;

  app.get('/v1/receptionist', { preHandler: requireSession }, async (req) =>
    withTenant(pool, req.auth!.organizationId, async (db) =>
      (await db.query(`SELECT ${receptionistCols} FROM receptionists WHERE organization_id = $1`, [req.auth!.organizationId])).rows[0]));

  app.patch('/v1/receptionist', { preHandler: requireSession }, async (req) => {
    const p = receptionistPatch.parse(req.body);
    const cols: Record<string, unknown> = {
      greeting: p.greeting, voice_id: p.voiceId, business_hours: p.businessHours && JSON.stringify(p.businessHours),
      services: p.services && JSON.stringify(p.services), faqs: p.faqs && JSON.stringify(p.faqs),
      call_flow: p.callFlow && JSON.stringify(p.callFlow), transfer_rules: p.transferRules && JSON.stringify(p.transferRules),
    };
    const entries = Object.entries(cols).filter(([, v]) => v !== undefined);
    return withTenant(pool, req.auth!.organizationId, async (db) => {
      if (entries.length) {
        // Column names come from the fixed map above, never from client input.
        const sets = entries.map(([c], i) => `${c} = $${i + 2}`).join(', ');
        await db.query(`UPDATE receptionists SET ${sets}, updated_at = now() WHERE organization_id = $1`,
          [req.auth!.organizationId, ...entries.map(([, v]) => v)]);
        await audit(db, { organizationId: req.auth!.organizationId, actorType: 'user', actorId: req.auth!.userId,
          action: 'receptionist.updated', entity: 'receptionist', metadata: { fields: entries.map(([c]) => c) } });
        // TODO: push changes to the voice agent once the Retell integration exists.
      }
      return (await db.query(`SELECT ${receptionistCols} FROM receptionists WHERE organization_id = $1`, [req.auth!.organizationId])).rows[0];
    });
  });

  app.post('/v1/receptionist/test-call', { preHandler: requireSession }, async (req, reply) => {
    const ctx = await withTenant(pool, req.auth!.organizationId, async (db) => {
      const r = (await db.query(
        `SELECT r.voice_agent_id, u.phone FROM receptionists r JOIN users u ON u.id = $2 WHERE r.organization_id = $1`,
        [req.auth!.organizationId, req.auth!.userId])).rows[0];
      return r as { voice_agent_id: string | null; phone: string | null } | undefined;
    });
    if (!ctx?.voice_agent_id) return reply.code(409).send({ error: 'receptionist_not_ready' });
    if (!ctx.phone) return reply.code(409).send({ error: 'no_phone_on_file' });
    const call = await providers.voice.placeTestCall({ agentId: ctx.voice_agent_id, toNumber: ctx.phone });
    await withTenant(pool, req.auth!.organizationId, async (db) => {
      await db.query(`INSERT INTO calls (organization_id, direction, caller_number) VALUES ($1,'test',$2)`, [req.auth!.organizationId, ctx.phone]);
      await audit(db, { organizationId: req.auth!.organizationId, actorType: 'user', actorId: req.auth!.userId, action: 'receptionist.test_call', metadata: { providerCallId: call.providerCallId } });
    });
    return reply.code(202).send({ ok: true });
  });

  app.get('/v1/calls', { preHandler: requireSession }, async (req) =>
    withTenant(pool, req.auth!.organizationId, async (db) =>
      (await db.query(
        `SELECT id, caller_number AS "callerNumber", direction, outcome, duration_sec AS "durationSec", started_at AS "startedAt"
         FROM calls WHERE organization_id = $1 ORDER BY started_at DESC LIMIT 100`, [req.auth!.organizationId])).rows));

  return app;
}
