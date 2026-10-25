import { t } from '../i18n.ts';
// Pure helpers for the alerts UI. Self-contained on purpose: the Node test runner loads this file
// directly (no extensionless runtime imports).

export type MetricUnit = 'ratio' | 'ms' | 'count';
/** Labels (Spanish keys) of the rule-based turn criteria scored by the backend. */
export const CRITERION_LABEL: Record<string, string> = {
  reliability: 'Fiabilidad',
  degradation: 'Degradación',
  health: 'Salud',
  complete: 'Completitud',
  quality: 'Calidad',
  fallback: 'Vía alterna',
  latency: 'Latencia',
  ttft: 'Primer token',
};

/** Labels (Spanish keys) of the rule-based rubrics, by id without the "@version" suffix. */
export const RUBRIC_LABEL: Record<string, string> = {
  manitos_turn: 'Turno de Manitos',
  generic_turn: 'Salud operativa genérica',
};

/** "generic_turn@1" -> "Salud operativa genérica (v1)"; an unknown rubric keeps its id. */
export function rubricLabel(rubric: string): string {
  const [id, version] = rubric.split('@');
  const label = RUBRIC_LABEL[id] ? t(RUBRIC_LABEL[id]) : id;
  return version ? `${label} (v${version})` : label;
}

export type Severity = 'info' | 'warning' | 'critical';

export const SEVERITY_LABEL: Record<Severity, string> = {
  info: 'Aviso',
  warning: 'Advertencia',
  critical: 'Crítica',
};

export function severityRank(s: string): number {
  return s === 'critical' ? 0 : s === 'warning' ? 1 : 2;
}

/** The unit a person types a threshold in: percent for ratios, seconds for latency. */
export function inputUnitLabel(unit: MetricUnit): string {
  return unit === 'ratio' ? '%' : unit === 'ms' ? 's' : '';
}

export function toInputValue(unit: MetricUnit, stored: number): number {
  if (unit === 'ratio') return +(stored * 100).toFixed(4);
  if (unit === 'ms') return +(stored / 1000).toFixed(4);
  return stored;
}

export function fromInputValue(unit: MetricUnit, typed: number): number {
  if (unit === 'ratio') return typed / 100;
  if (unit === 'ms') return typed * 1000;
  return typed;
}

export function formatMetricValue(unit: MetricUnit, value: number | null | undefined): string {
  if (value == null || Number.isNaN(value)) return '–';
  if (unit === 'ratio') return `${(value * 100).toFixed(1)} %`;
  if (unit === 'ms') return value >= 1000 ? `${(value / 1000).toFixed(1)} s` : `${Math.round(value)} ms`;
  return String(Math.round(value));
}

export function windowLabel(minutes: number): string {
  if (minutes < 60) return `${minutes} min`;
  if (minutes % 1440 === 0) return `${minutes / 1440} d`;
  if (minutes % 60 === 0) return `${minutes / 60} h`;
  return `${minutes} min`;
}

export interface RuleLike {
  metric: string;
  comparator: string;
  threshold: number;
  window_minutes: number;
  scope?: string | null;
}

/** "Latencia p95 > 30 s en 1 h" (provider rules add the provider). */
export function describeRule(
  rule: RuleLike,
  metric: { label: string; unit: MetricUnit } | undefined,
): string {
  const unit = metric?.unit ?? 'count';
  const label = t(metric?.label ?? rule.metric);
  const scope = rule.scope ? ` (${rule.scope})` : '';
  const window = unit === 'count' ? '' : ` ${t('en')} ${windowLabel(rule.window_minutes)}`;
  return `${label}${scope} ${rule.comparator} ${formatMetricValue(unit, rule.threshold)}${window}`;
}

export interface EventLike {
  rule_name: string;
  severity: string;
  value: number | null;
  threshold: number;
  comparator: string;
  metric: string;
}

export function notificationBody(e: EventLike, metric: { unit: MetricUnit } | undefined): string {
  const unit = metric?.unit ?? 'count';
  return `${formatMetricValue(unit, e.value)} ${e.comparator} ${formatMetricValue(unit, e.threshold)}`;
}
