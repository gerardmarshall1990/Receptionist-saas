import { readFile, readdir } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { z } from 'zod';

const dayHours = z.object({ open: z.string(), close: z.string() }).nullable(); // null = closed
export const businessHoursSchema = z.object({
  mon: dayHours, tue: dayHours, wed: dayHours, thu: dayHours, fri: dayHours, sat: dayHours, sun: dayHours,
}).partial();

export const faqSchema = z.object({ question: z.string().min(1), answer: z.string().min(1) });
export const serviceSchema = z.object({ name: z.string().min(1), description: z.string().optional() });

/**
 * One niche knowledge base. Shared by the outbound engine (pitch/demo) and the
 * receptionist product (layer 2 of the answer hierarchy). Swappable config, not code.
 */
export const nicheSchema = z.object({
  slug: z.string().regex(/^[a-z0-9-]+$/),
  label: z.string(),
  defaultVoiceId: z.string(),
  painPoints: z.array(z.string()).min(1),
  qualificationQuestions: z.array(z.string()),
  commonFaqs: z.array(faqSchema),
  defaultServices: z.array(serviceSchema),
  defaultCallFlow: z.array(z.string()),
  objections: z.array(z.object({ objection: z.string(), response: z.string() })),
  closeFraming: z.string(),
});
export type Niche = z.infer<typeof nicheSchema>;

const dir = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../../../knowledge/niches');

export async function listNiches(): Promise<Niche[]> {
  const files = (await readdir(dir)).filter((f) => f.endsWith('.json')).sort();
  return Promise.all(files.map(async (f) => nicheSchema.parse(JSON.parse(await readFile(path.join(dir, f), 'utf8')))));
}

export async function getNiche(slug: string): Promise<Niche | undefined> {
  if (!/^[a-z0-9-]+$/.test(slug)) return undefined; // slug becomes a filename: reject anything else
  try {
    return nicheSchema.parse(JSON.parse(await readFile(path.join(dir, `${slug}.json`), 'utf8')));
  } catch (err) {
    if ((err as NodeJS.ErrnoException).code === 'ENOENT') return undefined;
    throw err;
  }
}
