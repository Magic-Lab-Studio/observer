import { useEffect, useState } from 'react';
import { api } from '../api';
import type { DatasetSummary } from '../types';
import { buildExpect, emptyCaseForm, hasExpectation } from '../helpers/datasets';
import type { CaseForm } from '../helpers/datasets';
import { Card } from './ui';
import { t } from '../i18n';

const inputCls = 'w-full rounded-lg border border-line bg-panel px-3 py-1.5 text-xs text-ink placeholder:text-faint focus:border-accent outline-none';
const labelCls = 'mb-1 block text-[11px] font-semibold uppercase tracking-wider text-faint';

/** Saves a trace as a test case. Only metadata leaves the trace; the expectations are typed here. */
export default function SaveAsCase({ traceId, onClose }: { traceId: string; onClose: () => void }) {
  const [datasets, setDatasets] = useState<DatasetSummary[]>([]);
  const [datasetId, setDatasetId] = useState('');
  const [newName, setNewName] = useState('');
  const [form, setForm] = useState<CaseForm>(emptyCaseForm());
  const [label, setLabel] = useState('');
  const [tags, setTags] = useState('');
  const [notes, setNotes] = useState('');
  const [state, setState] = useState<'idle' | 'saving' | 'saved' | 'error'>('idle');

  useEffect(() => {
    api.listDatasets().then((d) => { setDatasets(d); if (d[0]) setDatasetId(d[0].id); }).catch(() => setDatasets([]));
  }, []);

  const set = <K extends keyof CaseForm>(k: K, v: CaseForm[K]) => setForm((f) => ({ ...f, [k]: v }));
  const expect = buildExpect(form);
  const creating = datasetId === '' || datasetId === '__new';
  const canSave = hasExpectation(expect) && (creating ? newName.trim() !== '' : true) && state !== 'saving';

  const save = async () => {
    setState('saving');
    try {
      const id = creating ? (await api.createDataset(newName.trim())).id : datasetId;
      await api.addCase(id, {
        trace_id: traceId, tags: tags.split(',').map((x) => x.trim()).filter(Boolean), expect,
        label: label || null, notes: notes.trim() || null,
      });
      setState('saved');
    } catch {
      setState('error');
    }
  };

  const check = (k: 'noError' | 'noTruncation' | 'noDegradation' | 'noFallback', text: string) => (
    <label className="flex items-center gap-2 text-xs text-dim">
      <input type="checkbox" checked={form[k]} onChange={(e) => set(k, e.target.checked)} /> {text}
    </label>
  );

  return (
    <Card title={t('Guardar como caso de prueba')}
      subtitle={t('Solo se guardan metadatos de la traza (intención, herramientas, modelo, señales de salud). Nada del texto del usuario ni de la respuesta.')}>
      <div className="grid gap-4 md:grid-cols-2">
        <div className="space-y-3">
          <div>
            <label className={labelCls} htmlFor="case-ds">{t('Conjunto')}</label>
            <select id="case-ds" className={inputCls} value={datasetId || '__new'} onChange={(e) => setDatasetId(e.target.value)}>
              {datasets.map((d) => <option key={d.id} value={d.id}>{d.name} ({d.items})</option>)}
              <option value="__new">{t('+ Nuevo conjunto')}</option>
            </select>
            {creating && <input className={`${inputCls} mt-2`} placeholder={t('Nombre del conjunto')} value={newName} onChange={(e) => setNewName(e.target.value)} />}
          </div>
          <div>
            <label className={labelCls} htmlFor="case-req">{t('Herramientas que debe usar')}</label>
            <input id="case-req" className={inputCls} placeholder="web_search, calc" value={form.toolsRequired} onChange={(e) => set('toolsRequired', e.target.value)} />
          </div>
          <div>
            <label className={labelCls} htmlFor="case-forb">{t('Herramientas que no debe usar')}</label>
            <input id="case-forb" className={inputCls} placeholder="shell_run" value={form.toolsForbidden} onChange={(e) => set('toolsForbidden', e.target.value)} />
          </div>
          <div>
            <label className={labelCls} htmlFor="case-has">{t('Frases que debe contener')}</label>
            <input id="case-has" className={inputCls} value={form.mustContain} onChange={(e) => set('mustContain', e.target.value)} />
          </div>
          <div>
            <label className={labelCls} htmlFor="case-not">{t('Frases que no debe contener')}</label>
            <input id="case-not" className={inputCls} value={form.mustNotContain} onChange={(e) => set('mustNotContain', e.target.value)} />
          </div>
        </div>
        <div className="space-y-3">
          <div>
            <span className={labelCls}>{t('Condiciones de salud')}</span>
            <div className="space-y-1.5">
              {check('noError', t('Sin error'))}
              {check('noTruncation', t('Sin truncar'))}
              {check('noDegradation', t('Sin degradar'))}
              {check('noFallback', t('Sin vía alterna'))}
            </div>
            <div className="mt-2 flex items-center gap-2 text-xs text-dim">
              <label htmlFor="case-max">{t('Duración máxima (s)')}</label>
              <input id="case-max" className={`${inputCls} w-20`} inputMode="decimal" value={form.maxSeconds} onChange={(e) => set('maxSeconds', e.target.value)} />
            </div>
          </div>
          <div>
            <label className={labelCls} htmlFor="case-label">{t('Veredicto humano')}</label>
            <select id="case-label" className={inputCls} value={label} onChange={(e) => setLabel(e.target.value)}>
              <option value="">{t('Sin veredicto')}</option>
              <option value="correct">{t('Correcta')}</option>
              <option value="incorrect">{t('Incorrecta')}</option>
            </select>
          </div>
          <div>
            <label className={labelCls} htmlFor="case-tags">{t('Etiquetas')}</label>
            <input id="case-tags" className={inputCls} placeholder="search, regresión" value={tags} onChange={(e) => setTags(e.target.value)} />
          </div>
          <div>
            <label className={labelCls} htmlFor="case-notes">{t('Notas')}</label>
            <textarea id="case-notes" rows={2} className={inputCls} value={notes} onChange={(e) => setNotes(e.target.value)} />
          </div>
        </div>
      </div>
      <div className="mt-4 flex flex-wrap items-center gap-3">
        <button type="button" onClick={() => void save()} disabled={!canSave || state === 'saved'}
          className="rounded-lg bg-accent px-3 py-1.5 text-xs font-medium text-canvas disabled:opacity-40">{t('Guardar caso')}</button>
        <button type="button" onClick={onClose} className="rounded-lg border border-line bg-panel px-3 py-1.5 text-xs text-ink hover:bg-raised">{t('Cerrar')}</button>
        {!hasExpectation(expect) && <span className="text-xs text-faint">{t('Define al menos una expectativa.')}</span>}
        {state === 'saved' && <span className="text-xs text-ok">{t('Caso guardado.')}</span>}
        {state === 'error' && <span className="text-xs text-bad">{t('No se pudo guardar.')}</span>}
      </div>
    </Card>
  );
}
