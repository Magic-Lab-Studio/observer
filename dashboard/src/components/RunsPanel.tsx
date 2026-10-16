import { useCallback, useEffect, useState } from 'react';
import { api } from '../api';
import type { CaseChange, DatasetCase, DatasetRunInfo, RunComparison } from '../types';
import { Card, Chip, EmptyState } from './ui';
import { changeRank, configDiff, fmtDelta, versionLabel } from '../helpers/runs';
import { fmtPct, timeAgo } from '../helpers/format';
import { t } from '../i18n';

const selectCls = 'rounded-lg border border-line bg-panel px-3 py-1.5 text-xs text-ink focus:border-accent outline-none';
const CHANGE_LABEL: Record<CaseChange, string> = {
  regressed: 'Empeoró', improved: 'Mejoró', same: 'Igual', unknown: 'Sin datos', missing: 'No está en ambas',
};
const CHANGE_TONE: Record<CaseChange, string> = {
  regressed: 'border-bad/40 bg-bad/10 text-bad', improved: 'border-ok/40 bg-ok/10 text-ok',
  same: 'border-line-strong bg-raised text-dim', unknown: 'border-warn/40 bg-warn/10 text-warn',
  missing: 'border-line-strong bg-raised text-faint',
};

/** Runs of one dataset and a case-by-case comparison of two of them. */
export default function RunsPanel({ datasetId, cases }: { datasetId: string; cases: DatasetCase[] }) {
  const [runs, setRuns] = useState<DatasetRunInfo[] | null>(null);
  const [base, setBase] = useState('');
  const [candidate, setCandidate] = useState('');
  const [diff, setDiff] = useState<RunComparison | null>(null);

  const load = useCallback(() => {
    api.listRuns(datasetId).then((r) => {
      setRuns(r);
      // newest = candidate, the one before it = baseline
      setCandidate(r[0]?.id ?? '');
      setBase(r[1]?.id ?? '');
    }).catch(() => setRuns([]));
  }, [datasetId]);
  useEffect(() => { setDiff(null); load(); }, [load]);

  useEffect(() => {
    if (base && candidate && base !== candidate) {
      api.compareRuns(datasetId, base, candidate).then(setDiff).catch(() => setDiff(null));
    } else setDiff(null);
  }, [datasetId, base, candidate]);

  const remove = async (id: string) => { await api.deleteRun(id); load(); };
  const label = (r: DatasetRunInfo) => versionLabel(r.name, r.version);
  const caseName = (id: string) => {
    const c = cases.find((x) => x.id === id);
    return c ? `${c.intent ?? t('caso')} · ${(c.source_trace_id ?? id).slice(0, 8)}` : id.slice(0, 8);
  };

  if (!runs) return null;
  return (
    <Card title={t('Ejecuciones')}
      subtitle={t('Cada ejecución verifica los mismos casos contra una versión del agente. Se registran con POST /v1/datasets/{id}/runs.')}>
      {runs.length === 0 ? <EmptyState title={t('Aún no hay ejecuciones')} /> : (
        <>
          <ul className="mb-3 divide-y divide-line text-xs">
            {runs.map((r) => (
              <li key={r.id} className="flex flex-wrap items-center gap-2 py-2">
                <span className="font-medium text-ink">{label(r)}</span>
                <Chip>{r.version.rubric ?? ''}</Chip>
                <span className="text-dim">
                  {t('{p} aprobados', { p: fmtPct(r.summary.pass_rate, 0) })} · {r.summary.pass}/{r.summary.fail}/{r.summary.unknown} {t('ok/falla/sin datos')}
                  {r.summary.avg_health != null && ` · ${t('salud')} ${r.summary.avg_health.toFixed(2)}`}
                </span>
                <span className="ml-auto text-faint">{r.created_at ? timeAgo(r.created_at) : ''}</span>
                <button type="button" onClick={() => void remove(r.id)} className="text-faint hover:text-bad">{t('Quitar')}</button>
              </li>
            ))}
          </ul>
          {runs.length >= 2 && (
            <div className="space-y-3">
              <div className="flex flex-wrap items-center gap-2 text-xs text-dim">
                <label htmlFor="run-base">{t('Base')}</label>
                <select id="run-base" className={selectCls} value={base} onChange={(e) => setBase(e.target.value)}>
                  {runs.map((r) => <option key={r.id} value={r.id}>{label(r)}</option>)}
                </select>
                <label htmlFor="run-cand">{t('Candidata')}</label>
                <select id="run-cand" className={selectCls} value={candidate} onChange={(e) => setCandidate(e.target.value)}>
                  {runs.map((r) => <option key={r.id} value={r.id}>{label(r)}</option>)}
                </select>
              </div>
              {diff && (
                <>
                  <div className="flex flex-wrap gap-2 text-xs">
                    {(Object.keys(CHANGE_LABEL) as CaseChange[]).map((k) => (
                      <span key={k} className={`rounded-md border px-2 py-1 ${CHANGE_TONE[k]}`}>{t(CHANGE_LABEL[k])}: {diff.summary[k]}</span>
                    ))}
                  </div>
                  {configDiff(diff.base.version, diff.candidate.version).length > 0 && (
                    <p className="text-xs text-dim">{t('Configuración distinta')}: {configDiff(diff.base.version, diff.candidate.version).join(' · ')}</p>
                  )}
                  <div className="overflow-x-auto">
                    <table className="w-full min-w-[480px] text-left text-xs">
                      <thead>
                        <tr className="border-b border-line text-[11px] uppercase tracking-wider text-faint">
                          <th className="py-2 pr-3 font-medium">{t('Caso')}</th>
                          <th className="px-3 py-2 font-medium">{t('Cambio')}</th>
                          <th className="px-3 py-2 font-medium">{t('Base')} → {t('Candidata')}</th>
                          <th className="px-3 py-2 text-right font-medium">{t('Δ salud')}</th>
                          <th className="py-2 pl-3 text-right font-medium">{t('Δ duración')}</th>
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-line">
                        {[...diff.cases].sort((a, b) => changeRank(a.change) - changeRank(b.change)).map((c) => (
                          <tr key={c.item_id}>
                            <td className="py-2 pr-3 font-mono text-dim">{caseName(c.item_id)}</td>
                            <td className="px-3 py-2"><span className={`rounded-md border px-1.5 py-0.5 ${CHANGE_TONE[c.change]}`}>{t(CHANGE_LABEL[c.change])}</span></td>
                            <td className="px-3 py-2 text-dim">{c.base?.status ?? '–'} → {c.candidate?.status ?? '–'}</td>
                            <td className="px-3 py-2 text-right">{fmtDelta(c.health_delta)}</td>
                            <td className="py-2 pl-3 text-right">{c.duration_delta_ms == null ? '–' : `${fmtDelta(c.duration_delta_ms / 1000, 1)} s`}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </>
              )}
            </div>
          )}
        </>
      )}
    </Card>
  );
}
