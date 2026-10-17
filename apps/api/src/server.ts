import { buildApp } from './app.js';
import { loadConfig } from './config.js';
import { createPool } from './db/pool.js';
import { createDevConsoleNotifier } from './providers/notifier.js';
import { createRetellVoice } from './providers/retell.js';
import { createStripeBilling } from './providers/stripe.js';

const config = loadConfig();
const app = buildApp({
  config,
  pool: createPool(config.databaseUrl),
  providers: {
    billing: createStripeBilling(config.stripeSecretKey),
    voice: createRetellVoice(config.retellApiKey),
    notifier: createDevConsoleNotifier(config.env),
  },
});
await app.listen({ port: config.port, host: '0.0.0.0' });
