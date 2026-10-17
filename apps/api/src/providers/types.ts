export class NotConfiguredError extends Error {
  constructor(public readonly provider: string) {
    super(`${provider} is not configured`);
  }
}

export interface BillingProvider {
  /** Authorize the card ($0 charged) and start the 7-day trial. Must be idempotent on idempotencyKey. */
  startTrial(input: { organizationId: string; email?: string; name: string; paymentMethodId: string; plan: string; idempotencyKey: string }):
    Promise<{ customerId: string; subscriptionId: string; trialEndsAt: Date }>;
  /** Compensation if the DB write fails after the trial was started. */
  cancelSubscription(subscriptionId: string): Promise<void>;
}

export interface VoiceProvider {
  /** Create the receptionist agent for a business. */
  provisionAgent(input: { organizationId: string; businessName: string; greeting: string; voiceId: string; knowledge: unknown }):
    Promise<{ agentId: string }>;
  /** Ring the owner so they hear their receptionist. */
  placeTestCall(input: { agentId: string; toNumber: string }): Promise<{ providerCallId: string }>;
}

export interface Notifier {
  sendLoginCode(input: { email?: string; phone?: string; code: string }): Promise<void>;
}

export interface Providers {
  billing: BillingProvider;
  voice: VoiceProvider;
  notifier: Notifier;
}
