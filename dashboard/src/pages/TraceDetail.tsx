import { useState, useEffect, useMemo } from 'react';
import { useParams, Link } from 'react-router-dom';
import { api } from '../api';
import type { Trace, Span } from '../types';
import TraceWaterfall from '../components/TraceWaterfall';
import SpanDetail from '../components/SpanDetail';
import SaveAsCase from '../components/SaveAsCase';
import { Card, Chip, EmptyState, ErrorState, Icon, Kpi, Skeleton, StatusPill } from '../components/ui';
import { fmtInt, fmtMs, fmtUsd, stageLabel, stageTimings, traceDurationMs } from '../helpers/format';
import { parseApiDate } from '../dates';
import { parseAttempts, resultLabel, resultTone } from '../helpers/chain';
import { localeTag, t } from '../i18n';

const CHAIN_TONE = { ok: 'border-ok/40 bg-ok/10 text-ok', warn: 'border-warn/40 bg-warn/10 text-warn', bad: 'border-bad/40 bg-bad/10 text-bad', skip: 'border-line-strong bg-raised text-faint' };

function ChainView({ text, answeredBy }: { text: string; answeredBy: string }) {
  const attempts = parseAttempts(text);
  if (attempts.length === 0) return null;
  return (
    <Card title={t("Cadena de este turno")} subtitle={answeredBy ? t('Respondió {x}', { x: answeredBy }) : undefined} className="mb-4">
      <ol className="space-y-1.5">
        {attempts.map((a, i) => (
          <li key={i} className={`flex items-center gap-2 rounded-lg border px-3 py-2 text-xs ${CHAIN_TONE[resultTone(a.result)]}`}>
            <span className="w-4 text-faint">{i + 1}</span>
            <span className="font-semibold">{a.backend}</span>
            <span className="min-w-0 flex-1 truncate font-mono opacity-80" title={a.model}>{a.model || ''}</span>
            <span>{resultLabel(a.result)}</span>
            {a.ms != null && <span className="font-medium">{fmtMs(a.ms)}</span>}
          </li>
        ))}
      </ol>
    </Card>
  );
}

