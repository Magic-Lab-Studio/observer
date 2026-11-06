import { useCallback, useEffect, useState } from 'react';
import { api } from '../api';
import type { CaptureMode, GroundednessConfig, GroundednessSection, PrivacyOverview, PrivacyProject } from '../types';
import { Card, Chip, EmptyState, ErrorState, Skeleton } from '../components/ui';
import { MODE_HINT, MODE_LABEL, needsEnforcement } from '../helpers/privacy';
import { fmtInt } from '../helpers/format';
import { t } from '../i18n';

const inputCls = 'rounded-lg border border-line bg-panel px-3 py-1.5 text-xs text-ink focus:border-accent outline-none';
const list = (text: string) => text.split(',').map((x) => x.trim()).filter(Boolean);

const GROUNDED_MODE_LABEL: Record<string, string> = {
  off: 'Apagado',
  rules: 'Reglas (sin modelo)',
  judge: 'Juez (modelo)',
  'rules+judge': 'Reglas y juez',
};

/** Groundedness per project: off by default; ``rules`` checks each reply against what its trace fetched. */
function GroundednessEditor({ projects }: { projects: PrivacyProject[] }) {
  const [project, setProject] = useState(projects[0]?.project_id ?? '');
  const [section, setSection] = useState<GroundednessSection | null>(null);
  const [draft, setDraft] = useState<GroundednessConfig | null>(null);
  const [types, setTypes] = useState('');
  const [applies, setApplies] = useState('');
  const [status, setStatus] = useState('');

  useEffect(() => {
    if (!project) return;
    setStatus('');
    api.getGroundedness(project).then((s) => {
      setSection(s); setDraft(s.value);
      setTypes(s.value.context.span_types.join(', ')); setApplies(s.value.applies_to.join(', '));
    }).catch(() => { setSection(null); setDraft(null); setStatus(t('No se pudo cargar')); });
  }, [project]);

  const save = async () => {
    if (!draft) return;
    const value: GroundednessConfig = { ...draft, applies_to: list(applies), context: { ...draft.context, span_types: list(types) } };
    try {
      const s = await api.setGroundedness(project, value);
      setSection(s); setDraft(s.value); setStatus(t('Guardado'));
    } catch (err) {
      setStatus(err instanceof Error ? err.message : t('Error desconocido'));
    }
  };

  return (
    <Card title={t('Sustento de las respuestas')} bodyClass="p-4">
      <p className="mb-3 text-xs text-faint">{t('Con «Reglas», cada respuesta se compara con lo que su traza leyó (salidas de herramientas y búsquedas): citas a fuentes que no se leyeron y números que las fuentes no contienen cuentan como sin sustento. No llama a ningún modelo. Sin contenido guardado (modo «Solo metadatos») el resultado es «desconocido».')}</p>
      <div className="flex flex-wrap items-end gap-3 text-xs">
        <label className="flex flex-col gap-1 text-dim">{t('Proyecto')}
          <select className={selectCls} value={project} onChange={(e) => setProject(e.target.value)}>
            {projects.map((p) => <option key={p.project_id} value={p.project_id}>{p.project_id}</option>)}
          </select>
        </label>
        {draft && section && (<>
          <label className="flex flex-col gap-1 text-dim">{t('Modo')}
            <select className={selectCls} value={draft.mode} onChange={(e) => setDraft({ ...draft, mode: e.target.value as GroundednessConfig['mode'] })}>
              {(['off', 'rules', 'judge', 'rules+judge'] as const).map((m) => (
                <option key={m} value={m} disabled={!section.built_modes.includes(m)}>
                  {t(GROUNDED_MODE_LABEL[m])}{section.built_modes.includes(m) ? '' : ` (${t('aún no disponible')})`}
                </option>
              ))}
            </select>
          </label>
          <label className="flex flex-col gap-1 text-dim">{t('Umbral')}
            <input type="number" min={0.05} max={1} step={0.05} className={`${inputCls} w-24`} value={draft.threshold}
              onChange={(e) => setDraft({ ...draft, threshold: Number(e.target.value) })} />
          </label>
          <label className="flex flex-col gap-1 text-dim">{t('Tipos de span que cuentan como fuente')}
            <input className={`${inputCls} w-56`} value={types} onChange={(e) => setTypes(e.target.value)} placeholder="tool, retrieval" />
          </label>
          <label className="flex flex-col gap-1 text-dim">{t('Solo para estas tareas o etiquetas (vacío: todas)')}
            <input className={`${inputCls} w-56`} value={applies} onChange={(e) => setApplies(e.target.value)} />
          </label>
          <label className="flex items-center gap-2 text-dim">
            <input type="checkbox" checked={draft.context.include_input}
              onChange={(e) => setDraft({ ...draft, context: { ...draft.context, include_input: e.target.checked } })} />
            {t('Contar la entrada del turno como fuente')}
          </label>
          <button type="button" onClick={() => void save()} className="rounded-lg border border-accent/40 px-3 py-1.5 text-accent hover:bg-accent/10">{t('Guardar')}</button>
        </>)}
      </div>
      {section && <p className="mt-2 text-xs text-faint">{section.source === 'default' ? t('Sin configuración propia: apagado.') : t('Configuración propia del proyecto.')}</p>}
      {status && <p role="status" className="mt-1 text-xs text-dim">{status}</p>}
    </Card>
  );
}

