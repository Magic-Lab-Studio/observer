/** Pure helpers for the ingestion-health card (no React imports, so Node can test them). */

export type IngestTone = 'ok' | 'warn' | 'bad' | 'neutral';

/** How long a project has been silent, in words: "ahora", "12 min", "3 h", "2 d". */
export function silenceText(minutes: number | null): string {
  if (minutes == null) return '–';
  if (minutes < 1) return '<1 min';
  if (minutes < 60) return `${Math.round(minutes)} min`;
  if (minutes < 1440) return `${Math.round(minutes / 60)} h`;
  return `${Math.round(minutes / 1440)} d`;
}

/** Tone of a project's silence given how often it normally sends (traces in the last hour). */
export function silenceTone(minutes: number | null, tracesLastHour: number): IngestTone {
  if (minutes == null) return 'neutral';
  if (minutes <= 10) return 'ok';
  // A project that sent several traces an hour ago and is now quiet is worth a look; one that
  // was already idle is just idle.
  if (tracesLastHour === 0 && minutes > 60) return 'neutral';
  return minutes > 60 ? 'bad' : 'warn';
}

/** A project silent for longer than this is tucked away instead of listed (an old test project). */
export const DORMANT_AFTER_MINUTES = 7 * 1440;

/** Splits projects into the ones worth listing and the dormant ones (never sent, or silent for 7+ days). */
export function splitDormant<T extends { silence_minutes: number | null }>(projects: T[]): { active: T[]; dormant: T[] } {
  const dormant = projects.filter((p) => p.silence_minutes != null && p.silence_minutes > DORMANT_AFTER_MINUTES);
  return { active: projects.filter((p) => !dormant.includes(p)), dormant };
}
