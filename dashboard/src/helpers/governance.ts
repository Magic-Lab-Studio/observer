/** Pure helpers for the governance page (no React imports, so Node can test them). */

import type { GovernanceAttrValue, GovernanceItem, GovernanceKind, GovernanceStep } from '../types';

export const KINDS: { value: GovernanceKind; label: string }[] = [
  { value: 'denials', label: 'Denegaciones' },
  { value: 'decisions', label: 'Decisiones' },
  { value: 'approvals', label: 'Aprobaciones' },
  { value: 'skills', label: 'Skills' },
];

/** Spanish label (an i18n key) of each step of a turn's governed path. */
export const STEP_LABEL: Record<string, string> = {
  decision: 'Decisión',
  security: 'Permiso',
  approval: 'Respuesta',
  tool: 'Herramienta',
  skill: 'Skill',
};

export type Tone = 'good' | 'bad' | 'warn' | 'neutral';

const BAD = new Set(['denied', 'expired', 'error', 'failed', 'rejected']);
const WARN = new Set(['asked', '__abstain__']);
const GOOD = new Set(['allowed', 'granted', 'ok', 'success']);

export function outcomeTone(outcome: string | null | undefined): Tone {
  if (!outcome) return 'neutral';
  if (BAD.has(outcome)) return 'bad';
  if (WARN.has(outcome)) return 'warn';
  if (GOOD.has(outcome)) return 'good';
  return 'neutral';
}

function text(value: GovernanceAttrValue | undefined): string {
  if (value === undefined || value === null) return '';
  if (Array.isArray(value)) return value.filter((v) => v !== null && v !== '').join(', ');
  if (typeof value === 'number') return Number.isInteger(value) ? String(value) : value.toFixed(2);
  return String(value);
}

function join(...parts: string[]): string {
  return parts.filter(Boolean).join(' · ');
}

/** One line that says what the row is about, without opening it. */
export function describe(
  item: Pick<GovernanceItem, 'span_type' | 'name' | 'attributes'>,
  tr: (key: string) => string = (key) => key,
): string {
  const a = item.attributes;
  switch (item.span_type) {
    case 'decision': {
      const choice = a['decision.choice'] === '__abstain__' ? tr('abstención') : text(a['decision.choice']);
      const conf = text(a['decision.confidence']);
      return join(text(a['decision.kind']), conf ? `${choice} (${conf})` : choice, text(a['decision.model']) || text(a['decision.backend']));
    }
    case 'security':
      return join(text(a['security.tool']), text(a['security.reason']), text(a['security.capabilities']));
    case 'approval':
      return join(text(a['approval.subject']), text(a['approval.by']), text(a['approval.reason_code']));
    case 'skill':
      return join(`${text(a['skill.id'])}${a['skill.version'] ? `@${text(a['skill.version'])}` : ''}`, text(a['skill.source']), text(a['skill.capabilities']));
    default:
      return item.name;
  }
}

/** The outcome shown in a listing's status column. */
export function rowOutcome(item: Pick<GovernanceItem, 'span_type' | 'status' | 'attributes'>): string {
  const key = { decision: 'decision.final_by', security: 'security.outcome', approval: 'approval.result', skill: 'skill.validation' }[item.span_type];
  return key ? text(item.attributes[key]) || item.status : item.status;
}

/** "Decisión → Permiso → Respuesta → Herramienta": the chain in time order, as i18n keys. */
export function pathLabels(steps: Pick<GovernanceStep, 'span_type'>[]): string[] {
  return steps.map((s) => STEP_LABEL[s.span_type] ?? s.span_type);
}
