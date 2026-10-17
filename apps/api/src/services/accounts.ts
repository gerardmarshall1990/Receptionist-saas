import type pg from 'pg';
import { z } from 'zod';
import { withSystem } from '../db/pool.js';
import { getNiche, businessHoursSchema, faqSchema, serviceSchema } from '../knowledge/niche.js';
import type { Providers } from '../providers/types.js';
import { audit } from './audit.js';

const PLAN_MINUTES = { starter: 1200, growth: 2500, pro: 5000 } as const;

export const createAccountSchema = z.object({
  externalRef: z.string().min(1).max(200).optional(),
  entryPath: z.enum(['outbound', 'self_serve']),
  business: z.object({
    name: z.string().min(1).max(200),
    niche: z.string(),
    website: z.string().url().optional(),
    timezone: z.string().optional(),
    hours: businessHoursSchema.optional(),
    services: z.array(serviceSchema).optional(),
    faqs: z.array(faqSchema).optional(),
  }).strict(),
  owner: z.object({ email: z.string().email().optional(), phone: z.string().regex(/^\+[1-9]\d{6,14}$/).optional() })
    .strict().refine((o) => o.email || o.phone, 'email or phone required'),
  plan: z.enum(['starter', 'growth', 'pro']).default('starter'),
  /** Stripe PaymentMethod id from Stripe Elements. Raw card data is never accepted here. */
  paymentMethodId: z.string().startsWith('pm_'),
}).strict();
export type CreateAccountInput = z.infer<typeof createAccountSchema>;

export interface CreateAccountResult {
  organizationId: string;
  created: boolean;
  /** 'pending' means the account exists but the voice agent still needs provisioning (retryable). */
  voice: 'ready' | 'pending';
}

export class AccountError extends Error {
  constructor(public readonly code: 'unknown_niche' | 'owner_exists' | 'billing_failed', message: string) { super(message); }
}

export async function createAccount(pool: pg.Pool, providers: Providers, input: CreateAccountInput, actorId: string): Promise<CreateAccountResult> {
  const niche = await getNiche(input.business.niche);
  if (!niche) throw new AccountError('unknown_niche', `unknown niche "${input.business.niche}"`);

  const result = await withSystem(pool, async (db) => {
    if (input.externalRef) {
      const existing = await db.query('SELECT id FROM organizations WHERE external_ref = $1', [input.externalRef]);
      if (existing.rows[0]) return { organizationId: existing.rows[0].id as string, created: false };
    }
    const dup = await db.query('SELECT 1 FROM users WHERE email = $1 OR phone = $2', [input.owner.email ?? null, input.owner.phone ?? null]);
    if (dup.rowCount) throw new AccountError('owner_exists', 'an account already exists for that email or phone');

    const org = await db.query(
      `INSERT INTO organizations (name, niche_slug, website, timezone, entry_path, external_ref)
       VALUES ($1,$2,$3,coalesce($4,'America/New_York'),$5,$6) RETURNING id`,
      [input.business.name, niche.slug, input.business.website ?? null, input.business.timezone ?? null, input.entryPath, input.externalRef ?? null]);
    const orgId = org.rows[0].id as string;
    await db.query('INSERT INTO users (organization_id, email, phone, role) VALUES ($1,$2,$3,\'owner\')',
      [orgId, input.owner.email ?? null, input.owner.phone ?? null]);

    // Layer 2 (niche template) seeds the receptionist; layer 3 (this business's facts) overrides it.
    await db.query(
      `INSERT INTO receptionists (organization_id, greeting, voice_id, business_hours, services, faqs, call_flow)
       VALUES ($1,$2,$3,$4,$5,$6,$7)`,
      [orgId, `Thanks for calling ${input.business.name}, how can I help?`, niche.defaultVoiceId,
       JSON.stringify(input.business.hours ?? {}),
       JSON.stringify(input.business.services ?? niche.defaultServices),
       JSON.stringify(input.business.faqs ?? niche.commonFaqs),
       JSON.stringify(niche.defaultCallFlow)]);

    let trial;
    try {
      trial = await providers.billing.startTrial({
        organizationId: orgId, email: input.owner.email, name: input.business.name,
        paymentMethodId: input.paymentMethodId, plan: input.plan, idempotencyKey: orgId });
    } catch (err) {
      throw new AccountError('billing_failed', `could not start trial: ${(err as Error).message}`); // rolls back everything
    }
    try {
      await db.query(
        `INSERT INTO subscriptions (organization_id, plan, status, trial_ends_at, minutes_included, stripe_customer_id, stripe_subscription_id)
         VALUES ($1,$2,'trialing',$3,$4,$5,$6)`,
        [orgId, input.plan, trial.trialEndsAt, PLAN_MINUTES[input.plan], trial.customerId, trial.subscriptionId]);
      await audit(db, { organizationId: orgId, actorType: 'service', actorId, action: 'account.created', entity: 'organization', entityId: orgId,
        metadata: { entryPath: input.entryPath, niche: niche.slug, plan: input.plan } });
    } catch (err) {
      await providers.billing.cancelSubscription(trial.subscriptionId).catch((e) => console.error('compensation failed', e));
      throw err;
    }
    return { organizationId: orgId, created: true };
  });

  // Voice provisioning happens after commit: an outage here must not lose a paying customer.
  // The account stays in 'pending' and provisioning can be retried.
  let voice: CreateAccountResult['voice'] = 'pending';
  if (result.created) {
    try {
      const agent = await withSystem(pool, async (db) => {
        const r = (await db.query('SELECT greeting, voice_id, services, faqs, business_hours FROM receptionists WHERE organization_id = $1', [result.organizationId])).rows[0];
        return providers.voice.provisionAgent({ organizationId: result.organizationId, businessName: input.business.name,
          greeting: r.greeting, voiceId: r.voice_id, knowledge: { services: r.services, faqs: r.faqs, hours: r.business_hours } });
      });
      await withSystem(pool, (db) => db.query('UPDATE receptionists SET voice_agent_id = $2, updated_at = now() WHERE organization_id = $1', [result.organizationId, agent.agentId]));
      voice = 'ready';
    } catch (err) {
      console.error('voice provisioning failed (retryable)', { organizationId: result.organizationId, err: (err as Error).message });
    }
  } else {
    const r = await withSystem(pool, (db) => db.query('SELECT voice_agent_id FROM receptionists WHERE organization_id = $1', [result.organizationId]));
    voice = r.rows[0]?.voice_agent_id ? 'ready' : 'pending';
  }
  return { ...result, voice };
}
