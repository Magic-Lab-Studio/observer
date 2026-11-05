import { useEffect, useState } from 'react';
import { api } from '../api';
import { useFilters } from '../context';
import { OUTCOME_LABEL, outcomeShares, outcomeTone } from '../helpers/outcomes';
import { fmtInt } from '../helpers/format';
import { Card, Meter } from './ui';
import { t } from '../i18n';

const METER_TONE = { good: 'accent', warn: 'warn', bad: 'bad', neutral: 'muted' } as const;

/** How the turns of the selected project ended (agent contract observer.agent.v1). */
export default function OutcomeRates() {
  const { hours, env, project, totalTraces } = useFilters();
  const [rows, setRows] = useState<ReturnType<typeof outcomeShares> | null>(null);

  useEffect(() => {
    if (!project) return;
    let live = true;
    api.queryMetrics({
      view: 'turns', project_id: project, hours, dimensions: ['outcome'], row_limit: 20,
      metrics: [{ measure: 'count', aggregation: 'count' }],
      filters: env ? [{ field: 'environment', op: '=', value: env }] : [],
    }).then((r) => { if (live) setRows(outcomeShares(r.rows)); })
      .catch(() => { if (live) setRows(null); });
    return () => { live = false; };
  }, [project, hours, env, totalTraces]);

  if (!project || !rows || rows.length === 0) return null;
  const total = rows.reduce((sum, r) => sum + r.count, 0);
  return (
    <Card title={t('Cómo terminaron los turnos')}
      subtitle={`${t('proyecto «{p}»', { p: project })} · ${t('{n} turnos', { n: fmtInt(total) })}`}>
      <ul className="space-y-2">
        {rows.map((r) => (
          <li key={r.outcome} className="grid grid-cols-[9rem_1fr_3.5rem] items-center gap-3 text-xs">
            <span className="truncate text-dim">{t(OUTCOME_LABEL[r.outcome] ?? r.outcome)}</span>
            <Meter rate={r.share} tone={METER_TONE[outcomeTone(r.outcome)]} />
            <span className="text-right font-mono text-ink">{Math.round(r.share * 100)}%</span>
          </li>
        ))}
      </ul>
    </Card>
  );
}
