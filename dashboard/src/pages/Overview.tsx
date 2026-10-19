import { useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import {
  Bar, CartesianGrid, Cell, ComposedChart, Line, Pie, PieChart,
  ResponsiveContainer, Tooltip, XAxis, YAxis, BarChart,
} from 'recharts';
import { api } from '../api';
import { useFilters, SAMPLE_LIMIT } from '../context';
import type { CostByModel, ManitOSQualitySummary, QueryResult, SummaryData } from '../types';
import { fillBuckets } from '../helpers/query';
import type { Bucket } from '../helpers/stats';
import { bucketLabel } from '../helpers/stats';
import { parseApiDate } from '../dates';
import { Card, ChartTooltip, EmptyState, ErrorState, Kpi, Meter, PALETTE, Skeleton, StatusDot } from '../components/ui';
import IngestHealth from '../components/IngestHealth';
import { mergeModelRows } from '../helpers/chain';
import { bucketTraces, histogram, percentile, sortedDurations } from '../helpers/stats';
import { fmtAxisMs, fmtInt, fmtMs, fmtPct, fmtUsd, shortId, stageLabel, stageTimings, timeAgo, traceDurationMs } from '../helpers/format';
import { t } from '../i18n';

const AXIS = { stroke: 'transparent', tickLine: false, axisLine: false } as const;

/** Model shares from the spans' model attributes, for applications that are not Manitos. */
function modelRowsFromSpans(rows: CostByModel[]): { key: string; count: number; rate: number }[] {
  const total = rows.reduce((n, r) => n + r.span_count, 0);
  return [...rows].sort((a, b) => b.span_count - a.span_count)
    .map((r) => ({ key: r.model, count: r.span_count, rate: total ? r.span_count / total : 0 }));
}

function errorTone(rate: number | null | undefined): 'bad' | 'warn' | 'good' | 'neutral' {
  if (rate == null) return 'neutral';
  return rate > 0.05 ? 'bad' : rate > 0 ? 'warn' : 'good';
}

function Overview() {
  const { hours, env, windowTraces, windowStart, windowEnd, sampleTruncated, totalTraces, loading, error, refresh, newIds } = useFilters();
  const [quality, setQuality] = useState<ManitOSQualitySummary | null>(null);
  const [summary, setSummary] = useState<SummaryData | null>(null);
  const [costByModel, setCostByModel] = useState<CostByModel[]>([]);

  useEffect(() => {
    let live = true;
    Promise.all([api.getManitOSQuality(hours, 'manitos', env || undefined), api.getSummary(hours)])
      .then(([q, s]) => { if (live) { setQuality(q); setSummary(s); } })
      .catch(() => { if (live) setQuality(null); });
    api.getCostByModel(hours).then((rows) => { if (live) setCostByModel(rows); }).catch(() => undefined);
    return () => { live = false; };
  }, [hours, env, totalTraces]);

  // The activity chart, sparklines and latency KPIs come from the metrics query, so a 7 d or 30 d range
  // uses every turn instead of the last 1000 traces; the client-side numbers remain as the fallback.
  const [serverStats, setServerStats] = useState<{ series: QueryResult; totals: QueryResult } | null>(null);
  useEffect(() => {
    let live = true;
    const base = {
      view: 'turns', hours, dimensions: [], row_limit: 1000,
      filters: env ? [{ field: 'environment', op: '=', value: env }] : [],
    };
    const core = [
      { measure: 'count', aggregation: 'count' }, { measure: 'duration_ms', aggregation: 'p50' },
      { measure: 'duration_ms', aggregation: 'p95' },
    ];
    Promise.all([
      api.queryMetrics({ ...base, granularity: 'auto', metrics: [...core, { measure: 'error', aggregation: 'sum' }] }),
      api.queryMetrics({ ...base, metrics: [...core, { measure: 'error', aggregation: 'avg' }] }),
    ]).then(([series, totals]) => { if (live) setServerStats({ series, totals }); })
      .catch(() => { if (live) setServerStats(null); });
    return () => { live = false; };
  }, [hours, env, totalTraces]);

  const server = useMemo(() => {
    if (!serverStats || !serverStats.series.granularity) return null;
    const { series, totals } = serverStats;
    const granularity = series.granularity as string;
    const start = parseApiDate(series.from).getTime();
    const end = parseApiDate(series.to).getTime();
    const num = (v: unknown) => (typeof v === 'number' ? v : null);
    const rows = series.rows.map((r) => ({ t: parseApiDate(String(r.time)).getTime(), r }));
    const buckets: Bucket[] = fillBuckets(rows, start, end, granularity).map(({ t, row }) => {
      const count = num(row?.r.count) ?? 0;
      const err = num(row?.r.sum_error) ?? 0;
      return {
        t, label: bucketLabel(t, end - start), ok: count - err, err, total: count,
        p50: num(row?.r.p50_duration_ms), p95: num(row?.r.p95_duration_ms), errRate: count ? err / count : 0,
      };
    });
    const tot = totals.rows[0] ?? {};
    return { buckets, p50: num(tot.p50_duration_ms), p95: num(tot.p95_duration_ms), errRate: num(tot.avg_error), count: num(tot.count) ?? 0 };
  }, [serverStats]);

  const model = useMemo(() => {
    const durations = sortedDurations(windowTraces);
    const buckets = bucketTraces(windowTraces, windowStart, windowEnd, hours <= 1 ? 30 : 48);
    const stages = new Map<string, number[]>();
    for (const t of windowTraces) {
      for (const [k, v] of Object.entries(stageTimings(t))) {
        const key = stageLabel(k);
        if (!stages.has(key)) stages.set(key, []);
        stages.get(key)!.push(v);
      }
    }
    const stageRows = Array.from(stages, ([stage, vals]) => {
      const s = [...vals].sort((a, b) => a - b);
      return { stage, avg: s.reduce((a, b) => a + b, 0) / s.length, p95: percentile(s, 0.95) ?? 0, n: s.length };
    }).sort((a, b) => b.avg - a.avg).slice(0, 8);
    const slowest = [...windowTraces]
      .map((t) => ({ t, d: traceDurationMs(t) }))
      .filter((x): x is { t: typeof x.t; d: number } => x.d != null)
      .sort((a, b) => b.d - a.d)
      .slice(0, 6);
    const errors = windowTraces.filter((t) => t.status === 'error').length;
    return {
      durations, buckets, stageRows, slowest,
      p50: percentile(durations, 0.5), p95: percentile(durations, 0.95),
      errors, hist: histogram(durations),
    };
  }, [windowTraces, windowStart, windowEnd, hours]);

  if (error && windowTraces.length === 0) return <ErrorState message={error} onRetry={refresh} />;

  // Manitos turn metrics only when that integration sent turns; every other application gets
  // the generic trace metrics (evaluation C3: generic traces showed "0 turns, 0 % errors").
  const isManitos = (quality?.total_turns ?? 0) > 0;
  const q = isManitos ? quality : null;
  // Turn wording and turn statistics only when every trace in range is a Manitos turn; mixed data
  // keeps the generic numbers and shows the Manitos panels beside them.
  const allTurns = isManitos && (!summary || (q?.total_turns ?? 0) >= summary.total_traces);
  const turnServer = allTurns && server && server.count > 0 ? server : null;
  const total = turnServer ? turnServer.count : (summary?.total_traces ?? windowTraces.length);
  const errRate = turnServer?.errRate ?? summary?.trace_error_rate
    ?? (windowTraces.length ? model.errors / windowTraces.length : null);
  const errCount = summary?.error_count ?? model.errors;
  const buckets = turnServer?.buckets ?? model.buckets;
  const p50v = turnServer?.p50 ?? summary?.p50_trace_duration_ms ?? model.p50;
  const p95v = turnServer?.p95 ?? summary?.p95_trace_duration_ms ?? model.p95;
  const hasData = total > 0 || windowTraces.length > 0;
  const modelsPie = allTurns
    ? mergeModelRows(q?.models ?? []).slice(0, 6)
    : modelRowsFromSpans(costByModel).slice(0, 6);
  const langs = q?.languages ?? [];
  const unit = allTurns ? t('turnos') : t('trazas');

  const qualityRows = q ? [
    { label: 'Errores', n: q.error_count, rate: q.error_rate, tone: 'bad' as const },
    { label: 'Respuesta degradada', n: q.degraded_count, rate: q.degraded_rate, tone: 'warn' as const },
    { label: 'Truncadas', n: q.truncated_count, rate: q.truncated_rate, tone: 'warn' as const },
    { label: 'Error en herramientas', n: q.tool_error_count, rate: q.tool_error_rate, tone: 'bad' as const },
    { label: 'Vía alterna (fallback)', n: q.fallback_count, rate: q.fallback_rate, tone: 'accent' as const },
    { label: 'Error de voz (TTS)', n: q.tts_error_count, rate: q.tts_error_rate, tone: 'bad' as const },
  ] : [];

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-2">
        <div>
          <h1 className="text-lg font-semibold tracking-tight">{t("Resumen")}</h1>
          <p className="text-xs text-faint">
            {env ? t('Entorno {env}', { env }) : t('Todos los entornos')}
            {sampleTruncated && ` · ${t('histograma, etapas y listas con una muestra de las últimas {n} trazas de {total}', { n: fmtInt(SAMPLE_LIMIT), total: fmtInt(totalTraces) })}`}
          </p>
        </div>
      </div>

      {/* KPI strip */}
      <dl className="grid grid-cols-2 gap-3 md:grid-cols-4 xl:grid-cols-7">
        {loading && !hasData ? Array.from({ length: 7 }, (_, i) => <Skeleton key={i} className="h-[118px]" />) : (
          <>
            <Kpi label={allTurns ? t("Turnos") : t("Trazas")} value={fmtInt(allTurns ? q?.total_turns : total)}
              hint={!allTurns && summary?.running_traces ? t('{n} en curso', { n: fmtInt(summary.running_traces) }) : `${fmtInt(summary?.total_spans)} spans`}
              series={buckets.map((b) => b.total)} color="#6c8cff" />
            <Kpi label={t("Tasa de error")} value={fmtPct(allTurns ? q?.error_rate : errRate)}
              hint={t('{n} fallidos', { n: fmtInt(allTurns ? q?.error_count : errCount) })}
              tone={errorTone(allTurns ? q?.error_rate : errRate)}
              series={buckets.map((b) => b.errRate)} color="#ef5b5b" />
            <Kpi label={t("Latencia p50")} value={fmtMs(p50v)} hint={`p95 ${fmtMs(p95v)}`}
              series={buckets.map((b) => b.p50 ?? 0)} color="#3fb68b" />
            <Kpi label={t("Latencia p95")} value={fmtMs(p95v)} hint={t('peor 5 % de {u}', { u: unit })}
              series={buckets.map((b) => b.p95 ?? 0)} color="#e5a33b" />
            {isManitos ? (
              <>
                <Kpi label={allTurns ? t("Primer token (media)") : t("Manitos · primer token")} value={fmtMs(q?.avg_ttft_ms)} hint={t('turno medio {v}', { v: fmtMs(q?.avg_duration_ms) })} />
                <Kpi label={allTurns ? t("Vía alterna") : t("Manitos · vía alterna")} value={fmtPct(q?.fallback_rate)} hint={t('{n} turnos', { n: fmtInt(q?.fallback_count) })}
                  tone={(q?.fallback_rate ?? 0) > 0.25 ? 'warn' : 'neutral'} />
              </>
            ) : (
              <>
                <Kpi label={t("Llamadas a modelos")} value={fmtInt(summary?.llm_span_count)}
                  hint={t('{n} de herramientas', { n: fmtInt(summary?.tool_span_count) })} />
                <Kpi label={t("Spans con error")} value={fmtInt(summary?.span_error_count)}
                  hint={t('de {n} spans', { n: fmtInt(summary?.total_spans) })}
                  tone={(summary?.span_error_count ?? 0) > 0 ? 'warn' : 'neutral'} />
              </>
            )}
            <Kpi label={t("Costo")} value={summary && summary.priced_span_count === 0 && summary.total_spans > 0 ? '–' : fmtUsd(summary?.total_cost_usd ?? 0)}
              hint={summary && summary.priced_span_count === 0 && summary.total_spans > 0
                ? t('ningún span informó costo')
                : (summary?.total_cost_usd ?? 0) === 0 ? t('modelos gratuitos o locales') : `${fmtInt(summary?.total_spans)} spans`} />
          </>
        )}
      </dl>

      {/* Main chart + quality */}
      <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_340px]">
        <Card title={t("Actividad y latencia")}
          subtitle={allTurns ? t("Turnos por intervalo (barras, rojo = error) y latencia p95 (línea)") : t("Trazas por intervalo (barras, rojo = error) y latencia p95 (línea)")}
          bodyClass="p-2 pt-3">
          {hasData ? (
            <ResponsiveContainer width="100%" height={300}>
              <ComposedChart data={buckets} margin={{ top: 6, right: 12, left: 0, bottom: 0 }}>
                <CartesianGrid stroke="#232a38" vertical={false} />
                <XAxis dataKey="label" {...AXIS} interval="preserveStartEnd" minTickGap={36} />
                <YAxis yAxisId="n" {...AXIS} width={34} allowDecimals={false} />
                <YAxis yAxisId="ms" orientation="right" {...AXIS} width={46} tickFormatter={fmtAxisMs} />
                <Tooltip cursor={{ fill: 'rgba(255,255,255,.04)' }}
                  content={<ChartTooltip valueFormatter={(v, n) => (n === t('Latencia p95') ? fmtMs(Number(v)) : String(v))} />} />
                <Bar isAnimationActive={false} yAxisId="n" dataKey="ok" name={t("Correctos")} stackId="a" fill="#6c8cff" fillOpacity={0.85} radius={[0, 0, 0, 0]} />
                <Bar isAnimationActive={false} yAxisId="n" dataKey="err" name={t("Errores")} stackId="a" fill="#ef5b5b" radius={[3, 3, 0, 0]} />
                <Line isAnimationActive={false} yAxisId="ms" dataKey="p95" name={t("Latencia p95")} stroke="#e5a33b" strokeWidth={2} dot={false} connectNulls />
              </ComposedChart>
            </ResponsiveContainer>
          ) : <EmptyState title={allTurns ? t("Sin turnos en este rango") : t("Sin trazas en este rango")} hint={t("Prueba un rango más amplio.")} />}
        </Card>

        {isManitos ? <Card title={t("Calidad de turnos")} subtitle={q ? `${t('{n} turnos', { n: fmtInt(q.total_turns) })} · ${hours >= 24 ? `${Math.round(hours / 24)} d` : `${hours} h`}` : undefined}>
          {q ? (
            <ul className="space-y-3.5">
              {qualityRows.map((r) => (
                <li key={t(r.label)}>
                  <div className="mb-1 flex items-baseline justify-between text-xs">
                    <span className="text-dim">{t(r.label)}</span>
                    <span className="text-ink font-medium">{fmtPct(r.rate)} <span className="text-faint font-normal">· {fmtInt(r.n)}</span></span>
                  </div>
                  <Meter rate={r.rate} tone={r.tone} />
                </li>
              ))}
            </ul>
          ) : <Skeleton className="h-60" />}
        </Card> : (
          <Card title={t("Estado de las trazas")} subtitle={summary ? t('{n} trazas', { n: fmtInt(summary.total_traces) }) : undefined}>
            {summary ? (
              <ul className="space-y-3.5">
                {[
                  { label: t('Con error'), n: summary.error_count, rate: summary.trace_error_rate ?? 0, tone: 'bad' as const },
                  { label: t('En curso o incompletas'), n: summary.running_traces ?? 0, rate: summary.total_traces ? (summary.running_traces ?? 0) / summary.total_traces : 0, tone: 'accent' as const },
                  { label: t('Spans con error'), n: summary.span_error_count ?? 0, rate: summary.total_spans ? (summary.span_error_count ?? 0) / summary.total_spans : 0, tone: 'warn' as const },
                ].map((r) => (
                  <li key={r.label}>
                    <div className="mb-1 flex items-baseline justify-between text-xs">
                      <span className="text-dim">{r.label}</span>
                      <span className="text-ink font-medium">{fmtPct(r.rate)} <span className="text-faint font-normal">· {fmtInt(r.n)}</span></span>
                    </div>
                    <Meter rate={r.rate} tone={r.tone} />
                  </li>
                ))}
              </ul>
            ) : <Skeleton className="h-60" />}
          </Card>
        )}
      </div>

      {/* Distribution row */}
      <div className="grid gap-4 lg:grid-cols-2 2xl:grid-cols-3">
        <Card title={t("Distribución de latencia")} subtitle={hasData ? `p50 ${fmtMs(p50v)} · p95 ${fmtMs(p95v)} · ${t('máx')} ${fmtMs(model.durations[model.durations.length - 1])}` : undefined} bodyClass="p-2 pt-3">
          {hasData ? (
            <ResponsiveContainer width="100%" height={230}>
              <BarChart data={model.hist} margin={{ top: 4, right: 12, left: 0, bottom: 0 }}>
                <CartesianGrid stroke="#232a38" vertical={false} />
                <XAxis dataKey="range" {...AXIS} />
                <YAxis {...AXIS} width={34} allowDecimals={false} />
                <Tooltip cursor={{ fill: 'rgba(255,255,255,.04)' }} content={<ChartTooltip valueFormatter={(v) => `${v} ${unit}`} />} />
                <Bar isAnimationActive={false} dataKey="count" name={allTurns ? t("Turnos") : t("Trazas")} radius={[3, 3, 0, 0]}>
                  {model.hist.map((h) => (
                    <Cell key={h.range} fill={h.from >= 20000 ? '#ef5b5b' : h.from >= 10000 ? '#e5a33b' : '#3fb68b'} fillOpacity={0.85} />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          ) : <EmptyState title={t("Sin datos")} />}
        </Card>

        {model.stageRows.length > 0 && <Card title={t("Tiempo por etapa")} subtitle={t("Media y p95 por etapa del turno")} bodyClass="p-2 pt-3">
          {model.stageRows.length > 0 ? (
            <ResponsiveContainer width="100%" height={230}>
              <BarChart data={model.stageRows} layout="vertical" margin={{ top: 0, right: 16, left: 4, bottom: 0 }} barGap={2}>
                <CartesianGrid stroke="#232a38" horizontal={false} />
                <XAxis type="number" {...AXIS} tickFormatter={fmtAxisMs} />
                <YAxis type="category" dataKey="stage" {...AXIS} width={118} />
                <Tooltip cursor={{ fill: 'rgba(255,255,255,.04)' }} content={<ChartTooltip valueFormatter={(v) => fmtMs(Number(v))} />} />
                <Bar isAnimationActive={false} dataKey="avg" name={t("Media")} fill="#6c8cff" radius={[0, 3, 3, 0]} barSize={8} />
                <Bar isAnimationActive={false} dataKey="p95" name={t("p95")} fill="#e5a33b" fillOpacity={0.7} radius={[0, 3, 3, 0]} barSize={8} />
              </BarChart>
            </ResponsiveContainer>
          ) : <EmptyState title={t("Sin tiempos por etapa")} />}
        </Card>}

        <Card title={t("Modelos")} subtitle={allTurns ? t("Quién respondió cada turno") : t("Llamadas por modelo")} className={model.stageRows.length > 0 ? 'lg:col-span-2 2xl:col-span-1' : undefined}>
          {modelsPie.length > 0 ? (
            <div className="flex items-center gap-4">
              <div className="size-[150px] shrink-0">
                <ResponsiveContainer width="100%" height="100%">
                  <PieChart>
                    <Pie isAnimationActive={false} data={modelsPie} dataKey="count" nameKey="key" innerRadius={46} outerRadius={70} paddingAngle={2} stroke="none">
                      {modelsPie.map((m, i) => <Cell key={m.key} fill={PALETTE[i % PALETTE.length]} />)}
                    </Pie>
                    <Tooltip content={<ChartTooltip valueFormatter={(v) => `${v} ${allTurns ? t('turnos') : t('llamadas')}`} />} />
                  </PieChart>
                </ResponsiveContainer>
              </div>
              <ul className="min-w-0 flex-1 space-y-2">
                {modelsPie.map((m, i) => (
                  <li key={m.key} className="flex items-center gap-2 text-xs" title={m.key}>
                    <span className="size-2 shrink-0 rounded-sm" style={{ background: PALETTE[i % PALETTE.length] }} />
                    <span className="truncate text-dim">{m.key}</span>
                    <span className="ml-auto shrink-0 font-medium text-ink">{fmtPct(m.rate, 0)}</span>
                  </li>
                ))}
              </ul>
            </div>
          ) : <EmptyState title={t("Sin datos de modelos")} />}
          {langs.length > 0 && (
            <div className="mt-4 flex flex-wrap items-center gap-1.5 border-t border-line pt-3 text-xs text-faint">
              
              {t("Idiomas:")}
              {langs.map((l) => (
                <span key={l.key} className="rounded-md bg-raised px-1.5 py-0.5 text-dim">{l.key} · {fmtPct(l.rate, 0)}</span>
              ))}
            </div>
          )}
        </Card>
      </div>

      {/* Lists */}
      <div className="grid gap-4 xl:grid-cols-[minmax(0,1.6fr)_minmax(0,1fr)]">
        <Card title={allTurns ? t("Turnos recientes") : t("Trazas recientes")} subtitle={t("Se actualiza en vivo")}
          actions={<Link to="/traces" className="text-xs text-accent hover:underline">{t("Ver todas")}</Link>} bodyClass="p-0 pt-2">
          {hasData ? (
            <ul className="divide-y divide-line">
              {windowTraces.slice(0, 10).map((t) => {
                const d = traceDurationMs(t);
                return (
                  <li key={t.id} className={newIds.has(t.id) ? 'flash-row' : undefined}>
                    <Link to={`/traces/${t.id}`} className="flex items-center gap-3 px-4 py-2 text-xs hover:bg-panel-2">
                      <StatusDot status={t.status} />
                      <span className="min-w-0 flex-1 truncate text-ink" title={t.name}>{t.name}</span>
                      <span className="hidden w-24 shrink-0 font-mono text-faint sm:inline">{shortId(t.turn_id ?? t.id, 7)}</span>
                      <span className="hidden w-20 shrink-0 text-faint md:inline">{t.environment ?? '–'}</span>
                      <span className="w-16 text-right font-medium text-ink">{fmtMs(d)}</span>
                      <span className="w-20 text-right text-faint">{timeAgo(t.created_at)}</span>
                    </Link>
                  </li>
                );
              })}
            </ul>
          ) : <EmptyState title={allTurns ? t("Sin turnos recientes") : t("Sin trazas recientes")} />}
        </Card>

        <Card title={allTurns ? t("Turnos más lentos") : t("Trazas más lentas")} subtitle={t("Mayor latencia en el rango")} bodyClass="p-0 pt-2">
          {model.slowest.length > 0 ? (
            <ul className="divide-y divide-line">
              {model.slowest.map(({ t, d }) => {
                const pct = model.durations.length ? d / model.durations[model.durations.length - 1] : 0;
                return (
                  <li key={t.id}>
                    <Link to={`/traces/${t.id}`} className="block px-4 py-2 hover:bg-panel-2">
                      <div className="flex items-center justify-between text-xs">
                        <span className="min-w-0 truncate text-dim" title={t.name}>{t.name} <span className="font-mono text-faint">{shortId(t.turn_id ?? t.id, 7)}</span></span>
                        <span className="shrink-0 font-medium text-ink">{fmtMs(d)}</span>
                      </div>
                      <div className="mt-1.5"><Meter rate={pct} tone={t.status === 'error' ? 'bad' : 'warn'} /></div>
                    </Link>
                  </li>
                );
              })}
            </ul>
          ) : <EmptyState title={t("Sin datos")} />}
        </Card>
      </div>

      <IngestHealth />
    </div>
  );
}

export default Overview;