const selectCls = 'rounded-lg border border-line bg-panel px-3 py-1.5 text-xs text-ink focus:border-accent outline-none';

function Privacy() {
  const [data, setData] = useState<PrivacyOverview | null>(null);
  const [error, setError] = useState(false);
  const [notice, setNotice] = useState('');

  const load = useCallback(() => {
    api.getPrivacy().then((d) => { setData(d); setError(false); }).catch(() => setError(true));
  }, []);
  useEffect(() => { load(); }, [load]);

  const change = async (p: PrivacyProject, value: string) => {
    setNotice('');
    if (value === '') await api.clearCaptureMode(p.project_id);
    else await api.setCaptureMode(p.project_id, value as CaptureMode);
    load();
  };
  const enforce = async (p: PrivacyProject) => {
    if (!window.confirm(t('Esto reescribe los datos ya guardados de «{p}» y no se puede deshacer. ¿Continuar?', { p: p.project_id }))) return;
    const r = await api.enforceCaptureMode(p.project_id);
    setNotice(t('{n} spans modificados de {m} revisados.', { n: fmtInt(r.spans_changed), m: fmtInt(r.spans_scanned) }));
    load();
  };

  if (error && !data) return <ErrorState message={t('No se pudo cargar')} onRetry={load} />;
  if (!data) return <Skeleton className="h-64" />;

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-lg font-semibold tracking-tight">{t('Privacidad de captura')}</h1>
        <p className="text-xs text-faint">{t('Qué guarda Observer de cada proyecto y qué contiene lo ya guardado (últimos spans).')}</p>
      </div>
      <Card title={t('Modos')} bodyClass="p-4">
        <ul className="grid gap-2 text-xs text-dim md:grid-cols-3">
          {data.modes.map((m) => (
            <li key={m}><span className="font-medium text-ink">{t(MODE_LABEL[m])}</span><br />{t(MODE_HINT[m])}</li>
          ))}
        </ul>
        <p className="mt-3 text-xs text-faint">{t('Sin modo propio, cada vía de ingesta conserva su comportamiento: el SDK y el exportador de Manitos guardan lo que envían y OTLP guarda solo metadatos. La redacción usa patrones: no detecta nombres ni direcciones.')}</p>
      </Card>
      {notice && <p role="status" className="text-xs text-ok">{notice}</p>}
      {data.projects.length === 0 ? <EmptyState title={t('Aún no hay proyectos')} /> : (
        <div className="overflow-x-auto rounded-xl border border-line bg-panel">
          <table className="w-full min-w-[640px] text-left text-xs">
            <thead>
              <tr className="border-b border-line text-[11px] uppercase tracking-wider text-faint">
                <th className="px-4 py-2.5 font-medium">{t('Proyecto')}</th>
                <th className="px-3 py-2.5 font-medium">{t('Modo')}</th>
                <th className="px-3 py-2.5 text-right font-medium">{t('Con entrada')}</th>
                <th className="px-3 py-2.5 text-right font-medium">{t('Con salida')}</th>
                <th className="px-3 py-2.5 text-right font-medium">{t('Datos sensibles')}</th>
                <th className="px-4 py-2.5 text-right font-medium">{t('Acción')}</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-line">
              {data.projects.map((p) => (
                <tr key={p.project_id}>
                  <td className="px-4 py-2.5 font-mono text-ink">{p.project_id}</td>
                  <td className="px-3 py-2.5">
                    <select aria-label={t('Modo de {p}', { p: p.project_id })} className={selectCls} value={p.capture_mode ?? ''} onChange={(e) => void change(p, e.target.value)}>
                      <option value="">{t('Predeterminado')}</option>
                      {data.modes.map((m) => <option key={m} value={m}>{t(MODE_LABEL[m])}</option>)}
                    </select>
                  </td>
                  <td className="px-3 py-2.5 text-right">{fmtInt(p.report.with_input)} <span className="text-faint">/ {fmtInt(p.report.sampled_spans)}</span></td>
                  <td className="px-3 py-2.5 text-right">{fmtInt(p.report.with_output)}</td>
                  <td className="px-3 py-2.5 text-right">{p.report.with_sensitive_patterns > 0 ? <Chip tone="warn">{fmtInt(p.report.with_sensitive_patterns)}</Chip> : 0}</td>
                  <td className="px-4 py-2.5 text-right">
                    {needsEnforcement(p.capture_mode, p.report)
                      ? <button type="button" onClick={() => void enforce(p)} className="rounded-lg border border-warn/40 px-2.5 py-1 text-warn hover:bg-warn/10">{t('Aplicar a lo guardado')}</button>
                      : <span className="text-faint">–</span>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {data.projects.length > 0 && <GroundednessEditor projects={data.projects} />}
    </div>
  );
}

export default Privacy;
