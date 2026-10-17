export interface Config {
  env: 'development' | 'test' | 'production';
  port: number;
  databaseUrl: string;
  /** Shared secret the outbound engine presents when calling the account-creation API. */
  serviceApiKey: string | undefined;
  /** Secret used to hash login codes and session tokens at rest. */
  authSecret: string;
  stripeSecretKey: string | undefined;
  retellApiKey: string | undefined;
}

export function loadConfig(env: NodeJS.ProcessEnv = process.env): Config {
  const nodeEnv = (env.NODE_ENV ?? 'development') as Config['env'];
  const authSecret = env.AUTH_SECRET ?? (nodeEnv === 'production' ? undefined : 'dev-only-secret');
  if (!authSecret) throw new Error('AUTH_SECRET is required in production');
  return {
    env: nodeEnv,
    port: Number(env.PORT ?? 3000),
    databaseUrl: env.DATABASE_URL ?? 'postgres://frontdesk:frontdesk@localhost:5432/frontdesk_dev',
    serviceApiKey: env.SERVICE_API_KEY,
    authSecret,
    stripeSecretKey: env.STRIPE_SECRET_KEY,
    retellApiKey: env.RETELL_API_KEY,
  };
}
