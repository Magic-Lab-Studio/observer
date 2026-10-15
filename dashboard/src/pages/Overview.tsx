import { useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import {
  Bar, CartesianGrid, Cell, ComposedChart, Line, Pie, PieChart,
  ResponsiveContainer, Tooltip, XAxis, YAxis, BarChart,
} from 'recharts';
import { api } from '../api';
import { useFilters, SAMPLE_LIMIT } from '../context';
import type { ManitOSQualitySummary, SummaryData } from '../types';
import { Card, ChartTooltip, EmptyState, ErrorState, Kpi, Meter, PALETTE, Skeleton, StatusDot } from '../components/ui';
import { mergeModelRows } from '../helpers/chain';
import { bucketTraces, histogram, percentile, sortedDurations } from '../helpers/stats';
import { fmtAxisMs, fmtInt, fmtMs, fmtPct, fmtUsd, shortId, stageLabel, stageTimings, timeAgo, traceDurationMs } from '../helpers/format';

const AXIS = { stroke: 'transparent', tickLine: false, axisLine: false } as const;

function Overview() {
  const { hours, env, windowTraces, windowStart, windowEnd, sampleTruncated, totalTraces, loading, error, refresh, newIds } = useFilters();
  const [quality, setQuality] = useState<ManitOSQualitySummary | null>(null);
  const [summary, setSummary] = useState<SummaryData | null>(null);

  useEffect(() => {
    let live = true;
    Promise.all([api.getManitOSQuality(hours, 'manitos', env || undefined), api.getSummary(hours)])
      .then(([q, s]) => { if (live) { setQuality(q); setSummary(s); } })
      .catch(() => { if (live) setQuality(null); });
    return () => { live = false; };
  }, [hours, env, totalTraces]);

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
      .map((t) => ({ t, d: traceDurationMs(t) ?? 0 }))
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

  const q = quality;
  const total = windowTraces.length;
  const errRate = total ? model.errors / total : 0;
  const hasData = total > 0;
  const modelsPie = mergeModelRows(q?.models ?? []).slice(0, 6);
  const langs = q?.languages ?? [];

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
          <h1 className="text-lg font-semibold tracking-tight">Resumen</h1>
          <p className="text-xs text-faint">
            {env ? `Entorno ${env}` : 'Todos los entornos'}
            {sampleTruncated && ` · muestra de las últimas ${fmtInt(SAMPLE_LIMIT)} trazas de ${fmtInt(totalTraces)}`}
          </p>
        </div>
      </div>

      {/* KPI strip */}
      <dl className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
        {loading && !hasData ? Array.from({ length: 6 }, (_, i) => <Skeleton key={i} className="h-[118px]" />) : (
          <>
            <Kpi label="Turnos" value={fmtInt(q?.total_turns ?? total)} hint={`${fmtInt(summary?.total_spans)} spans`}
              series={model.buckets.map((b) => b.total)} color="#6c8cff" />
            <Kpi label="Tasa de error" value={fmtPct(q?.error_rate ?? errRate)}
              hint={`${fmtInt(q?.error_count ?? model.errors)} fallidos`}
              tone={(q?.error_rate ?? errRate) > 0.05 ? 'bad' : (q?.error_rate ?? errRate) > 0 ? 'warn' : 'good'}
              series={model.buckets.map((b) => b.errRate)} color="#ef5b5b" />
            <Kpi label="Latencia p50" value={fmtMs(model.p50)} hint={`p95 ${fmtMs(model.p95)}`}
              series={model.buckets.map((b) => b.p50 ?? 0)} color="#3fb68b" />
            <Kpi label="Latencia p95" value={fmtMs(model.p95)} hint="peor 5 % de turnos"
              series={model.buckets.map((b) => b.p95 ?? 0)} color="#e5a33b" />
            <Kpi label="Primer token (media)" value={fmtMs(q?.avg_ttft_ms)} hint={`turno medio ${fmtMs(q?.avg_duration_ms)}`} />
            <Kpi label="Vía alterna" value={fmtPct(q?.fallback_rate)} hint={`${fmtInt(q?.fallback_count)} turnos`}
              tone={(q?.fallback_rate ?? 0) > 0.25 ? 'warn' : 'neutral'} />
          </>
        )}
      </dl>

      {/* Main chart + quality */}
      <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_340px]">
        <Card title="Actividad y latencia"
          subtitle="Turnos por intervalo (barras, rojo = error) y latencia p95 (línea)"
          bodyClass="p-2 pt-3">
          {hasData ? (
            <ResponsiveContainer width="100%" height={300}>
              <ComposedChart data={model.buckets} margin={{ top: 6, right: 12, left: 0, bottom: 0 }}>
                <CartesianGrid stroke="#232a38" vertical={false} />
                <XAxis dataKey="label" {...AXIS} interval="preserveStartEnd" minTickGap={36} />
                <YAxis yAxisId="n" {...AXIS} width={34} allowDecimals={false} />
                <YAxis yAxisId="ms" orientation="right" {...AXIS} width={46} tickFormatter={fmtAxisMs} />
                <Tooltip cursor={{ fill: 'rgba(255,255,255,.04)' }}
                  content={<ChartTooltip valueFormatter={(v, n) => (n.startsWith('Latencia') ? fmtMs(Number(v)) : String(v))} />} />
                <Bar isAnimationActive={false} yAxisId="n" dataKey="ok" name="Correctos" stackId="a" fill="#6c8cff" fillOpacity={0.85} radius={[0, 0, 0, 0]} />
                <Bar isAnimationActive={false} yAxisId="n" dataKey="err" name="Errores" stackId="a" fill="#ef5b5b" radius={[3, 3, 0, 0]} />
                <Line isAnimationActive={false} yAxisId="ms" dataKey="p95" name="Latencia p95" stroke="#e5a33b" strokeWidth={2} dot={false} connectNulls />
              </ComposedChart>
            </ResponsiveContainer>
          ) : <EmptyState title="Sin turnos en este rango" hint="Prueba un rango más amplio." />}
        </Card>

        <Card title="Calidad de turnos" subtitle={q ? `${fmtInt(q.total_turns)} turnos · ${hours >= 24 ? `${Math.round(hours / 24)} d` : `${hours} h`}` : undefined}>
          {q ? (
            <ul className="space-y-3.5">
              {qualityRows.map((r) => (
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
      </div>

      {/* Distribution row */}
      <div className="grid gap-4 lg:grid-cols-2 2xl:grid-cols-3">
        <Card title="Distribución de latencia" subtitle={hasData ? `p50 ${fmtMs(model.p50)} · p95 ${fmtMs(model.p95)} · máx ${fmtMs(model.durations[model.durations.length - 1])}` : undefined} bodyClass="p-2 pt-3">
          {hasData ? (
            <ResponsiveContainer width="100%" height={230}>
              <BarChart data={model.hist} margin={{ top: 4, right: 12, left: 0, bottom: 0 }}>
                <CartesianGrid stroke="#232a38" vertical={false} />
                <XAxis dataKey="range" {...AXIS} />
                <YAxis {...AXIS} width={34} allowDecimals={false} />
                <Tooltip cursor={{ fill: 'rgba(255,255,255,.04)' }} content={<ChartTooltip valueFormatter={(v) => `${v} turnos`} />} />
                <Bar isAnimationActive={false} dataKey="count" name="Turnos" radius={[3, 3, 0, 0]}>
                  {model.hist.map((h) => (
                    <Cell key={h.range} fill={h.from >= 20000 ? '#ef5b5b' : h.from >= 10000 ? '#e5a33b' : '#3fb68b'} fillOpacity={0.85} />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          ) : <EmptyState title="Sin datos" />}
        </Card>

        <Card title="Tiempo por etapa" subtitle="Media y p95 por etapa del turno" bodyClass="p-2 pt-3">
          {model.stageRows.length > 0 ? (
            <ResponsiveContainer width="100%" height={230}>
              <BarChart data={model.stageRows} layout="vertical" margin={{ top: 0, right: 16, left: 4, bottom: 0 }} barGap={2}>
                <CartesianGrid stroke="#232a38" horizontal={false} />
                <XAxis type="number" {...AXIS} tickFormatter={fmtAxisMs} />
                <YAxis type="category" dataKey="stage" {...AXIS} width={118} />
                <Tooltip cursor={{ fill: 'rgba(255,255,255,.04)' }} content={<ChartTooltip valueFormatter={(v) => fmtMs(Number(v))} />} />
                <Bar isAnimationActive={false} dataKey="avg" name="Media" fill="#6c8cff" radius={[0, 3, 3, 0]} barSize={8} />
                <Bar isAnimationActive={false} dataKey="p95" name="p95" fill="#e5a33b" fillOpacity={0.7} radius={[0, 3, 3, 0]} barSize={8} />
              </BarChart>
            </ResponsiveContainer>
          ) : <EmptyState title="Sin tiempos por etapa" />}
        </Card>

        <Card title="Modelos" subtitle="Quién respondió cada turno" className="lg:col-span-2 2xl:col-span-1">
          {modelsPie.length > 0 ? (
            <div className="flex items-center gap-4">
              <div className="size-[150px] shrink-0">
                <ResponsiveContainer width="100%" height="100%">
                  <PieChart>
                    <Pie isAnimationActive={false} data={modelsPie} dataKey="count" nameKey="key" innerRadius={46} outerRadius={70} paddingAngle={2} stroke="none">
                      {modelsPie.map((m, i) => <Cell key={m.key} fill={PALETTE[i % PALETTE.length]} />)}
                    </Pie>
                    <Tooltip content={<ChartTooltip valueFormatter={(v) => `${v} turnos`} />} />
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
          ) : <EmptyState title="Sin datos de modelos" />}
          {langs.length > 0 && (
            <div className="mt-4 flex flex-wrap items-center gap-1.5 border-t border-line pt-3 text-xs text-faint">
              Idiomas:
              {langs.map((l) => (
                <span key={l.key} className="rounded-md bg-raised px-1.5 py-0.5 text-dim">{l.key} · {fmtPct(l.rate, 0)}</span>
              ))}
            </div>
          )}
        </Card>
      </div>

      {/* Lists */}
      <div className="grid gap-4 xl:grid-cols-[minmax(0,1.6fr)_minmax(0,1fr)]">
        <Card title="Turnos recientes" subtitle="Se actualiza en vivo"
          actions={<Link to="/traces" className="text-xs text-accent hover:underline">Ver todas</Link>} bodyClass="p-0 pt-2">
          {hasData ? (
            <ul className="divide-y divide-line">
              {windowTraces.slice(0, 10).map((t) => {
                const d = traceDurationMs(t);
                return (
                  <li key={t.id} className={newIds.has(t.id) ? 'flash-row' : undefined}>
                    <Link to={`/traces/${t.id}`} className="flex items-center gap-3 px-4 py-2 text-xs hover:bg-panel-2">
                      <StatusDot status={t.status} />
                      <span className="w-24 shrink-0 font-mono text-dim">{shortId(t.turn_id, 7)}</span>
                      <span className="hidden w-20 shrink-0 text-faint sm:inline">{t.environment ?? '–'}</span>
                      <span className="flex-1" />
                      <span className="w-16 text-right font-medium text-ink">{fmtMs(d)}</span>
                      <span className="w-20 text-right text-faint">{timeAgo(t.created_at)}</span>
                    </Link>
                  </li>
                );
              })}
            </ul>
          ) : <EmptyState title="Sin turnos recientes" />}
        </Card>

        <Card title="Turnos más lentos" subtitle="Mayor latencia en el rango" bodyClass="p-0 pt-2">
          {model.slowest.length > 0 ? (
            <ul className="divide-y divide-line">
              {model.slowest.map(({ t, d }) => {
                const pct = model.durations.length ? d / model.durations[model.durations.length - 1] : 0;
                return (
                  <li key={t.id}>
                    <Link to={`/traces/${t.id}`} className="block px-4 py-2 hover:bg-panel-2">
                      <div className="flex items-center justify-between text-xs">
                        <span className="font-mono text-dim">{shortId(t.turn_id, 7)}</span>
                        <span className="font-medium text-ink">{fmtMs(d)}</span>
                      </div>
                      <div className="mt-1.5"><Meter rate={pct} tone={t.status === 'error' ? 'bad' : 'warn'} /></div>
                    </Link>
                  </li>
                );
              })}
            </ul>
          ) : <EmptyState title="Sin datos" />}
        </Card>
      </div>

      {summary && summary.total_cost_usd > 0 && (
        <p className="text-xs text-faint">Costo total del rango: {fmtUsd(summary.total_cost_usd)}</p>
      )}
    </div>
  );
}

export default Overview;
