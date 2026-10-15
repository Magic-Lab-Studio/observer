import { useState, useEffect, useCallback, useMemo } from 'react';
import { Link } from 'react-router-dom';
import { Bar, BarChart, CartesianGrid, Cell, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import { api } from '../api';
import type { Evaluation } from '../types';
import { Card, Chip, ChartTooltip, EmptyState, ErrorState, Kpi, Meter, Segmented, Skeleton } from '../components/ui';
import { fmtInt, timeAgo } from '../helpers/format';
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

function Evaluations() {
  const [evaluations, setEvaluations] = useState<Evaluation[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [page, setPage] = useState(0);
  const [total, setTotal] = useState(0);
  const [type, setType] = useState('');
  const [selected, setSelected] = useState<Evaluation | null>(null);

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

  useEffect(() => { setPage(0); void fetchEvaluations(0); }, [fetchEvaluations]);
  useEffect(() => { if (page > 0) void fetchEvaluations(page * LIMIT); }, [page, fetchEvaluations]);

  const groups = useMemo(() => (['llm_judge', 'rule_based', 'human'] as const).map((k) => {
    const items = evaluations.filter((e) => e.evaluator_type === k);
    return { key: k, items, avg: avg(items), pass: items.length ? items.filter((e) => (e.score ?? 0) >= 0.8).length / items.length : null };
  }), [evaluations]);

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
            hint={g.items.length ? t('{n} evaluaciones · {p} % ≥ 0.8', { n: g.items.length, p: (g.pass! * 100).toFixed(0) }) : t('Sin datos')}
            tone={g.avg == null ? 'neutral' : g.avg >= 0.8 ? 'good' : g.avg >= 0.5 ? 'warn' : 'bad'} />
        ))}
      </dl>

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
                      <td className="px-3 py-2.5 text-dim">{e.criteria ? Object.keys(e.criteria).join(', ') : '–'}</td>
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
                {selected.criteria && <><h4 className="mb-1 text-[11px] font-semibold uppercase tracking-wider text-faint">{t("Criterios")}</h4>
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
