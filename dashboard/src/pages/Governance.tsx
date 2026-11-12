import { useCallback, useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { api } from '../api';
import { useFilters } from '../context';
import type { GateFunnel, GovernanceKind, GovernanceListing, GovernanceSummary, GovernanceTurn } from '../types';
import { Card, EmptyState, ErrorState, Segmented, Skeleton } from '../components/ui';
import { KINDS, STEP_LABEL, describe, outcomeTone, pathLabels, rowOutcome, type Tone } from '../helpers/governance';
import { fmtInt, fmtMs, fmtPct, shortId, timeAgo } from '../helpers/format';
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

/** Gate funnel (roadmap step 14): the gate that stops most turns first, with its reason codes. */
function Funnel({ hours, project }: { hours: number; project: string }) {
  const [data, setData] = useState<GateFunnel | null>(null);
  useEffect(() => {
    if (!project) return;
    let live = true;
    api.getGateFunnel(hours, project).then((d) => { if (live) setData(d); }).catch(() => { if (live) setData(null); });
    return () => { live = false; };
  }, [hours, project]);
  if (!data || data.gates.length === 0) return null;
  return (
    <Card title={t('Embudo de compuertas')} bodyClass="p-4"
      subtitle={t('{n} de {m} turnos pasaron por alguna compuerta. Primero, la que más turnos frena.', { n: fmtInt(data.turns_with_gates), m: fmtInt(data.turns) })}>
      <ul className="space-y-2 text-xs">
        {data.gates.map((g) => (
          <li key={g.gate} className="grid grid-cols-[1fr_auto] items-center gap-x-3 gap-y-1 sm:grid-cols-[12rem_1fr_9rem]">
            <span className="font-mono text-ink">{g.gate}</span>
            <span className="col-span-2 row-start-2 h-2 overflow-hidden rounded bg-raised sm:col-span-1 sm:row-start-auto">
              <span className="block h-full bg-bad/70" style={{ width: `${g.stop_rate * 100}%` }} />
            </span>
            <span className="text-right text-dim sm:col-start-3 sm:row-start-1">{t('frenó {n} de {m}', { n: fmtInt(g.stopped), m: fmtInt(g.turns) })} · {fmtPct(g.stop_rate, 0)}</span>
            {g.reason_codes.length > 0 && (
              <span className="col-span-2 flex flex-wrap gap-1 sm:col-span-3">
                {g.reason_codes.map((r) => <span key={r.code} className="rounded bg-bad/10 px-1 font-mono text-[10px] text-bad">{r.code} {r.count}</span>)}
              </span>
            )}
          </li>
        ))}
      </ul>
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

      <Funnel hours={hours} project={project} />

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
