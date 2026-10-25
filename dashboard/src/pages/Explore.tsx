import { useCallback, useEffect, useMemo, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import {
  Bar, BarChart, CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from 'recharts';
import { api } from '../api';
import { useFilters } from '../context';
import type { QueryResult, QuerySchema } from '../types';
import { Card, ChartTooltip, EmptyState, ErrorState, Kpi, PALETTE, Skeleton } from '../components/ui';
import {
  DEFAULT_EXPLORE, buildRequest, fillBuckets, formatMeasure, metricKey, parseSavedViews,
  pivotSeries, searchToState, stateToSearch,
} from '../helpers/query';
import type { ExploreState, SavedView } from '../helpers/query';
import { PROJECT_PARAM } from '../helpers/projects';
import { bucketLabel } from '../helpers/stats';
import { fmtInt } from '../helpers/format';
import { parseApiDate } from '../dates';
import { localeTag, t } from '../i18n';

const inputCls = 'rounded-lg border border-line bg-panel px-3 py-1.5 text-xs text-ink focus:border-accent outline-none';
const SAVED_KEY = 'observer.savedViews';
const AGG_LABEL: Record<string, string> = {
  count: 'Conteo', sum: 'Suma', avg: 'Promedio', min: 'Mínimo', max: 'Máximo', p50: 'p50', p95: 'p95', p99: 'p99',
};
const GRAN_LABEL: Record<string, string> = {
  '': 'Sin serie temporal', auto: 'Automática', minute: 'Minuto', hour: 'Hora', day: 'Día',
};

function readSaved(): SavedView[] {
  try {
    return parseSavedViews(localStorage.getItem(SAVED_KEY));
  } catch {
    return [];
  }
}

function writeSaved(views: SavedView[]): void {
  try {
    localStorage.setItem(SAVED_KEY, JSON.stringify(views));
  } catch {
    /* storage may be blocked: saved views just do not persist */
  }
}

function Explore() {
  const { hours, env, project } = useFilters();
  const [params, setRawParams] = useSearchParams();
  const state = useMemo(() => searchToState(params.toString()), [params]);
  // The query owns the URL except the global project selector's parameter, which is kept.
  const setParams = useCallback((search: string, opts: { replace: boolean }) => {
    const next = new URLSearchParams(search);
    const kept = params.get(PROJECT_PARAM);
    if (kept) next.set(PROJECT_PARAM, kept);
    setRawParams(next, opts);
  }, [params, setRawParams]);
  const [schema, setSchema] = useState<QuerySchema | null>(null);
  const [result, setResult] = useState<QueryResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [saved, setSaved] = useState<SavedView[]>(readSaved);
  const [name, setName] = useState('');

  useEffect(() => {
    api.getQuerySchema().then(setSchema).catch((e) => setError(e instanceof Error ? e.message : t('Error desconocido')));
  }, []);

  const view = schema?.views[state.view];

  // The URL is the state; a change of view drops a measure/dimension the new view does not have.
  const apply = useCallback((patch: Partial<ExploreState>) => {
    let next = { ...state, ...patch };
    const v = schema?.views[next.view];
    if (v) {
      if (next.measure !== 'count' && !v.measures.some((m) => m.id === next.measure)) next = { ...next, measure: 'count', aggregation: 'count' };
      if (next.dimension && !v.dimensions.some((d) => d.id === next.dimension)) next = { ...next, dimension: '' };
    }
    if (next.measure === 'count') next = { ...next, aggregation: 'count' };
    else if (next.aggregation === 'count' && patch.aggregation === undefined) next = { ...next, aggregation: 'avg' };
    setParams(stateToSearch(next), { replace: true });
  }, [state, schema, setParams]);

  useEffect(() => {
    if (!schema || !project) return;
    let live = true;
    setLoading(true);
    api.queryMetrics(buildRequest(state, { hours, environment: env || undefined, project }))
      .then((r) => { if (live) { setResult(r); setError(null); } })
      .catch((e) => { if (live) setError(e instanceof Error ? e.message : t('Error desconocido')); })
      .finally(() => { if (live) setLoading(false); });
    return () => { live = false; };
  }, [schema, state, hours, env, project]);

  const key = metricKey(state.measure, state.aggregation);
  const measureLabel = state.measure === 'count'
    ? t('Conteo de filas')
    : t(view?.measures.find((m) => m.id === state.measure)?.label ?? state.measure);
  const dimLabel = state.dimension ? t(view?.dimensions.find((d) => d.id === state.dimension)?.label ?? state.dimension) : '';
  const valueLabel = state.measure === 'count' ? measureLabel : `${t(AGG_LABEL[state.aggregation])} · ${measureLabel}`;
  const fmt = (v: unknown) => formatMeasure(state.measure, state.aggregation, typeof v === 'number' ? v : null);

  const chart = useMemo(() => {
    if (!result) return null;
    const rows = result.rows;
    if (result.granularity) {
      const start = parseApiDate(result.from).getTime();
      const end = parseApiDate(result.to).getTime();
      const span = end - start;
      const timed = rows.map((r) => ({
        t: parseApiDate(String(r.time)).getTime(), dim: state.dimension ? String(r[state.dimension] || '—') : valueLabel,
        value: typeof r[key] === 'number' ? (r[key] as number) : null,
      }));
      if (!state.dimension) {
        const filled = fillBuckets(timed, start, end, result.granularity).map((f) => ({
          t: f.t, label: bucketLabel(f.t, span), [valueLabel]: f.row ? f.row.value : null,
        }));
        return { kind: 'time' as const, data: filled, series: [valueLabel], xKey: 'label' };
      }
      const { data, series } = pivotSeries(timed);
      return {
        kind: 'time' as const, series, xKey: 'label',
        data: data.map((d) => ({ ...d, label: bucketLabel(d.t as number, span) })),
      };
    }
    const groups = rows.map((r) => ({
      label: state.dimension ? String(r[state.dimension] || '—') : valueLabel,
      value: typeof r[key] === 'number' ? (r[key] as number) : 0,
    })).sort((a, b) => b.value - a.value).slice(0, 20);
    return { kind: 'groups' as const, data: groups };
  }, [result, key, state.dimension, valueLabel]);

  const additive = state.aggregation === 'count' || state.aggregation === 'sum';

  const save = () => {
    const trimmed = name.trim();
    if (!trimmed) return;
    const next = [{ name: trimmed.slice(0, 60), state }, ...saved.filter((v) => v.name !== trimmed)].slice(0, 30);
    setSaved(next);
    writeSaved(next);
    setName('');
  };
  const remove = (n: string) => {
    const next = saved.filter((v) => v.name !== n);
    setSaved(next);
    writeSaved(next);
  };

  if (error && !schema) return <ErrorState message={error} />;
  if (!schema) return <div className="space-y-4"><Skeleton className="h-24" /><Skeleton className="h-80" /></div>;

  const sel = (label: string, value: string, onChange: (v: string) => void, options: { value: string; label: string }[]) => (
    <label className="text-xs text-dim">{label}
      <select className={`${inputCls} mt-1 w-full`} value={value} onChange={(e) => onChange(e.target.value)}>
        {options.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
      </select>
    </label>
  );

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-lg font-semibold tracking-tight">{t('Explorar')}</h1>
        <p className="text-xs text-faint">{t('Elige qué medir y cómo agrupar; la consulta queda en la URL para compartirla')}</p>
      </div>

      <Card>
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
          {sel(t('Vista'), state.view, (v) => apply({ view: v as ExploreState['view'] }),
            Object.entries(schema.views).map(([id, v]) => ({ value: id, label: t(v.label) })))}
          {sel(t('Medida'), state.measure, (v) => apply({ measure: v }),
            [{ value: 'count', label: t('Conteo de filas') }, ...(view?.measures ?? []).map((m) => ({ value: m.id, label: t(m.label) }))])}
          {sel(t('Agregación'), state.aggregation, (v) => apply({ aggregation: v }),
            (state.measure === 'count' ? ['count'] : schema.aggregations).map((a) => ({ value: a, label: t(AGG_LABEL[a] ?? a) })))}
          {sel(t('Agrupar por'), state.dimension, (v) => apply({ dimension: v }),
            [{ value: '', label: t('Sin agrupar') }, ...(view?.dimensions ?? []).map((d) => ({ value: d.id, label: t(d.label) }))])}
          {sel(t('Granularidad'), state.granularity, (v) => apply({ granularity: v }),
            ['', ...schema.granularities].map((g) => ({ value: g, label: t(GRAN_LABEL[g] ?? g) })))}
        </div>
        <div className="mt-4 flex flex-wrap items-center gap-2 border-t border-line pt-3">
          <input className={`${inputCls} w-52`} placeholder={t('Nombre de la vista')} aria-label={t('Nombre de la vista')} value={name}
            onChange={(e) => setName(e.target.value)} onKeyDown={(e) => { if (e.key === 'Enter') save(); }} />
          <button type="button" onClick={save} disabled={!name.trim()}
            className="rounded-lg border border-line bg-panel px-3 py-1.5 text-xs text-ink hover:bg-raised disabled:opacity-40">{t('Guardar vista')}</button>
          {saved.map((v) => (
            <span key={v.name} className="inline-flex items-center overflow-hidden rounded-lg border border-line bg-panel-2 text-xs">
              <button type="button" className="px-2.5 py-1 text-dim hover:text-ink"
                onClick={() => setParams(stateToSearch(v.state), { replace: true })}>{v.name}</button>
              <button type="button" aria-label={t('Quitar {x}', { x: v.name })} className="border-l border-line px-1.5 py-1 text-faint hover:text-bad"
                onClick={() => remove(v.name)}>×</button>
            </span>
          ))}
          <button type="button" className="ml-auto text-xs text-faint hover:text-ink"
            onClick={() => setParams(stateToSearch(DEFAULT_EXPLORE), { replace: true })}>{t('Restablecer')}</button>
        </div>
      </Card>

      {error && <ErrorState message={error} />}

      <Card title={valueLabel} subtitle={[
        dimLabel && t('por {d}', { d: dimLabel }),
        result?.granularity && t('cada {g}', { g: t(GRAN_LABEL[result.granularity] ?? result.granularity).toLowerCase() }),
        result && t('{n} filas analizadas', { n: fmtInt(result.samples) }),
      ].filter(Boolean).join(' · ')} bodyClass="p-2 pt-3">
        {loading && !result ? <Skeleton className="h-72" /> : !result || result.rows.length === 0 ? (
          <EmptyState title={t('Sin datos en este rango')} hint={t('Prueba otro rango o quita filtros.')} />
        ) : chart?.kind === 'time' ? (
          <ResponsiveContainer width="100%" height={320}>
            {additive ? (
              <BarChart data={chart.data} margin={{ top: 6, right: 12, left: 0, bottom: 0 }}>
                <CartesianGrid stroke="#232a38" vertical={false} />
                <XAxis dataKey="label" stroke="transparent" tickLine={false} minTickGap={36} />
                <YAxis stroke="transparent" tickLine={false} width={48} tickFormatter={fmt} />
                <Tooltip cursor={{ fill: 'rgba(255,255,255,.04)' }} content={<ChartTooltip valueFormatter={(v) => fmt(v)} />} />
                {chart.series.length > 1 && <Legend />}
                {chart.series.map((s, i) => (
                  <Bar key={s} isAnimationActive={false} dataKey={s} stackId="a" fill={PALETTE[i % PALETTE.length]} fillOpacity={0.85} />
                ))}
              </BarChart>
            ) : (
              <LineChart data={chart.data} margin={{ top: 6, right: 12, left: 0, bottom: 0 }}>
                <CartesianGrid stroke="#232a38" vertical={false} />
                <XAxis dataKey="label" stroke="transparent" tickLine={false} minTickGap={36} />
                <YAxis stroke="transparent" tickLine={false} width={56} tickFormatter={fmt} />
                <Tooltip content={<ChartTooltip valueFormatter={(v) => fmt(v)} />} />
                {chart.series.length > 1 && <Legend />}
                {chart.series.map((s, i) => (
                  <Line key={s} isAnimationActive={false} dataKey={s} stroke={PALETTE[i % PALETTE.length]} strokeWidth={2} dot={false} connectNulls />
                ))}
              </LineChart>
            )}
          </ResponsiveContainer>
        ) : chart?.kind === 'groups' && !state.dimension ? (
          <div className="px-4 pb-4 pt-2"><dl className="grid max-w-xs"><Kpi label={valueLabel} value={fmt(chart.data[0]?.value)} /></dl></div>
        ) : chart?.kind === 'groups' ? (
          <ResponsiveContainer width="100%" height={Math.max(180, chart.data.length * 30 + 30)}>
            <BarChart data={chart.data} layout="vertical" margin={{ top: 0, right: 16, left: 4, bottom: 0 }}>
              <CartesianGrid stroke="#232a38" horizontal={false} />
              <XAxis type="number" stroke="transparent" tickLine={false} tickFormatter={fmt} />
              <YAxis type="category" dataKey="label" stroke="transparent" tickLine={false} width={190} />
              <Tooltip cursor={{ fill: 'rgba(255,255,255,.04)' }} content={<ChartTooltip valueFormatter={(v) => fmt(v)} />} />
              <Bar isAnimationActive={false} dataKey="value" name={valueLabel} fill="#6c8cff" fillOpacity={0.85} radius={[0, 3, 3, 0]} barSize={14} />
            </BarChart>
          </ResponsiveContainer>
        ) : null}
      </Card>

      {result && result.rows.length > 0 && (
        <Card title={t('Datos')} subtitle={result.truncated ? t('La ventana superó el límite de filas: el resultado es parcial') : undefined} bodyClass="p-0 pt-2">
          <div className="max-h-96 overflow-auto">
            <table className="w-full min-w-[420px] text-left text-xs">
              <thead>
                <tr className="sticky top-0 border-b border-line bg-panel text-[11px] uppercase tracking-wider text-faint">
                  {result.granularity && <th className="px-4 py-2.5 font-medium">{t('Tiempo')}</th>}
                  {state.dimension && <th className="px-3 py-2.5 font-medium">{dimLabel}</th>}
                  <th className="px-4 py-2.5 text-right font-medium">{valueLabel}</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-line">
                {result.rows.slice(0, 500).map((r, i) => (
                  <tr key={i} className="hover:bg-panel-2">
                    {result.granularity && <td className="whitespace-nowrap px-4 py-2 text-dim">{parseApiDate(String(r.time)).toLocaleString(localeTag())}</td>}
                    {state.dimension && <td className="px-3 py-2 font-mono text-ink">{String(r[state.dimension] || '—')}</td>}
                    <td className="px-4 py-2 text-right font-medium text-ink">{fmt(r[key])}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      )}
    </div>
  );
}

export default Explore;
