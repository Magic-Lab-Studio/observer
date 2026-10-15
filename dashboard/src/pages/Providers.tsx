import { Fragment, useCallback, useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import { api } from '../api';
import { useFilters } from '../context';
import type { ProviderInfo, ProviderStats, ProvidersResponse } from '../types';
import { Card, ChartTooltip, Chip, EmptyState, ErrorState, Kpi, Meter, Skeleton } from '../components/ui';
import { fmtAxisMs, fmtInt, fmtMs, fmtPct, timeAgo } from '../helpers/format';

const AXIS = { stroke: 'transparent', tickLine: false, axisLine: false } as const;

function waitText(sec: number): string {
  if (sec < 90) return `${sec} s`;
  const m = Math.round(sec / 60);
  return m < 90 ? `${m} min` : `${Math.round(m / 60)} h`;
}

function stateOf(p: ProviderInfo): { label: string; tone: 'ok' | 'warn' | 'bad' | 'off' } {
  if (p.kind === 'local') return { label: 'Local', tone: 'ok' };
  if (!p.has_key) return { label: 'Sin clave', tone: 'off' };
  if (!p.active) return { label: 'Inactivo', tone: 'off' };
  if (p.out_of_quota.length > 0) {
    const soon = Math.min(...p.out_of_quota.map((q) => q.returns_in_sec));
    return { label: `Agotado · vuelve en ${waitText(soon)}`, tone: 'warn' };
  }
  return { label: 'Activo', tone: 'ok' };
}

const TONE: Record<string, string> = {
  ok: 'border-ok/30 bg-ok/10 text-ok', warn: 'border-warn/30 bg-warn/10 text-warn',
  bad: 'border-bad/30 bg-bad/10 text-bad', off: 'border-line-strong bg-raised text-faint',
};

function Providers() {
  const { hours, env } = useFilters();
  const [data, setData] = useState<ProvidersResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState<string | null>(null);

  const load = useCallback(() => {
    api.getProviders(hours, env || undefined).then((d) => { setData(d); setError(null); })
      .catch((e) => setError(e instanceof Error ? e.message : 'Error desconocido'));
  }, [hours, env]);

  useEffect(() => {
    load();
    const id = setInterval(load, 30_000);
    return () => clearInterval(id);
  }, [load]);

  if (error && !data) return <ErrorState message={error} onRetry={load} />;
  if (!data) return <div className="space-y-4"><Skeleton className="h-28" /><Skeleton className="h-80" /></div>;

  const stats = new Map<string, ProviderStats>(data.providers.map((p) => [p.id, p]));
  const snap = data.snapshot;
  const chain = snap?.providers ?? [];
  // Providers seen in spans but missing from the snapshot (older data, renamed ids) still get a row.
  const known = new Set(chain.map((c) => c.id));
  const extra = data.providers.filter((p) => !known.has(p.id));
  const dormant = chain.filter((c) => c.kind === 'cloud' && c.active && (stats.get(c.id)?.attempts ?? 0) === 0);
  const noKey = chain.filter((c) => c.kind === 'cloud' && !c.has_key);
  const latency = data.providers.filter((p) => p.p50_ms != null).map((p) => ({ id: p.id, p50: p.p50_ms, p95: p.p95_ms }));
  const failing = data.providers.filter((p) => p.rate_limited + p.timeout + p.error > 0);
  const totalFailed = failing.reduce((a, p) => a + p.rate_limited + p.timeout + p.error, 0);
  const depthTotal = data.fallback_depth.reduce((a, d) => a + d.count, 0) || 1;
  const fellBack = data.fallback_depth.filter((d) => d.depth > 0).reduce((a, d) => a + d.count, 0);

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-2">
        <div>
          <h1 className="text-lg font-semibold tracking-tight">Proveedores de LLM</h1>
          <p className="text-xs text-faint">
            Cadena de respaldo en el orden en que se intenta · {fmtInt(data.turns)} turnos en el rango
            {snap && ` · configuración de hace ${waitText(snap.age_sec)}`}
          </p>
        </div>
      </div>

      {!snap && (
        <div className="rounded-xl border border-warn/30 bg-warn/5 p-3 text-xs text-warn">
          Manitos aún no ha enviado su configuración de proveedores. Se envía al arrancar y cada minuto
          (requiere <code className="font-mono">MANITOS_OBSERVER_ENABLED=1</code>); mientras tanto solo se ven los datos de las trazas.
        </div>
      )}
      {snap?.stale && (
        <div className="rounded-xl border border-warn/30 bg-warn/5 p-3 text-xs text-warn">
          La configuración tiene {waitText(snap.age_sec)}: Manitos dejó de enviarla (¿servidor apagado?). Los estados pueden no ser actuales.
        </div>
      )}

      {chain.length > 0 && (
        <Card title="Cadena de respaldo" subtitle="Cada turno intenta el primero; si falla o no tiene cuota, pasa al siguiente">
          <ol className="flex flex-wrap items-stretch gap-2">
            {chain.map((p, i) => {
              const st = stateOf(p);
              const s = stats.get(p.id);
              return (
                <Fragment key={p.id}>
                  <li className="min-w-[150px] flex-1 rounded-lg border border-line bg-panel-2 p-3 sm:max-w-[220px]">
                    <div className="flex items-center justify-between gap-2">
                      <span className="text-[11px] text-faint">#{i + 1}</span>
                      <span className={`rounded-full border px-2 py-0.5 text-[10px] font-medium ${TONE[st.tone]}`}>{st.label}</span>
                    </div>
                    <div className="mt-1.5 truncate text-sm font-semibold" title={p.label}>{p.label}</div>
                    <div className="mt-0.5 text-[11px] text-faint">{p.models.length} modelo{p.models.length === 1 ? '' : 's'}</div>
                    <div className="mt-2 flex items-baseline justify-between text-xs">
                      <span className="text-dim">Respondió</span>
                      <span className="font-medium text-ink">{s ? fmtPct(s.answered_share, 0) : '0%'}</span>
                    </div>
                    <div className="mt-1"><Meter rate={s?.answered_share ?? 0} /></div>
                  </li>
                  {i < chain.length - 1 && <li aria-hidden="true" className="hidden self-center text-faint sm:block">›</li>}
                </Fragment>
              );
            })}
          </ol>
        </Card>
      )}

      <dl className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <Kpi label="Turnos con cadena" value={fmtInt(data.turns)} hint="con al menos un intento registrado" />
        <Kpi label="Cayeron a respaldo" value={fmtPct(fellBack / depthTotal)} hint={`${fmtInt(fellBack)} turnos`}
          tone={fellBack / depthTotal > 0.25 ? 'warn' : 'neutral'} />
        <Kpi label="Llamadas fallidas" value={fmtInt(totalFailed)} hint="429, tiempo agotado o error"
          tone={totalFailed > 0 ? 'warn' : 'good'} />
        <Kpi label="Proveedores sin uso" value={fmtInt(dormant.length)}
          hint={dormant.length ? dormant.map((d) => d.label).join(', ') : 'todos han intervenido'} tone={dormant.length ? 'warn' : 'good'} />
      </dl>

      {(dormant.length > 0 || noKey.length > 0) && (
        <Card title="Hallazgos">
          <ul className="space-y-2 text-xs text-dim">
            {dormant.length > 0 && (
              <li><Chip tone="warn">sin uso</Chip>{' '}
                <span className="text-ink">{dormant.map((d) => d.label).join(', ')}</span>: activos, pero sin ninguna llamada en este rango.
                Solo entran cuando los anteriores fallan o agotan su cuota, así que un fallo suyo no se habría notado todavía.</li>
            )}
            {noKey.map((d) => (
              <li key={d.id}><Chip>sin clave</Chip>{' '}<span className="text-ink">{d.label}</span> no tiene clave en <code className="font-mono">.env</code>; la cadena lo salta.</li>
            ))}
          </ul>
        </Card>
      )}

      <Card title="Detalle por proveedor" subtitle="Intentos del rango; latencia = tiempo de la llamada que respondió" bodyClass="p-0 pt-2">
        <div className="overflow-x-auto">
          <table className="w-full min-w-[900px] text-left text-xs">
            <thead>
              <tr className="border-b border-line text-[11px] uppercase tracking-wider text-faint">
                <th className="px-4 py-2.5 font-medium">Proveedor</th>
                <th className="px-3 py-2.5 font-medium">Estado</th>
                <th className="px-3 py-2.5 text-right font-medium">Intentos</th>
                <th className="px-3 py-2.5 text-right font-medium">Éxito</th>
                <th className="px-3 py-2.5 text-right font-medium">429</th>
                <th className="px-3 py-2.5 text-right font-medium">Timeout</th>
                <th className="px-3 py-2.5 text-right font-medium">Error</th>
                <th className="px-3 py-2.5 text-right font-medium">p50 / p95</th>
                <th className="px-3 py-2.5 font-medium w-[14%]">Respondió</th>
                <th className="px-4 py-2.5 text-right font-medium">Última vez</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-line">
              {[...chain.map((c) => ({ info: c as ProviderInfo | null, id: c.id, label: c.label })),
                ...extra.map((e) => ({ info: null, id: e.id, label: e.id }))].map(({ info, id, label }) => {
                const s = stats.get(id);
                const st = info ? stateOf(info) : { label: '—', tone: 'off' as const };
                const isOpen = open === id;
                return (
                  <Fragment key={id}>
                    <tr className="cursor-pointer hover:bg-panel-2" onClick={() => setOpen(isOpen ? null : id)}>
                      <td className="px-4 py-2.5 font-medium text-ink">{label}</td>
                      <td className="px-3 py-2.5"><span className={`rounded-full border px-2 py-0.5 text-[10px] font-medium ${TONE[st.tone]}`}>{st.label}</span></td>
                      <td className="px-3 py-2.5 text-right">{fmtInt(s?.attempts ?? 0)}</td>
                      <td className={`px-3 py-2.5 text-right ${s?.success_rate != null && s.success_rate < 0.8 ? 'text-warn' : ''}`}>{fmtPct(s?.success_rate, 0)}</td>
                      <td className="px-3 py-2.5 text-right">{s?.rate_limited || '–'}</td>
                      <td className="px-3 py-2.5 text-right">{s?.timeout || '–'}</td>
                      <td className="px-3 py-2.5 text-right">{s?.error || '–'}</td>
                      <td className="px-3 py-2.5 text-right text-dim">{s?.p50_ms != null ? `${fmtMs(s.p50_ms)} / ${fmtMs(s.p95_ms)}` : '–'}</td>
                      <td className="px-3 py-2.5">
                        <div className="flex items-center gap-2"><span className="w-9 text-ink">{fmtPct(s?.answered_share ?? 0, 0)}</span><Meter rate={s?.answered_share ?? 0} /></div>
                      </td>
                      <td className="px-4 py-2.5 text-right text-faint whitespace-nowrap">{s?.last_seen ? timeAgo(s.last_seen) : 'nunca'}</td>
                    </tr>
                    {isOpen && (
                      <tr className="bg-panel-2">
                        <td colSpan={10} className="px-4 py-3">
                          <div className="grid gap-4 md:grid-cols-2">
                            <div>
                              <h4 className="mb-1.5 text-[11px] font-semibold uppercase tracking-wider text-faint">Modelos</h4>
                              <ul className="space-y-1">
                                {(info?.models ?? []).map((m) => {
                                  const used = s?.models.find((x) => x.model === m.model);
                                  return (
                                    <li key={m.model || m.label} className="flex items-center gap-2 text-xs">
                                      <span className="min-w-0 flex-1 truncate font-mono text-dim" title={m.label}>{m.model || m.label}</span>
                                      {m.fallback === false && <Chip>solo manual</Chip>}
                                      <span className="shrink-0 text-faint">{used ? `${used.attempts} int. · ${used.ok} ok${used.p50_ms != null ? ` · ${fmtMs(used.p50_ms)}` : ''}` : 'sin uso'}</span>
                                    </li>
                                  );
                                })}
                                {!info && (s?.models ?? []).map((m) => (
                                  <li key={m.model} className="font-mono text-xs text-dim">{m.model || '–'} · {m.attempts} int.</li>
                                ))}
                              </ul>
                            </div>
                            <div>
                              <h4 className="mb-1.5 text-[11px] font-semibold uppercase tracking-wider text-faint">Términos de datos y límites</h4>
                              <p className="text-xs leading-relaxed text-dim">{info?.data_note || (info?.kind === 'local' ? 'Corre en esta máquina: nada sale de ella.' : 'Sin nota registrada.')}</p>
                              {info && info.out_of_quota.length > 0 && (
                                <ul className="mt-2 space-y-0.5 text-xs text-warn">
                                  {info.out_of_quota.map((q) => (
                                    <li key={q.model}>{q.model || 'todos los modelos'}: sin cuota ({q.kind}), vuelve en {waitText(q.returns_in_sec)}</li>
                                  ))}
                                </ul>
                              )}
                            </div>
                          </div>
                        </td>
                      </tr>
                    )}
                  </Fragment>
                );
              })}
            </tbody>
          </table>
        </div>
        {chain.length === 0 && extra.length === 0 && <EmptyState title="Sin datos de cadena en este rango" hint="Prueba un rango más amplio." />}
      </Card>

      <div className="grid gap-4 lg:grid-cols-2 xl:grid-cols-3">
        <Card title="Fallos antes de responder" subtitle="0 = el primero respondió" bodyClass="p-2 pt-3">
          {depthTotal > 1 || data.fallback_depth[0]?.count ? (
            <ResponsiveContainer width="100%" height={210}>
              <BarChart data={data.fallback_depth.map((d) => ({ n: d.depth === 5 ? '5+' : String(d.depth), count: d.count }))} margin={{ top: 4, right: 12, left: 0, bottom: 0 }}>
                <CartesianGrid stroke="#232a38" vertical={false} />
                <XAxis dataKey="n" {...AXIS} />
                <YAxis {...AXIS} width={34} allowDecimals={false} />
                <Tooltip cursor={{ fill: 'rgba(255,255,255,.04)' }} content={<ChartTooltip valueFormatter={(v) => `${v} turnos`} />} />
                <Bar isAnimationActive={false} dataKey="count" name="Turnos" fill="#6c8cff" radius={[3, 3, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          ) : <EmptyState title="Sin datos" />}
        </Card>

        <Card title="Latencia por proveedor" subtitle="Llamadas que respondieron (p50 y p95)" bodyClass="p-2 pt-3">
          {latency.length > 0 ? (
            <ResponsiveContainer width="100%" height={210}>
              <BarChart data={latency} margin={{ top: 4, right: 12, left: 0, bottom: 0 }}>
                <CartesianGrid stroke="#232a38" vertical={false} />
                <XAxis dataKey="id" {...AXIS} />
                <YAxis {...AXIS} width={44} tickFormatter={fmtAxisMs} />
                <Tooltip cursor={{ fill: 'rgba(255,255,255,.04)' }} content={<ChartTooltip valueFormatter={(v) => fmtMs(Number(v))} />} />
                <Bar isAnimationActive={false} dataKey="p50" name="p50" fill="#3fb68b" radius={[3, 3, 0, 0]} />
                <Bar isAnimationActive={false} dataKey="p95" name="p95" fill="#e5a33b" fillOpacity={0.75} radius={[3, 3, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          ) : <EmptyState title="Aún sin latencias por llamada" hint="Se registran en los turnos nuevos." />}
        </Card>

        <Card title="Rutas más frecuentes" subtitle="Orden real en que se probaron los proveedores" className="lg:col-span-2 xl:col-span-1">
          {data.paths.length > 0 ? (
            <ul className="space-y-2.5">
              {data.paths.map((p) => (
                <li key={p.path}>
                  <div className="flex items-baseline justify-between gap-3 text-xs">
                    <span className="min-w-0 truncate font-mono text-dim" title={p.path}>{p.path}</span>
                    <span className="shrink-0 font-medium text-ink">{fmtInt(p.count)}</span>
                  </div>
                  <div className="mt-1"><Meter rate={p.count / (data.paths[0]?.count || 1)} /></div>
                </li>
              ))}
            </ul>
          ) : <EmptyState title="Sin rutas" />}
        </Card>
      </div>

      <p className="text-xs text-faint">
        ¿Un turno concreto? Abre su <Link to="/traces" className="text-accent hover:underline">traza</Link> y selecciona el span <span className="font-mono">llm.chain</span> para ver su cadena.
      </p>
    </div>
  );
}

export default Providers;
