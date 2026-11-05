// How a turn ended (agent contract observer.agent.v1): labels, tones and the "why" lines the
// trace page shows. Pure functions; the Spanish text is the i18n key.

export const OUTCOMES = ['answered', 'degraded', 'abstained', 'clarification', 'refused', 'failed', 'unknown'] as const;
export type Outcome = (typeof OUTCOMES)[number];

export const OUTCOME_LABEL: Record<string, string> = {
  answered: 'Respondido',
  degraded: 'Degradado',
  abstained: 'Abstenido',
  clarification: 'Pidió aclaración',
  refused: 'Rechazado',
  failed: 'Fallido',
  unknown: 'Sin resultado conocido',
};

export const SOURCE_LABEL: Record<string, string> = {
  attribute: 'declarado por el agente',
  close: 'paso de cierre',
  status: 'la traza terminó en error',
  flags: 'respuesta degradada o cortada',
  evidence: 'el modelo terminó su respuesta',
  none: 'sin evidencia',
};

export type Tone = 'good' | 'warn' | 'bad' | 'neutral';

export function outcomeTone(outcome: string | null | undefined): Tone {
  if (outcome === 'answered') return 'good';
  if (outcome === 'failed') return 'bad';
  if (outcome === 'degraded' || outcome === 'abstained' || outcome === 'clarification' || outcome === 'refused') return 'warn';
  return 'neutral';
}

export interface GateInfo { name: string; stage: string; status: string; reason_codes: string[] }
export interface OutcomeInfo {
  outcome?: string; outcome_source?: string; outcome_reason?: string;
  abstained_with_evidence?: boolean; retrieval_passages?: number; gates?: GateInfo[];
}

export interface WhyLine { key: string; label: string; detail: string; tone: Tone }

/** The lines of the "why" panel, in reading order; empty when the trace has no outcome data. */
export function whyLines(info: OutcomeInfo | null | undefined): WhyLine[] {
  if (!info?.outcome) return [];
  const lines: WhyLine[] = [{
    key: 'outcome',
    label: OUTCOME_LABEL[info.outcome] ?? info.outcome,
    detail: SOURCE_LABEL[info.outcome_source ?? 'none'] ?? info.outcome_source ?? '',
    tone: outcomeTone(info.outcome),
  }];
  if (info.outcome_reason) lines.push({ key: 'reason', label: 'Razón', detail: info.outcome_reason, tone: 'neutral' });
  for (const g of info.gates ?? []) {
    if (g.status !== 'stop') continue;
    lines.push({
      key: `gate:${g.name}`,
      label: 'Compuerta que detuvo el turno',
      detail: [g.name, ...(g.reason_codes ?? [])].filter(Boolean).join(' · '),
      tone: 'warn',
    });
  }
  if (info.abstained_with_evidence) {
    lines.push({
      key: 'evidence',
      label: 'Se abstuvo con evidencia',
      detail: String(info.retrieval_passages ?? 0),
      tone: 'warn',
    });
  }
  return lines;
}

/** Outcome rates from an Explore query grouped by outcome: [{outcome, count, share}], largest first. */
export function outcomeShares(rows: { outcome?: unknown; count?: unknown }[]): { outcome: string; count: number; share: number }[] {
  const counts = rows
    .map((r) => ({ outcome: String(r.outcome ?? 'unknown'), count: Number(r.count ?? 0) }))
    .filter((r) => r.count > 0);
  const total = counts.reduce((sum, r) => sum + r.count, 0);
  return counts
    .map((r) => ({ ...r, share: total ? r.count / total : 0 }))
    .sort((a, b) => b.count - a.count || a.outcome.localeCompare(b.outcome));
}
