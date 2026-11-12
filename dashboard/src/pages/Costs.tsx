import { useCallback, useEffect, useState } from 'react';
import { api } from '../api';
import { useFilters } from '../context';
import type { CostSummary } from '../types';
import { Card, EmptyState, ErrorState, Kpi, Skeleton } from '../components/ui';
import { fmtInt, fmtPct, fmtUsd } from '../helpers/format';
import { OUTCOME_LABEL } from '../helpers/outcomes';
import { t } from '../i18n';

const BUCKET_LABEL: Record<string, string> = {
  useful: 'Útil', failed: 'Turnos que no respondieron', error: 'Llamadas con error',
  repeated: 'Llamadas repetidas', unknown: 'Resultado desconocido',
};
const PRICING_LABEL: Record<string, string> = {
  priced: 'Con precio', free: 'Gratis', local: 'Local', unpriced: 'Sin precio',
};

/** Cost per outcome (roadmap step 13a): what the spend bought, not only how much was spent. */
function Costs() {
  const { hours, project } = useFilters();
  const [data, setData] = useState<CostSummary | null>(null);
  const [error, setError] = useState(false);

  const load = useCallback(() => {
    api.getCostSummary(hours, project || undefined)
      .then((d) => { setData(d); setError(false); })
      .catch(() => setError(true));
  }, [hours, project]);
  useEffect(() => { load(); }, [load]);

  if (error && !data) return <ErrorState message={t('No se pudo cargar')} onRetry={load} />;
  if (!data) return <Skeleton className="h-64" />;
  const total = data.total_usd;

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-lg font-semibold tracking-tight">{t('Costo por resultado')}</h1>
        <p className="text-xs text-faint">{t('Cada llamada con costo cae en un solo grupo: útil, turnos que no respondieron, llamadas con error, llamadas repetidas o resultado desconocido. Los grupos suman el total.')}</p>
      </div>
      {data.costed_spans === 0 ? <EmptyState title={t('No hay llamadas con costo en esta ventana')} /> : (<>
        <dl className="grid grid-cols-2 gap-3 md:grid-cols-4">
          <Kpi label={t('Gasto total')} value={fmtUsd(total)} hint={t('{n} trazas', { n: fmtInt(data.traces) })} />
          <Kpi label={t('Costo por turno respondido')} value={fmtUsd(data.cost_per_answered)}
            hint={t('{n} respondidos', { n: fmtInt(data.answered_turns) })} />
          <Kpi label={t('Gasto desperdiciado')} value={fmtPct(data.waste_share, 0)}
            tone={(data.waste_share ?? 0) >= 0.25 ? 'warn' : 'neutral'} />
          <Kpi label={t('Top 5 % de usuarios')} value={fmtPct(data.concentration.top5_share, 0)}
            hint={t('{n} de {m} usuarios', { n: fmtInt(data.concentration.top_users), m: fmtInt(data.concentration.users) })} />
        </dl>
        <Card title={t('A dónde fue el gasto')} bodyClass="p-4">
          <ul className="space-y-2 text-xs">
            {Object.entries(data.buckets).map(([k, v]) => (
              <li key={k} className="grid grid-cols-[1fr_auto] items-center gap-x-3 gap-y-1 sm:grid-cols-[14rem_1fr_6rem]">
                <span className="text-dim">{t(BUCKET_LABEL[k] ?? k)}</span>
                <span className="col-span-2 row-start-2 h-2 overflow-hidden rounded bg-raised sm:col-span-1 sm:row-start-auto">
                  <span className={`block h-full ${k === 'useful' ? 'bg-ok' : k === 'unknown' ? 'bg-line-strong' : 'bg-warn'}`}
                    style={{ width: `${total > 0 ? (v / total) * 100 : 0}%` }} />
                </span>
                <span className="text-right text-ink sm:col-start-3 sm:row-start-1">{fmtUsd(v)}</span>
              </li>
            ))}
          </ul>
          {data.concentration.unattributed_usd > 0 && (
            <p className="mt-3 text-xs text-faint">{t('{u} sin usuario identificado (sin user.id ni actor).', { u: fmtUsd(data.concentration.unattributed_usd) })}</p>
          )}
          {data.truncated && <p className="mt-1 text-xs text-warn">{t('Ventana recortada a las trazas más recientes.')}</p>}
        </Card>
        <Card title={t('Por modelo')} bodyClass="p-0">
          <div className="overflow-x-auto">
            <table className="w-full min-w-[520px] text-left text-xs">
              <thead>
                <tr className="border-b border-line text-[11px] uppercase tracking-wider text-faint">
                  <th className="px-4 py-2.5 font-medium">{t('Modelo')}</th>
                  <th className="px-3 py-2.5 font-medium">{t('Precio')}</th>
                  <th className="px-3 py-2.5 text-right font-medium">{t('Gasto')}</th>
                  <th className="px-3 py-2.5 text-right font-medium">{t('Llamadas')}</th>
                  <th className="px-4 py-2.5 font-medium">{t('Resultados')}</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-line">
                {data.routing.map((m) => (
                  <tr key={m.model}>
                    <td className="px-4 py-2 font-mono text-ink">{m.model}</td>
                    <td className="px-3 py-2 text-dim">{t(PRICING_LABEL[m.pricing] ?? m.pricing)}</td>
                    <td className="px-3 py-2 text-right">{fmtUsd(m.cost_usd)}</td>
                    <td className="px-3 py-2 text-right">{fmtInt(m.spans)}</td>
                    <td className="px-4 py-2 text-dim">{Object.entries(m.outcomes).map(([o, n]) => `${t(OUTCOME_LABEL[o] ?? o)} ${n}`).join(' · ')}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="px-4 py-2 text-xs text-faint">{t('Un modelo local cuesta 0 aquí: el costo por hora de la capacidad propia aún no está construido.')}</p>
        </Card>
      </>)}
    </div>
  );
}

export default Costs;
