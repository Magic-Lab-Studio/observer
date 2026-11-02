import { useCallback, useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { api } from '../api';
import { useFilters } from '../context';
import type { GovernanceKind, GovernanceListing, GovernanceSummary, GovernanceTurn } from '../types';
import { Card, EmptyState, ErrorState, Segmented, Skeleton } from '../components/ui';
import { KINDS, STEP_LABEL, describe, outcomeTone, pathLabels, rowOutcome, type Tone } from '../helpers/governance';
import { fmtInt, fmtMs, shortId, timeAgo } from '../helpers/format';
import { t } from '../i18n';

const TONE_CLASS: Record<Tone, string> = {
  good: 'bg-ok/15 text-ok',
  bad: 'bg-bad/15 text-bad',
  warn: 'bg-warn/15 text-warn',
  neutral: 'bg-raised text-dim',
};

function Outcome({ value }: { value: string | null }) {
  if (!value) return <span className="text-faint">–</span>;
  const label = value === '__abstain__' ? t('abstención') : value;
  return <span className={`inline-flex rounded-md px-1.5 py-0.5 font-mono text-[11px] ${TONE_CLASS[outcomeTone(value)]}`}>{label}</span>;
}

const SUMMARY: { key: keyof GovernanceSummary['counts']; label: string }[] = [
  { key: 'decision', label: 'Decisiones' },
  { key: 'security', label: 'Controles de seguridad' },
  { key: 'approval', label: 'Aprobaciones' },
  { key: 'skill', label: 'Skills' },
];

function TurnPath({ traceId, onClose }: { traceId: string; onClose: () => void }) {
  const [turn, setTurn] = useState<GovernanceTurn | null>(null);
  const [error, setError] = useState(false);
  useEffect(() => {
    setTurn(null);
    setError(false);
    api.getGovernanceTurn(traceId).then(setTurn).catch(() => setError(true));
  }, [traceId]);

  return (
    <Card
      title={t('Trayectoria del turno')}
      subtitle={turn ? pathLabels(turn.steps).map((l) => t(l)).join(' → ') : undefined}
      actions={<button type="button" onClick={onClose} className="text-xs text-dim hover:text-ink">{t('Cerrar')}</button>}
    >
      {error && <ErrorState message={t('No se pudo cargar')} />}
      {!error && !turn && <Skeleton className="h-24" />}
      {turn && (
        <div className="space-y-3">
          {!turn.complete && (
            <p role="status" className="text-xs text-warn">
              {t('Se pidió permiso y nadie respondió: {tools}', { tools: turn.unanswered_asks.join(', ') })}
            </p>
          )}
          <ol className="space-y-2">
            {turn.steps.map((s, i) => (
              <li key={s.span_id} className="flex flex-wrap items-center gap-2 text-xs">
                <span className="w-5 text-right text-faint">{i + 1}</span>
                <span className="w-24 font-medium text-ink">{t(STEP_LABEL[s.span_type] ?? s.span_type)}</span>
                <Outcome value={s.outcome} />
                <span className="min-w-0 flex-1 truncate text-dim">{describe(s, t)}</span>
                <span className="text-faint">{fmtMs(s.duration_ms)}</span>
              </li>
            ))}
          </ol>
          <Link to={`/traces/${turn.trace_id}`} className="text-xs text-accent hover:underline">{t('Abrir la traza')}</Link>
        </div>
      )}
    </Card>
  );
}

function Governance() {
  const { hours, project } = useFilters();
  const [kind, setKind] = useState<GovernanceKind>('denials');
  const [listing, setListing] = useState<GovernanceListing | null>(null);
  const [summary, setSummary] = useState<GovernanceSummary | null>(null);
  const [error, setError] = useState(false);
  const [selected, setSelected] = useState<string | null>(null);

  const load = useCallback(() => {
    if (!project) return;
    setError(false);
    Promise.all([api.getGovernance(kind, hours, project), api.getGovernanceSummary(hours, project)])
      .then(([l, s]) => { setListing(l); setSummary(s); })
      .catch(() => setError(true));
  }, [kind, hours, project]);
  useEffect(() => { setListing(null); load(); }, [load]);

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-lg font-semibold tracking-tight">{t('Gobernanza')}</h1>
        <p className="text-xs text-faint">{t('Qué decidió el agente, qué se bloqueó, qué permisos se pidieron y qué skills se cargaron. Solo nombres y códigos: nunca el contenido.')}</p>
      </div>

      {summary && (
        <dl className="grid grid-cols-2 gap-3 md:grid-cols-4">
          {SUMMARY.map(({ key, label }) => (
            <div key={key} className="rounded-xl border border-line bg-panel p-3">
              <dt className="text-[11px] uppercase tracking-wider text-faint">{t(label)}</dt>
              <dd className="mt-1 text-xl font-semibold">{fmtInt(summary.counts[key].total)}</dd>
              <dd className="mt-1 flex flex-wrap gap-1">
                {Object.entries(summary.counts[key].by).map(([value, n]) => (
                  <span key={value} className={`rounded px-1 font-mono text-[10px] ${TONE_CLASS[outcomeTone(value)]}`}>{value} {n}</span>
                ))}
              </dd>
            </div>
          ))}
        </dl>
      )}

      <Segmented label={t('Vista')} options={KINDS.map((k) => ({ value: k.value, label: t(k.label) }))} value={kind} onChange={(k) => { setKind(k); setSelected(null); }} />

      {selected && <TurnPath traceId={selected} onClose={() => setSelected(null)} />}

      {error && !listing && <ErrorState message={t('No se pudo cargar')} onRetry={load} />}
      {!error && !listing && <Skeleton className="h-64" />}
      {listing && listing.items.length === 0 && (
        <EmptyState title={t('Nada en esta ventana')} hint={t('ManitOS envía estos eventos cuando su emisor de gobernanza está conectado.')} />
      )}
      {listing && listing.items.length > 0 && (
        <div className="overflow-x-auto rounded-xl border border-line bg-panel">
          <table className="w-full min-w-[640px] text-left text-xs">
            <thead>
              <tr className="border-b border-line text-[11px] uppercase tracking-wider text-faint">
                <th className="px-4 py-2.5 font-medium">{t('Cuándo')}</th>
                <th className="px-3 py-2.5 font-medium">{t('Resultado')}</th>
                <th className="px-3 py-2.5 font-medium">{t('Detalle')}</th>
                <th className="px-3 py-2.5 font-medium">{t('Sesión')}</th>
                <th className="px-4 py-2.5 text-right font-medium">{t('Turno')}</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-line">
              {listing.items.map((item) => (
                <tr key={item.span_id} className={selected === item.trace_id ? 'bg-panel-2' : undefined}>
                  <td className="px-4 py-2.5 text-dim">{item.start_time ? timeAgo(item.start_time) : '–'}</td>
                  <td className="px-3 py-2.5"><Outcome value={rowOutcome(item)} /></td>
                  <td className="max-w-[28rem] truncate px-3 py-2.5 text-ink">{describe(item, t)}</td>
                  <td className="px-3 py-2.5 font-mono text-faint">{shortId(item.session_id, 18)}</td>
                  <td className="px-4 py-2.5 text-right">
                    <button type="button" onClick={() => setSelected(item.trace_id)} className="rounded-lg border border-line px-2 py-1 text-dim hover:text-ink">
                      {t('Ver trayectoria')}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {listing.total > listing.items.length && (
            <p className="border-t border-line px-4 py-2 text-xs text-faint">{t('Se muestran {n} de {m}.', { n: fmtInt(listing.items.length), m: fmtInt(listing.total) })}</p>
          )}
        </div>
      )}
    </div>
  );
}

export default Governance;
