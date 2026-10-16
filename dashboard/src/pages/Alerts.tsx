import { useCallback, useEffect, useMemo, useState } from 'react';
import { api } from '../api';
import { useAlerts } from '../alerts-context';
import type { AlertEvent, AlertMetric, AlertRule } from '../types';
import { Card, Chip, EmptyState, ErrorState, Skeleton } from '../components/ui';
import {
  SEVERITY_LABEL, describeRule, formatMetricValue, fromInputValue, inputUnitLabel, severityRank, toInputValue,
} from '../helpers/alerts';
import type { MetricUnit } from '../helpers/alerts';
import { clockTime, timeAgo } from '../helpers/format';
import { t } from '../i18n';

const inputCls = 'rounded-lg border border-line bg-panel px-3 py-1.5 text-xs text-ink placeholder:text-faint focus:border-accent outline-none';
const SEV_TONE: Record<string, string> = {
  critical: 'border-bad/40 bg-bad/10 text-bad',
  warning: 'border-warn/40 bg-warn/10 text-warn',
  info: 'border-accent/40 bg-accent/10 text-accent',
};

interface Draft {
  id: string | null; name: string; metric: string; comparator: string; threshold: string;
  window_minutes: string; min_samples: string; scope: string; severity: string; cooldown_minutes: string;
}

const emptyDraft = (metric: AlertMetric | undefined): Draft => ({
  id: null, name: '', metric: metric?.id ?? 'error_rate', comparator: '>', threshold: '',
  window_minutes: '60', min_samples: '10', scope: '', severity: 'warning', cooldown_minutes: '30',
});

