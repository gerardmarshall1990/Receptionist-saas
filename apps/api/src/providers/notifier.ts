import { NotConfiguredError, type Notifier } from './types.js';

/** Development only: prints the code to the server log. Refuses to run in production. */
export function createDevConsoleNotifier(env: string): Notifier {
  return {
    async sendLoginCode(i) {
      if (env === 'production') throw new NotConfiguredError('login code delivery (SMS/email)');
      console.log(`[dev] login code for ${i.email ?? i.phone}: ${i.code}`);
    },
  };
}
