import { useCallback, useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { api } from '../api';
import type { DatasetDetail, DatasetSummary } from '../types';
import { Card, Chip, EmptyState, ErrorState, Skeleton } from '../components/ui';
import RunsPanel from '../components/RunsPanel';
import { timeAgo } from '../helpers/format';
import { t } from '../i18n';

const CHECK_LABEL: Record<string, string> = { tools: 'Herramientas', health: 'Salud', text: 'Texto' };

function Datasets() {
  const [datasets, setDatasets] = useState<DatasetSummary[] | null>(null);
  const [open, setOpen] = useState<DatasetDetail | null>(null);
  const [error, setError] = useState(false);

  const load = useCallback(() => {
    api.listDatasets().then((d) => { setDatasets(d); setError(false); }).catch(() => setError(true));
  }, []);
  useEffect(() => { load(); }, [load]);

  const show = (id: string) => api.getDataset(id).then(setOpen).catch(() => setOpen(null));
  const remove = async (id: string) => {
    await api.deleteDataset(id);
    setOpen(null);
    load();
  };
  const removeCase = async (id: string) => {
    await api.deleteCase(id);
    if (open) await show(open.id);
    load();
  };
  const download = async (d: DatasetDetail) => {
    const blob = new Blob([JSON.stringify(await api.exportDataset(d.id), null, 2)], { type: 'application/json' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = `${d.name.replace(/[^\w.-]+/g, '_') || 'dataset'}.json`;
    a.click();
    URL.revokeObjectURL(a.href);
  };

  if (error && !datasets) return <ErrorState message={t('No se pudo cargar')} onRetry={load} />;
  if (!datasets) return <Skeleton className="h-64" />;

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-lg font-semibold tracking-tight">{t('Casos de prueba')}</h1>
        <p className="text-xs text-faint">{t('Fallos guardados desde una traza con lo que debe cumplir la próxima ejecución. Solo metadatos.')}</p>
      </div>
      {datasets.length === 0 ? (
        <EmptyState title={t('Aún no hay conjuntos')} hint={t('Abre una traza y usa «Guardar como caso».')} />
      ) : (
        <div className="grid gap-4 xl:grid-cols-[320px_minmax(0,1fr)]">
          <ul className="space-y-2">
            {datasets.map((d) => (
              <li key={d.id}>
                <button type="button" onClick={() => void show(d.id)}
                  className={`w-full rounded-xl border px-4 py-3 text-left text-xs hover:bg-raised ${open?.id === d.id ? 'border-accent bg-raised' : 'border-line bg-panel'}`}>
                  <span className="block font-medium text-ink">{d.name}</span>
                  <span className="text-faint">{t('{n} casos', { n: d.items })}{d.created_at ? ` · ${timeAgo(d.created_at)}` : ''}</span>
                </button>
              </li>
            ))}
          </ul>
          <div className="min-w-0">
            {!open ? <Card title={t('Conjunto')}><EmptyState title={t('Selecciona un conjunto')} /></Card> : (
              <Card title={open.name} subtitle={t('{n} casos', { n: open.items.length })}>
                <div className="mb-3 flex gap-2">
                  <button type="button" onClick={() => void download(open)} className="rounded-lg border border-line bg-panel px-3 py-1.5 text-xs text-ink hover:bg-raised">{t('Exportar JSON')}</button>
                  <button type="button" onClick={() => void remove(open.id)} className="rounded-lg border border-bad/40 px-3 py-1.5 text-xs text-bad hover:bg-bad/10">{t('Eliminar conjunto')}</button>
                </div>
                {open.items.length === 0 ? <EmptyState title={t('Conjunto vacío')} /> : (
                  <ul className="divide-y divide-line">
                    {open.items.map((c) => (
                      <li key={c.id} className="flex flex-wrap items-center gap-2 py-2.5 text-xs">
                        {c.source_trace_id
                          ? <Link to={`/traces/${c.source_trace_id}`} className="font-mono text-accent hover:underline">{c.source_trace_id.slice(0, 8)}…</Link>
                          : <span className="text-faint">–</span>}
                        {c.intent && <Chip>{c.intent}</Chip>}
                        {c.checks.map((k) => <Chip key={k} tone="accent">{t(CHECK_LABEL[k] ?? k)}</Chip>)}
                        {c.label && <Chip tone={c.label === 'correct' ? 'accent' : 'warn'}>{c.label === 'correct' ? t('Correcta') : t('Incorrecta')}</Chip>}
                        {c.tags.map((g) => <span key={g} className="text-faint">#{g}</span>)}
                        <button type="button" onClick={() => void removeCase(c.id)} className="ml-auto text-faint hover:text-bad">{t('Quitar')}</button>
                      </li>
                    ))}
                  </ul>
                )}
              </Card>
            )}
            {open && <div className="mt-4"><RunsPanel datasetId={open.id} cases={open.items} /></div>}
          </div>
        </div>
      )}
    </div>
  );
}

export default Datasets;