function Alerts() {
  const { metrics, refresh: refreshOpen, notifySupported, notifyEnabled, setNotifyEnabled } = useAlerts();
  const [rules, setRules] = useState<AlertRule[] | null>(null);
  const [events, setEvents] = useState<AlertEvent[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const byMetric = useMemo(() => new Map(metrics.map((m) => [m.id, m])), [metrics]);

  const load = useCallback(() => {
    Promise.all([api.listAlertRules(), api.listAlertEvents(undefined, 100)])
      .then(([r, e]) => { setRules(r); setEvents(e.events); setError(null); })
      .catch((e) => setError(e instanceof Error ? e.message : t('Error desconocido')));
    refreshOpen();
  }, [refreshOpen]);

  useEffect(() => {
    load();
    const id = setInterval(load, 20_000);
    return () => clearInterval(id);
  }, [load]);

  if (error && !rules) return <ErrorState message={error} onRetry={load} />;
  if (!rules) return <div className="space-y-4"><Skeleton className="h-24" /><Skeleton className="h-64" /></div>;

  const open = events.filter((e) => e.state === 'open').sort((a, b) => severityRank(a.severity) - severityRank(b.severity));
  const history = events.filter((e) => e.state === 'resolved');
  const draftMetric = draft ? byMetric.get(draft.metric) : undefined;
  const draftUnit: MetricUnit = draftMetric?.unit ?? 'count';

  const save = async () => {
    if (!draft) return;
    const typed = Number(draft.threshold);
    if (!draft.name.trim() || draft.threshold.trim() === '' || Number.isNaN(typed)) {
      setNote(t('Pon un nombre y un umbral numérico.'));
      return;
    }
    const body = {
      name: draft.name.trim(), comparator: draft.comparator as AlertRule['comparator'],
      threshold: fromInputValue(draftUnit, typed), window_minutes: Number(draft.window_minutes) || 60,
      min_samples: Number(draft.min_samples) || 1, scope: draft.scope.trim() || null,
      severity: draft.severity as AlertRule['severity'], cooldown_minutes: Number(draft.cooldown_minutes) || 0,
    };
    setBusy(true);
    try {
      if (draft.id) await api.updateAlertRule(draft.id, body);
      else await api.createAlertRule({ ...body, metric: draft.metric });
      setDraft(null);
      setNote(null);
      load();
    } catch {
      setNote(t('No se pudo guardar la regla.'));
    } finally {
      setBusy(false);
    }
  };

  const edit = (r: AlertRule) => {
    const unit = byMetric.get(r.metric)?.unit ?? 'count';
    setNote(null);
    setDraft({
      id: r.id, name: r.name, metric: r.metric, comparator: r.comparator,
      threshold: String(toInputValue(unit, r.threshold)), window_minutes: String(r.window_minutes),
      min_samples: String(r.min_samples), scope: r.scope ?? '', severity: r.severity,
      cooldown_minutes: String(r.cooldown_minutes),
    });
  };

  const run = async (fn: () => Promise<unknown>, done?: string) => {
    setBusy(true);
    try { await fn(); if (done) setNote(done); } catch { setNote(t('La acción falló.')); } finally { setBusy(false); load(); }
  };

  const permission = notifySupported ? Notification.permission : 'denied';

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-lg font-semibold tracking-tight">{t("Alertas")}</h1>
          <p className="text-xs text-faint">{t("Reglas que vigilan el Observer cada minuto y avisan al cruzar un umbral")}</p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {notifySupported && (
            <button type="button" aria-pressed={notifyEnabled} onClick={() => void setNotifyEnabled(!notifyEnabled)}
              className={`rounded-lg border px-3 py-1.5 text-xs ${notifyEnabled ? 'border-ok/40 bg-ok/10 text-ok' : 'border-line bg-panel text-dim hover:text-ink'}`}>
              
              {notifyEnabled ? t("Notificaciones de escritorio: activadas") : t("Notificaciones de escritorio: desactivadas")}
            </button>
          )}
          <button type="button" disabled={busy} onClick={() => void run(async () => {
            const r = await api.evaluateAlerts();
            setNote(t('Evaluadas {e} reglas: {o} abiertas, {r} resueltas.', { e: r.evaluated, o: r.opened, r: r.resolved }));
          })} className="rounded-lg border border-line bg-panel px-3 py-1.5 text-xs text-ink hover:bg-raised disabled:opacity-50">
            
            {t("Evaluar ahora")}
          </button>
          <button type="button" onClick={() => { setNote(null); setDraft(emptyDraft(metrics[0])); }}
            className="rounded-lg bg-accent px-3 py-1.5 text-xs font-medium text-canvas hover:opacity-90">
            
            {t("Nueva regla")}
          </button>
        </div>
      </div>

      {notifySupported && !notifyEnabled && permission === 'denied' && (
        <p className="text-xs text-faint">{t("El navegador bloqueó las notificaciones; habilítalas en los permisos del sitio para recibirlas.")}</p>
      )}
      {note && <p role="status" className="text-xs text-dim">{note}</p>}

      <Card title={t('Abiertas ({n})', { n: open.length })} bodyClass="p-0 pt-2">
        {open.length === 0 ? <EmptyState title={t("Sin alertas abiertas")} hint={t("Todo dentro de los umbrales.")} /> : (
          <ul className="divide-y divide-line">
            {open.map((e) => {
              const unit = byMetric.get(e.metric)?.unit ?? 'count';
              return (
                <li key={e.id} className="flex flex-wrap items-center gap-3 px-4 py-3 text-xs">
                  <span className={`rounded-full border px-2 py-0.5 text-[10px] font-medium ${SEV_TONE[e.severity]}`}>{t(SEVERITY_LABEL[e.severity])}</span>
                  <span className="font-medium text-ink">{t(e.rule_name)}</span>
                  <span className="text-dim">{formatMetricValue(unit, e.value)} {e.comparator} {formatMetricValue(unit, e.threshold)}</span>
                  {e.scope && <Chip>{e.scope}</Chip>}
                  <span className="flex-1" />
                  <span className="text-faint">{e.fired_at ? t('desde {t}', { t: timeAgo(e.fired_at) }) : ''}</span>
                  {e.acked_at ? <Chip>{t("vista")}</Chip> : (
                    <button type="button" onClick={() => void run(() => api.ackAlertEvent(e.id))}
                      className="rounded-md border border-line-strong px-2 py-1 text-ink hover:bg-raised">{t("Marcar vista")}</button>
                  )}
                </li>
              );
            })}
          </ul>
        )}
      </Card>

      {draft && (
        <Card title={draft.id ? t('Editar regla') : t('Nueva regla')}>
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            <label className="text-xs text-dim sm:col-span-2">{t("Nombre")}
              <input className={`${inputCls} mt-1 w-full`} value={draft.name} onChange={(e) => setDraft({ ...draft, name: e.target.value })} />
            </label>
            <label className="text-xs text-dim">{t("Métrica")}
              <select className={`${inputCls} mt-1 w-full`} value={draft.metric} disabled={!!draft.id}
                onChange={(e) => setDraft({ ...draft, metric: e.target.value })}>
                {metrics.map((m) => <option key={m.id} value={m.id}>{t(m.label)}</option>)}
              </select>
            </label>
            <label className="text-xs text-dim">{t("Severidad")}
              <select className={`${inputCls} mt-1 w-full`} value={draft.severity} onChange={(e) => setDraft({ ...draft, severity: e.target.value })}>
                <option value="info">{t("Aviso")}</option><option value="warning">{t("Advertencia")}</option><option value="critical">{t("Crítica")}</option>
              </select>
            </label>
            <label className="text-xs text-dim">{t("Condición")}
              <div className="mt-1 flex gap-2">
                <select aria-label={t("Comparador")} className={inputCls} value={draft.comparator} onChange={(e) => setDraft({ ...draft, comparator: e.target.value })}>
                  {['>', '>=', '<', '<='].map((c) => <option key={c}>{c}</option>)}
                </select>
                <input aria-label={t("Umbral")} inputMode="decimal" className={`${inputCls} w-full`} value={draft.threshold}
                  placeholder={inputUnitLabel(draftUnit) ? `umbral (${inputUnitLabel(draftUnit)})` : 'umbral'}
                  onChange={(e) => setDraft({ ...draft, threshold: e.target.value })} />
              </div>
            </label>
            {draftMetric?.kind !== 'quota' && draftMetric?.kind !== 'ingest' && (
              <>
                <label className="text-xs text-dim">{t("Ventana (min)")}
                  <input inputMode="numeric" className={`${inputCls} mt-1 w-full`} value={draft.window_minutes} onChange={(e) => setDraft({ ...draft, window_minutes: e.target.value })} />
                </label>
                <label className="text-xs text-dim">{t("Mínimo de muestras")}
                  <input inputMode="numeric" className={`${inputCls} mt-1 w-full`} value={draft.min_samples} onChange={(e) => setDraft({ ...draft, min_samples: e.target.value })} />
                </label>
              </>
            )}
            {(draftMetric?.kind === 'provider' || draftMetric?.kind === 'quota') && (
              <label className="text-xs text-dim">{t("Proveedor (opcional)")}
                <input className={`${inputCls} mt-1 w-full font-mono`} placeholder={t("p. ej. gemini")} value={draft.scope} onChange={(e) => setDraft({ ...draft, scope: e.target.value })} />
              </label>
            )}
            <label className="text-xs text-dim">{t("Espera tras resolverse (min)")}
              <input inputMode="numeric" className={`${inputCls} mt-1 w-full`} value={draft.cooldown_minutes} onChange={(e) => setDraft({ ...draft, cooldown_minutes: e.target.value })} />
            </label>
          </div>
          <div className="mt-4 flex gap-2">
            <button type="button" disabled={busy} onClick={() => void save()} className="rounded-lg bg-accent px-3 py-1.5 text-xs font-medium text-canvas hover:opacity-90 disabled:opacity-50">{t("Guardar")}</button>
            <button type="button" onClick={() => setDraft(null)} className="rounded-lg border border-line px-3 py-1.5 text-xs text-dim hover:text-ink">{t("Cancelar")}</button>
          </div>
        </Card>
      )}

      <Card title={t("Reglas")} bodyClass="p-0 pt-2">
        <div className="overflow-x-auto">
          <table className="w-full min-w-[640px] text-left text-xs">
            <thead>
              <tr className="border-b border-line text-[11px] uppercase tracking-wider text-faint">
                <th className="px-4 py-2.5 font-medium">{t("Regla")}</th>
                <th className="px-3 py-2.5 font-medium">{t("Condición")}</th>
                <th className="px-3 py-2.5 font-medium">{t("Severidad")}</th>
                <th className="px-3 py-2.5 font-medium">{t("Activa")}</th>
                <th aria-label={t("Acciones")} className="px-4 py-2.5 text-right font-medium" />
              </tr>
            </thead>
            <tbody className="divide-y divide-line">
              {rules.length === 0 ? <tr><td colSpan={5}><EmptyState title={t("Sin reglas")} hint={t("Crea una con «Nueva regla».")} /></td></tr> : rules.map((r) => (
                <tr key={r.id} className="hover:bg-panel-2">
                  <td className="px-4 py-2.5 font-medium text-ink">{t(r.name)}</td>
                  <td className="px-3 py-2.5 text-dim">{describeRule(r, byMetric.get(r.metric))}</td>
                  <td className="px-3 py-2.5"><span className={`rounded-full border px-2 py-0.5 text-[10px] font-medium ${SEV_TONE[r.severity]}`}>{t(SEVERITY_LABEL[r.severity])}</span></td>
                  <td className="px-3 py-2.5">
                    <button type="button" role="switch" aria-checked={r.enabled} aria-label={t('Activar {x}', { x: t(r.name) })}
                      onClick={() => void run(() => api.updateAlertRule(r.id, { enabled: !r.enabled }))}
                      className={`h-5 w-9 rounded-full border transition-colors ${r.enabled ? 'border-ok/40 bg-ok/30' : 'border-line-strong bg-raised'}`}>
                      <span className={`block size-3.5 rounded-full bg-ink transition-transform ${r.enabled ? 'translate-x-4' : 'translate-x-0.5'}`} />
                    </button>
                  </td>
                  <td className="px-4 py-2.5 text-right whitespace-nowrap">
                    <button type="button" onClick={() => edit(r)} className="mr-2 text-accent hover:underline">{t("Editar")}</button>
                    <button type="button" onClick={() => { if (window.confirm(t('¿Eliminar «{x}» y su historial?', { x: t(r.name) }))) void run(() => api.deleteAlertRule(r.id)); }}
                      className="text-bad hover:underline">{t("Eliminar")}</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>

      <Card title={t("Historial")} subtitle={t("Alertas ya resueltas")} bodyClass="p-0 pt-2">
        {history.length === 0 ? <EmptyState title={t("Sin historial todavía")} /> : (
          <ul className="divide-y divide-line">
            {history.slice(0, 30).map((e) => {
              const unit = byMetric.get(e.metric)?.unit ?? 'count';
              return (
                <li key={e.id} className="flex flex-wrap items-center gap-3 px-4 py-2 text-xs">
                  <span className="font-medium text-ink">{t(e.rule_name)}</span>
                  <span className="text-dim">{t("pico {v}", { v: formatMetricValue(unit, e.value) })}</span>
                  <span className="flex-1" />
                  <span className="text-faint">{e.fired_at ? clockTime(e.fired_at) : ''} → {e.resolved_at ? clockTime(e.resolved_at) : ''}</span>
                </li>
              );
            })}
          </ul>
        )}
      </Card>
    </div>
  );
}

export default Alerts;
