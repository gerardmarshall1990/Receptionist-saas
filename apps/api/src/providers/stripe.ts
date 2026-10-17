import Stripe from 'stripe';
import { NotConfiguredError, type BillingProvider } from './types.js';

// Price IDs are created in the Stripe dashboard and supplied via env; none are invented here.
const PRICE_ENV: Record<string, string> = {
  starter: 'STRIPE_PRICE_STARTER',
  growth: 'STRIPE_PRICE_GROWTH',
  pro: 'STRIPE_PRICE_PRO',
};

/** NOT yet exercised against a live Stripe account. Card data never reaches us: only a PaymentMethod id from Stripe Elements. */
export function createStripeBilling(secretKey: string | undefined, env: NodeJS.ProcessEnv = process.env): BillingProvider {
  if (!secretKey) return unconfiguredBilling();
  const stripe = new Stripe(secretKey);
  return {
    async startTrial(i) {
      const priceId = env[PRICE_ENV[i.plan] ?? ''];
      if (!priceId) throw new NotConfiguredError(`Stripe price for plan "${i.plan}"`);
      const customer = await stripe.customers.create(
        { name: i.name, email: i.email, payment_method: i.paymentMethodId,
          invoice_settings: { default_payment_method: i.paymentMethodId }, metadata: { organization_id: i.organizationId } },
        { idempotencyKey: `cust-${i.idempotencyKey}` });
      const sub = await stripe.subscriptions.create(
        { customer: customer.id, items: [{ price: priceId }], trial_period_days: 7,
          default_payment_method: i.paymentMethodId, metadata: { organization_id: i.organizationId } },
        { idempotencyKey: `sub-${i.idempotencyKey}` });
      return { customerId: customer.id, subscriptionId: sub.id, trialEndsAt: new Date((sub.trial_end ?? 0) * 1000) };
    },
    async cancelSubscription(id) {
      await stripe.subscriptions.cancel(id);
    },
  };
}

export function unconfiguredBilling(): BillingProvider {
  const fail = async (): Promise<never> => { throw new NotConfiguredError('Stripe'); };
  return { startTrial: fail, cancelSubscription: fail };
}
