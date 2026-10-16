import { useEffect, useState } from 'react';
import { api } from '../api';
import type { IngestHealth as Health } from '../types';
import { Card, EmptyState } from './ui';
import { silenceText, silenceTone, splitDormant } from '../helpers/ingest';
import { fmtInt } from '../helpers/format';
import { t } from '../i18n';

const DOT: Record<string, string> = { ok: 'bg-ok', warn: 'bg-warn', bad: 'bg-bad', neutral: 'bg-faint' };

/** Is telemetry still arriving? Fails silently: it is a hint, never a reason to show an error. */
export default function IngestHealth() {
  const [health, setHealth] = useState<Health | null>(null);

  useEffect(() => {
    let live = true;
    const load = () => api.getIngestHealth().then((h) => { if (live) setHealth(h); }).catch(() => undefined);
    load();
    const id = setInterval(load, 30_000);
    return () => { live = false; clearInterval(id); };
  }, []);

  if (!health) return null;
  const { active, dormant } = splitDormant(health.projects);
  return (
    <Card title={t('Recepción de telemetría')} subtitle={t('Cuándo llegó la última traza de cada proyecto')}>
      {active.length === 0 && dormant.length === 0 ? <EmptyState title={t('Aún no llega telemetría')} /> : (
        <ul className="divide-y divide-line text-xs">
          {active.map((p) => (
            <li key={p.project_id} className="flex flex-wrap items-center gap-3 py-2">
              <span aria-hidden className={`size-2 rounded-full ${DOT[silenceTone(p.silence_minutes, p.traces_1h)]}`} />
              <span className="font-mono text-ink">{p.project_id}</span>
              <span className="text-dim">{t('hace {x}', { x: silenceText(p.silence_minutes) })}</span>
              <span className="ml-auto text-faint">{t('{a} en 5 min · {b} en 1 h · {c} en 24 h', { a: fmtInt(p.traces_5m), b: fmtInt(p.traces_1h), c: fmtInt(p.traces_24h) })}</span>
            </li>
          ))}
        </ul>
      )}
      {dormant.length > 0 && (
        <p className="pt-2 text-xs text-faint">{t('Sin trazas desde hace más de 7 días: {names}', { names: dormant.map((p) => p.project_id).join(', ') })}</p>
      )}
    </Card>
  );
}