function TraceDetail() {
  const { traceId } = useParams<{ traceId: string }>();
  const [trace, setTrace] = useState<Trace | null>(null);
  const [spans, setSpans] = useState<Span[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<Span | null>(null);

  useEffect(() => {
    if (!traceId) return;
    setLoading(true);
    Promise.all([api.getTrace(traceId), api.getTraceSpans(traceId)])
      .then(([t, s]) => { setTrace(t); setSpans(s); setError(null); })
      .catch((e) => setError(e instanceof Error ? e.message : t('Error desconocido')))
      .finally(() => setLoading(false));
  }, [traceId]);

  const stages = useMemo(
    () => trace ? Object.entries(stageTimings(trace)).map(([k, v]) => ({ name: stageLabel(k), ms: v })).sort((a, b) => b.ms - a.ms) : [],
    [trace],
  );

  if (loading) {
    return <div className="space-y-4"><Skeleton className="h-10 w-72" /><Skeleton className="h-24" /><Skeleton className="h-80" /></div>;
  }
  if (error || !trace) {
    return (
      <div className="space-y-3">
        <ErrorState message={error || t('Traza no encontrada')} />
        <Link to="/traces" className="text-xs text-accent hover:underline">{t("← Volver a trazas")}</Link>
      </div>
    );
  }

  const dur = traceDurationMs(trace);
  const tokens = spans.reduce((a, s) => a + (s.tokens_input ?? 0) + (s.tokens_output ?? 0), 0);
  const cost = spans.reduce((a, s) => a + (s.cost_usd ?? 0), 0);
  const stageTotal = stages.reduce((a, s) => a + s.ms, 0) || 1;
  const meta = [
    ['Proyecto', trace.project_id], ['Entorno', trace.environment], ['Instancia', trace.service_instance_id],
    ['Sesión', trace.session_id], ['Turno', trace.turn_id], ['Esquema', trace.schema_version],
  ].filter(([, v]) => v) as [string, string][];
  const extraMeta = trace.metadata ? Object.fromEntries(Object.entries(trace.metadata).filter(([k]) => k !== 'stage_timings_ms')) : {};

  return (
    <div className="space-y-4">
      <div>
        <Link to="/traces" className="inline-flex items-center gap-1 text-xs text-dim hover:text-ink">
          <Icon name="back" className="size-3.5" />  {t("Trazas")}
        </Link>
        <div className="mt-2 flex flex-wrap items-center gap-3">
          <h1 className="text-lg font-semibold tracking-tight">{trace.name}</h1>
          <StatusPill status={trace.status} />
          {trace.environment && <Chip>{trace.environment}</Chip>}
          <button type="button" onClick={() => setSaving((v) => !v)}
            className="ml-auto rounded-lg border border-line bg-panel px-3 py-1.5 text-xs text-ink hover:bg-raised">{t('Guardar como caso')}</button>
        </div>
        <p className="mt-1 font-mono text-[11px] text-faint">{trace.id} · {parseApiDate(trace.created_at).toLocaleString(localeTag())}</p>
      </div>

      {saving && <SaveAsCase traceId={trace.id} onClose={() => setSaving(false)} />}

      <dl className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <Kpi label={t("Duración")} value={fmtMs(dur)} />
        <Kpi label={t("Spans")} value={fmtInt(spans.length)} />
        <Kpi label={t("Tokens")} value={tokens ? fmtInt(tokens) : '–'} />
        <Kpi label={t("Costo")} value={fmtUsd(cost)} />
      </dl>

      {stages.length > 0 && (
        <Card title={t("Etapas del turno")} subtitle={t('Suma de etapas {v}', { v: fmtMs(stageTotal) })}>
          <div className="flex h-3 overflow-hidden rounded-full bg-raised">
            {stages.map((s, i) => (
              <div key={s.name} title={`${s.name}: ${fmtMs(s.ms)}`} style={{ width: `${(s.ms / stageTotal) * 100}%`, background: ['#6c8cff', '#9b7bea', '#3fb68b', '#e5a33b', '#d6657e', '#4fb7d6'][i % 6] }} />
            ))}
          </div>
          <ul className="mt-3 flex flex-wrap gap-x-5 gap-y-1.5 text-xs">
            {stages.map((s, i) => (
              <li key={s.name} className="inline-flex items-center gap-1.5 text-dim">
                <span className="size-2 rounded-sm" style={{ background: ['#6c8cff', '#9b7bea', '#3fb68b', '#e5a33b', '#d6657e', '#4fb7d6'][i % 6] }} />
                {s.name} <span className="font-medium text-ink">{fmtMs(s.ms)}</span>
              </li>
            ))}
          </ul>
        </Card>
      )}

      <div className="grid gap-4 xl:grid-cols-[minmax(0,1.35fr)_minmax(0,1fr)]">
        <div className="space-y-4 min-w-0">
          <TraceWaterfall traceId={traceId!} spans={spans} selectedId={selected?.id} onSelect={(s) => setSelected(selected?.id === s.id ? null : s)} />
          <Card title={t("Correlación")} bodyClass="p-4">
            <dl className="grid gap-x-6 gap-y-3 sm:grid-cols-2">
              {meta.map(([label, value]) => (
                <div key={label} className="min-w-0">
                  <dt className="text-[11px] uppercase tracking-wider text-faint">{t(label)}</dt>
                  <dd className="mt-0.5 break-all font-mono text-xs text-ink">{value}</dd>
                </div>
              ))}
            </dl>
            {Object.keys(extraMeta).length > 0 && (
              <pre className="mt-4 max-h-56 overflow-auto rounded-lg bg-canvas p-3 text-xs text-dim">{JSON.stringify(extraMeta, null, 2)}</pre>
            )}
          </Card>
        </div>
        <div className="min-w-0 xl:sticky xl:top-[72px] xl:self-start">
          {selected?.name === 'llm.chain' && <ChainView text={String((selected.attributes ?? {}).attempts_text ?? '')} answeredBy={String((selected.attributes ?? {}).answered_by ?? '')} />}
          {selected ? <SpanDetail span={selected} /> : (
            <Card title={t("Detalle del span")}><EmptyState title={t("Selecciona un span en la cascada")} hint={t("Verás sus atributos, entrada y salida.")} /></Card>
          )}
        </div>
      </div>
    </div>
  );
}

export default TraceDetail;
