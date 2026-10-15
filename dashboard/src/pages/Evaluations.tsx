import { useState, useEffect, useCallback, useMemo } from 'react';
import { Link } from 'react-router-dom';
import { Bar, BarChart, CartesianGrid, Cell, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import { api } from '../api';
import type { Evaluation, EvaluationsByModel, TurnCriterion } from '../types';
import { useFilters } from '../context';
import { useWebSocket } from '../hooks/useWebSocket';
import { CRITERION_LABEL } from '../helpers/alerts';
import { Card, Chip, ChartTooltip, EmptyState, ErrorState, Kpi, Meter, Segmented, Skeleton } from '../components/ui';
import { fmtInt, fmtPct, timeAgo } from '../helpers/format';
import { t } from '../i18n';

const LIMIT = 50;
const TYPES = [
  { label: 'Todas', value: '' },
  { label: 'LLM judge', value: 'llm_judge' },
  { label: 'Reglas', value: 'rule_based' },
  { label: 'Humana', value: 'human' },
];
const TYPE_NAME: Record<string, string> = { llm_judge: 'LLM judge', rule_based: 'Reglas', human: 'Humana' };

const avg = (xs: Evaluation[]) => {
  const s = xs.filter((e) => e.score != null);
  return s.length ? s.reduce((a, e) => a + (e.score ?? 0), 0) / s.length : null;
};

function criteriaText(e: Evaluation): string {
  const names = (e.criteria as { criteria?: unknown } | null)?.criteria;
  if (Array.isArray(names)) return names.map((n) => t(CRITERION_LABEL[String(n)] ?? String(n))).join(', ');
  return e.criteria ? Object.keys(e.criteria).join(', ') : '–';
}

function turnCriteria(e: Evaluation): { grade?: string; passed?: boolean; items: TurnCriterion[] } | null {
  const r = e.result as { criteria?: unknown; grade?: string; passed?: boolean } | null;
  if (!r || !Array.isArray(r.criteria) || r.criteria.length === 0 || typeof (r.criteria[0] as TurnCriterion)?.weight !== 'number') return null;
  return { grade: r.grade, passed: r.passed, items: r.criteria as TurnCriterion[] };
}

function Evaluations() {
  const [evaluations, setEvaluations] = useState<Evaluation[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [page, setPage] = useState(0);
  const [total, setTotal] = useState(0);
  const [type, setType] = useState('');
  const [selected, setSelected] = useState<Evaluation | null>(null);
  const { hours } = useFilters();
  const [byModel, setByModel] = useState<EvaluationsByModel | null>(null);

  const fetchEvaluations = useCallback(async (offset: number) => {
    setLoading(true);
    try {
      const data = await api.listEvaluations({ limit: LIMIT, offset, evaluator_type: type || undefined });
      setEvaluations(data.evaluations);
      setTotal(data.total);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : t('Error desconocido'));
    } finally {
      setLoading(false);
    }
  }, [type]);

  const loadByModel = useCallback(() => {
    api.getEvaluationsByModel(hours).then(setByModel).catch(() => setByModel(null));
  }, [hours]);

  useEffect(() => { loadByModel(); }, [loadByModel]);

  useWebSocket({
    onMessage: useCallback((m: { type: string }) => {
      if (m.type === 'new_evaluation') { void fetchEvaluations(0); loadByModel(); }
    }, [fetchEvaluations, loadByModel]),
  });

  useEffect(() => { setPage(0); void fetchEvaluations(0); }, [fetchEvaluations]);
  useEffect(() => { if (page > 0) void fetchEvaluations(page * LIMIT); }, [page, fetchEvaluations]);

  const groups = useMemo(() => (['llm_judge', 'rule_based', 'human'] as const).map((k) => {
    const items = evaluations.filter((e) => e.evaluator_type === k);
    // Rule-based turn evaluations: totals for the whole range come from the backend, not the visible page.
    if (k === 'rule_based' && byModel && byModel.count > 0) {
      return { key: k, count: byModel.count, avg: byModel.avg_score, passRate: byModel.pass_rate, passed: true };
    }
    return { key: k, count: items.length, avg: avg(items), passRate: items.length ? items.filter((e) => (e.score ?? 0) >= 0.8).length / items.length : null, passed: false };
  }), [evaluations, byModel]);

  const hist = useMemo(() => {
    const bins = Array.from({ length: 10 }, (_, i) => ({ range: `${(i / 10).toFixed(1)}–${((i + 1) / 10).toFixed(1)}`, count: 0, low: i < 5 }));
    for (const e of evaluations) if (e.score != null) bins[Math.min(9, Math.floor(e.score * 10))].count += 1;
    return bins;
  }, [evaluations]);

  const pages = Math.max(1, Math.ceil(total / LIMIT));

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-lg font-semibold tracking-tight">{t("Evaluaciones")}</h1>
          <p className="text-xs text-faint">{fmtInt(total)}  {t("en total")}</p>
        </div>
        <Segmented label={t("Tipo de evaluador")} options={TYPES.map((o) => ({ ...o, label: t(o.label) }))} value={type} onChange={setType} />
      </div>

      <dl className="grid gap-3 sm:grid-cols-3">
        {groups.map((g) => (
          <Kpi key={g.key} label={t(TYPE_NAME[g.key])} value={g.avg != null ? g.avg.toFixed(2) : '–'}
            hint={g.count ? (g.passed ? t('{n} evaluaciones · {p} % aprobadas', { n: fmtInt(g.count), p: ((g.passRate ?? 0) * 100).toFixed(0) }) : t('{n} evaluaciones · {p} % ≥ 0.8', { n: g.count, p: ((g.passRate ?? 0) * 100).toFixed(0) })) : t('Sin datos')}
            tone={g.avg == null ? 'neutral' : g.avg >= 0.8 ? 'good' : g.avg >= 0.5 ? 'warn' : 'bad'} />
        ))}
      </dl>

      {byModel && byModel.count > 0 && (
        <Card title={t('Calidad por modelo')}
          subtitle={t('Puntaje medio de las evaluaciones automáticas por reglas · {n} turnos', { n: fmtInt(byModel.count) })}
          bodyClass="p-2 pt-3">
          <div className="grid gap-4 lg:grid-cols-[minmax(0,1.4fr)_minmax(0,1fr)]">
            <ResponsiveContainer width="100%" height={Math.max(150, byModel.models.slice(0, 8).length * 34 + 30)}>
              <BarChart data={byModel.models.slice(0, 8).map((m) => ({ key: m.key, score: m.avg_score ?? 0 }))}
                layout="vertical" margin={{ top: 0, right: 16, left: 4, bottom: 0 }}>
                <CartesianGrid stroke="#232a38" horizontal={false} />
                <XAxis type="number" domain={[0, 1]} stroke="transparent" tickLine={false} />
                <YAxis type="category" dataKey="key" stroke="transparent" tickLine={false} width={190} />
                <Tooltip cursor={{ fill: 'rgba(255,255,255,.04)' }} content={<ChartTooltip valueFormatter={(v) => Number(v).toFixed(2)} />} />
                <Bar isAnimationActive={false} dataKey="score" name={t('Puntaje medio')} radius={[0, 3, 3, 0]} barSize={14}>
                  {byModel.models.slice(0, 8).map((m) => (
                    <Cell key={m.key} fill={(m.avg_score ?? 0) >= 0.8 ? '#3fb68b' : (m.avg_score ?? 0) >= 0.6 ? '#e5a33b' : '#ef5b5b'} fillOpacity={0.85} />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
            <ul className="space-y-2.5 px-2">
              {byModel.models.slice(0, 8).map((m) => (
                <li key={m.key} className="flex items-baseline justify-between gap-3 text-xs">
                  <span className="min-w-0 truncate font-mono text-dim" title={m.key}>{m.key}</span>
                  <span className="shrink-0 text-ink">{fmtPct(m.pass_rate, 0)} <span className="text-faint">{t('aprobadas')} · {fmtInt(m.count)}</span></span>
                </li>
              ))}
            </ul>
          </div>
        </Card>
      )}

      {error ? <ErrorState message={error} onRetry={() => void fetchEvaluations(page * LIMIT)} /> : (
        <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_380px]">
          <div className="space-y-4 min-w-0">
            {evaluations.some((e) => e.score != null) && (
              <Card title={t("Distribución de puntajes")} bodyClass="p-2 pt-3">
                <ResponsiveContainer width="100%" height={160}>
                  <BarChart data={hist} margin={{ top: 4, right: 12, left: 0, bottom: 0 }}>
                    <CartesianGrid stroke="#232a38" vertical={false} />
                    <XAxis dataKey="range" stroke="transparent" tickLine={false} />
                    <YAxis stroke="transparent" tickLine={false} width={28} allowDecimals={false} />
                    <Tooltip cursor={{ fill: 'rgba(255,255,255,.04)' }} content={<ChartTooltip />} />
                    <Bar isAnimationActive={false} dataKey="count" name={t("Evaluaciones")} radius={[3, 3, 0, 0]}>
                      {hist.map((b) => <Cell key={b.range} fill={b.low ? '#e5a33b' : '#3fb68b'} fillOpacity={0.85} />)}
                    </Bar>
                  </BarChart>
                </ResponsiveContainer>
              </Card>
            )}
            <div className="overflow-x-auto rounded-xl border border-line bg-panel">
              <table className="w-full min-w-[620px] text-left text-xs">
                <thead>
                  <tr className="border-b border-line text-[11px] uppercase tracking-wider text-faint">
                    <th className="px-4 py-2.5 font-medium">{t("Traza")}</th>
                    <th className="px-3 py-2.5 font-medium">{t("Evaluador")}</th>
                    <th className="px-3 py-2.5 font-medium w-[22%]">{t("Puntaje")}</th>
                    <th className="px-3 py-2.5 font-medium">{t("Criterios")}</th>
                    <th className="px-4 py-2.5 text-right font-medium">{t("Creada")}</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-line">
                  {loading && evaluations.length === 0 ? (
                    Array.from({ length: 6 }, (_, i) => <tr key={i}><td colSpan={5} className="px-4 py-2"><Skeleton className="h-6" /></td></tr>)
                  ) : evaluations.length === 0 ? (
                    <tr><td colSpan={5}><EmptyState title={t("Aún no hay evaluaciones")} hint={t("Aparecerán cuando un evaluador califique una traza.")} /></td></tr>
                  ) : evaluations.map((e) => (
                    <tr key={e.id} onClick={() => setSelected(selected?.id === e.id ? null : e)}
                      className={`cursor-pointer hover:bg-panel-2 ${selected?.id === e.id ? 'bg-raised' : ''}`}>
                      <td className="px-4 py-2.5 font-mono">
                        <Link to={`/traces/${e.trace_id}`} onClick={(ev) => ev.stopPropagation()} className="text-accent hover:underline">{e.trace_id.slice(0, 8)}…</Link>
                      </td>
                      <td className="px-3 py-2.5"><Chip tone="accent">{t(TYPE_NAME[e.evaluator_type] ?? e.evaluator_type)}</Chip></td>
                      <td className="px-3 py-2.5">
                        {e.score != null ? (
                          <div className="flex items-center gap-2"><span className="w-9 font-medium text-ink">{e.score.toFixed(2)}</span>
                            <Meter rate={e.score} tone={e.score >= 0.8 ? 'accent' : 'warn'} /></div>
                        ) : '–'}
                      </td>
                      <td className="px-3 py-2.5 text-dim">{criteriaText(e)}</td>
                      <td className="px-4 py-2.5 text-right text-faint whitespace-nowrap">{timeAgo(e.created_at)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div className="flex items-center justify-between text-xs text-faint">
              <span>{t("Página {p} de {n}", { p: page + 1, n: pages })}</span>
              <div className="flex gap-2">
                <button type="button" onClick={() => setPage((p) => Math.max(0, p - 1))} disabled={page === 0}
                  className="rounded-lg border border-line bg-panel px-3 py-1.5 text-ink hover:bg-raised disabled:opacity-40">{t("Anterior")}</button>
                <button type="button" onClick={() => setPage((p) => p + 1)} disabled={page + 1 >= pages}
                  className="rounded-lg border border-line bg-panel px-3 py-1.5 text-ink hover:bg-raised disabled:opacity-40">{t("Siguiente")}</button>
              </div>
            </div>
          </div>

          <div className="min-w-0 xl:sticky xl:top-[72px] xl:self-start">
            {selected ? (
              <Card title={t("Detalle de evaluación")} subtitle={<span className="font-mono">{selected.id}</span>}>
                <p className="mb-3 text-xs text-dim">{t("Puntaje:")} <span className="font-medium text-ink">{selected.score != null ? selected.score.toFixed(4) : 'N/D'}</span></p>
                {turnCriteria(selected) && (
                  <div className="mb-4">
                    <div className="mb-2 flex items-center gap-2 text-xs">
                      <Chip tone="accent">{t('Nota')} {turnCriteria(selected)?.grade}</Chip>
                      <Chip tone={turnCriteria(selected)?.passed ? 'accent' : 'warn'}>{turnCriteria(selected)?.passed ? t('Aprobada') : t('No aprobada')}</Chip>
                    </div>
                    <ul className="space-y-2.5">
                      {turnCriteria(selected)?.items.map((c) => (
                        <li key={c.name}>
                          <div className="mb-1 flex items-baseline justify-between text-xs">
                            <span className="text-dim">{t(CRITERION_LABEL[c.name] ?? c.name)} <span className="text-faint">· {c.detail}</span></span>
                            <span className="font-medium text-ink">{c.score.toFixed(2)}</span>
                          </div>
                          <Meter rate={c.score} tone={c.score >= 0.8 ? 'accent' : c.score >= 0.4 ? 'warn' : 'bad'} />
                        </li>
                      ))}
                    </ul>
                  </div>
                )}
                {selected.criteria && !turnCriteria(selected) && <><h4 className="mb-1 text-[11px] font-semibold uppercase tracking-wider text-faint">{t("Criterios")}</h4>
                  <pre className="mb-3 max-h-60 overflow-auto rounded-lg bg-canvas p-3 text-xs text-dim">{JSON.stringify(selected.criteria, null, 2)}</pre></>}
                {selected.result && <><h4 className="mb-1 text-[11px] font-semibold uppercase tracking-wider text-faint">{t("Resultado")}</h4>
                  <pre className="max-h-72 overflow-auto rounded-lg bg-canvas p-3 text-xs text-dim">{JSON.stringify(selected.result, null, 2)}</pre></>}
              </Card>
            ) : <Card title={t("Detalle de evaluación")}><EmptyState title={t("Selecciona una fila")} /></Card>}
          </div>
        </div>
      )}
    </div>
  );
}

export default Evaluations;
