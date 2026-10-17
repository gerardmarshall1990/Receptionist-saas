import { NotConfiguredError, type VoiceProvider } from './types.js';

/**
 * Retell integration is NOT implemented yet. It is deliberately a stub that throws,
 * not a mock that returns plausible ids, so nothing can mistake it for a live receptionist.
 */
export function createRetellVoice(apiKey: string | undefined): VoiceProvider {
  const fail = async (): Promise<never> => {
    if (!apiKey) throw new NotConfiguredError('Retell');
    throw new Error('Retell integration not implemented yet');
  };
  return { provisionAgent: fail, placeTestCall: fail };
}
