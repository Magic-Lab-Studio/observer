import { useState, useEffect, useCallback, useRef } from 'react';
import { Link } from 'react-router-dom';
import { api } from '../api';
import { useFilters } from '../context';
import type { Trace } from '../types';
import { Chip, EmptyState, ErrorState, Icon, Segmented, Skeleton, StatusDot } from '../components/ui';
import { fmtInt, fmtMs, shortId, stageLabel, stageTimings, timeAgo, traceDurationMs } from '../helpers/format';
import { t } from '../i18n';

const LIMIT = 25;
const inputCls = 'rounded-lg border border-line bg-panel px-3 py-1.5 text-xs text-ink placeholder:text-faint focus:border-accent outline-none';

function topStage(t: Trace): { name: string; ms: number } | null {
  const entries = Object.entries(stageTimings(t));
  if (!entries.length) return null;
  const [k, v] = entries.reduce((a, b) => (b[1] > a[1] ? b : a));
  return { name: stageLabel(k), ms: v };
}

function Traces() {
  const { environments, newIds } = useFilters();
  const [traces, setTraces] = useState<Trace[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [page, setPage] = useState(0);
  const [total, setTotal] = useState(0);
  const [search, setSearch] = useState('');
  const [debouncedSearch, setDebouncedSearch] = useState('');
  const [status, setStatus] = useState('');
  const [envFilter, setEnvFilter] = useState('');
  const [sessionFilter, setSessionFilter] = useState('');
  const [projectFilter, setProjectFilter] = useState('');

  useEffect(() => {
    const timer = setTimeout(() => setDebouncedSearch(search), 300);
    return () => clearTimeout(timer);
  }, [search]);

  const fetchTraces = useCallback(async (offset: number) => {
    setLoading(true);
    try {
      const data = await api.listTraces({
        limit: LIMIT, offset,
        status: status || undefined,
        search: debouncedSearch || undefined,
        project_id: projectFilter.trim() || undefined,
        environment: envFilter || undefined,
        session_id: sessionFilter.trim() || undefined,
      });
      setTraces(data.traces);
      setTotal(data.total);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : t('Error desconocido'));
    } finally {
      setLoading(false);
    }
  }, [status, debouncedSearch, projectFilter, envFilter, sessionFilter]);

  useEffect(() => { setPage(0); void fetchTraces(0); }, [fetchTraces]);

  const prevPage = useRef(page);
  useEffect(() => {
    if (prevPage.current !== page) { prevPage.current = page; void fetchTraces(page * LIMIT); }
  }, [page, fetchTraces]);

  const maxDur = Math.max(1, ...traces.map((t) => traceDurationMs(t) ?? 0));
  const pages = Math.max(1, Math.ceil(total / LIMIT));

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-2">
        <div>
          <h1 className="text-lg font-semibold tracking-tight">{t("Trazas")}</h1>
          <p className="text-xs text-faint">{fmtInt(total)}  {t("resultados")}</p>
        </div>
      </div>

      <div className="sticky top-14 z-10 -mx-4 flex flex-wrap items-center gap-2 border-b border-line bg-canvas/90 px-4 py-3 backdrop-blur lg:-mx-6 lg:px-6">
        <div className="relative min-w-[220px] flex-1 max-w-md">
          <Icon name="search" className="pointer-events-none absolute left-2.5 top-1/2 size-3.5 -translate-y-1/2 text-faint" />
          <input type="text" aria-label={t("Buscar trazas")} placeholder={t("Buscar por nombre…")} value={search}
            onChange={(e) => setSearch(e.target.value)} className={`${inputCls} w-full pl-8`} />
        </div>
        <Segmented label={t("Estado")} value={status} onChange={setStatus}
          options={[{ label: t('Todas'), value: '' }, { label: 'OK', value: 'ok' }, { label: t('Error'), value: 'error' }]} />
        <select aria-label={t("Entorno")} value={envFilter} onChange={(e) => setEnvFilter(e.target.value)} className={inputCls}>
          <option value="">{t("Todos los entornos")}</option>
          {environments.map((e) => <option key={e} value={e}>{e}</option>)}
        </select>
        <input type="text" aria-label={t("Project filter")} placeholder={t("Proyecto")} value={projectFilter}
          onChange={(e) => setProjectFilter(e.target.value)} className={`${inputCls} w-32`} />
        <input type="text" aria-label={t("Session filter")} placeholder={t("ID de sesión")} value={sessionFilter}
          onChange={(e) => setSessionFilter(e.target.value)} className={`${inputCls} w-44 font-mono`} />
      </div>

      {error ? <ErrorState message={error} onRetry={() => void fetchTraces(page * LIMIT)} /> : (
        <div className="overflow-x-auto rounded-xl border border-line bg-panel">
          <table className="w-full min-w-[860px] text-left text-xs">
            <thead>
              <tr className="border-b border-line text-[11px] uppercase tracking-wider text-faint">
                <th className="w-8 py-2.5 pl-4 font-medium"><span className="sr-only">{t("Estado")}</span></th>
                <th className="px-3 py-2.5 font-medium">{t("Traza")}</th>
                <th className="px-3 py-2.5 font-medium">{t("Entorno")}</th>
                <th className="px-3 py-2.5 font-medium">{t("Sesión")}</th>
                <th className="px-3 py-2.5 font-medium w-[26%]">{t("Duración")}</th>
                <th className="px-3 py-2.5 font-medium">{t("Etapa más lenta")}</th>
                <th className="px-4 py-2.5 text-right font-medium">{t("Creada")}</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-line">
              {loading && traces.length === 0 ? (
                Array.from({ length: 8 }, (_, i) => (
                  <tr key={i}><td colSpan={7} className="px-4 py-2"><Skeleton className="h-6" /></td></tr>
                ))
              ) : traces.length === 0 ? (
                <tr><td colSpan={7}><EmptyState title={t("No hay trazas con estos filtros")} /></td></tr>
              ) : traces.map((t) => {
                const d = traceDurationMs(t);
                const ts = topStage(t);
                const slow = d != null && d > 20000;
                return (
                  <tr key={t.id} className={`group hover:bg-panel-2 ${newIds.has(t.id) ? 'flash-row' : ''}`}>
                    <td className="py-2.5 pl-4"><StatusDot status={t.status} /></td>
                    <td className="px-3 py-2.5">
                      <Link to={`/traces/${t.id}`} className="font-medium text-ink hover:text-accent">{t.name}</Link>
                      <div className="font-mono text-[11px] text-faint">{shortId(t.turn_id, 12)}</div>
                    </td>
                    <td className="px-3 py-2.5">{t.environment ? <Chip>{t.environment}</Chip> : <span className="text-faint">–</span>}</td>
                    <td className="max-w-[180px] truncate px-3 py-2.5 font-mono text-dim" title={t.session_id ?? undefined}>{(t.session_id ?? '–').replace(/^session_\d{8}_/, '')}</td>
                    <td className="px-3 py-2.5">
                      <div className="flex items-center gap-2.5">
                        <span className={`w-20 shrink-0 whitespace-nowrap text-right font-medium ${slow ? 'text-warn' : 'text-ink'}`}>{fmtMs(d)}</span>
                        <div className="h-1.5 flex-1 rounded-full bg-raised">
                          <div className={`h-full rounded-full ${t.status === 'error' ? 'bg-bad' : slow ? 'bg-warn' : 'bg-accent'}`}
                            style={{ width: `${Math.max(2, ((d ?? 0) / maxDur) * 100)}%` }} />
                        </div>
                      </div>
                    </td>
                    <td className="px-3 py-2.5 text-dim">{ts ? <>{ts.name} <span className="text-faint">· {fmtMs(ts.ms)}</span></> : '–'}</td>
                    <td className="px-4 py-2.5 text-right text-faint whitespace-nowrap">{timeAgo(t.created_at)}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      <div className="flex items-center justify-between text-xs text-faint">
        <span>{t("Página {p} de {n}", { p: page + 1, n: pages })}</span>
        <div className="flex gap-2">
          <button type="button" onClick={() => setPage((p) => Math.max(0, p - 1))} disabled={page === 0}
            className="rounded-lg border border-line bg-panel px-3 py-1.5 text-ink hover:bg-raised disabled:opacity-40 disabled:hover:bg-panel">{t("Anterior")}</button>
          <button type="button" onClick={() => setPage((p) => p + 1)} disabled={page + 1 >= pages}
            className="rounded-lg border border-line bg-panel px-3 py-1.5 text-ink hover:bg-raised disabled:opacity-40 disabled:hover:bg-panel">{t("Siguiente")}</button>
        </div>
      </div>
    </div>
  );
}

export default Traces;
