import { lazy, Suspense, useEffect, useState } from 'react';
import { Routes, Route, NavLink, useLocation } from 'react-router-dom';
import { ErrorBoundary } from './components/ErrorBoundary';
import { FilterProvider, useFilters } from './context';
import { AlertsProvider, useAlerts } from './alerts-context';
import { LocaleProvider, useLocale } from './i18n-context';
import { LANGS } from './i18n';
import { SEVERITY_LABEL, severityRank } from './helpers/alerts';
import { Icon, Segmented } from './components/ui';
import { clockTime } from './helpers/format';
import { t } from './i18n';

const Overview = lazy(() => import('./pages/Overview'));
const Traces = lazy(() => import('./pages/Traces'));
const TraceDetail = lazy(() => import('./pages/TraceDetail'));
const Evaluations = lazy(() => import('./pages/Evaluations'));
const Providers = lazy(() => import('./pages/Providers'));
const Alerts = lazy(() => import('./pages/Alerts'));

const NAV = [
  { to: '/', label: 'Resumen', icon: 'overview', end: true },
  { to: '/traces', label: 'Trazas', icon: 'traces', end: false },
  { to: '/providers', label: 'Proveedores', icon: 'providers', end: false },
  { to: '/alerts', label: 'Alertas', icon: 'bell', end: false },
  { to: '/evaluations', label: 'Evaluaciones', icon: 'evals', end: false },
];

const RANGES = [
  { label: '1 h', value: 1 },
  { label: '6 h', value: 6 },
  { label: '24 h', value: 24 },
  { label: '7 d', value: 168 },
  { label: '30 d', value: 720 },
];

const navClass = ({ isActive }: { isActive: boolean }) =>
  `flex items-center gap-2.5 rounded-lg px-3 py-2 text-[13px] font-medium transition-colors ${
    isActive ? 'bg-raised text-ink' : 'text-dim hover:bg-panel-2 hover:text-ink'
  }`;

function AlertBadge() {
  const { openCount } = useAlerts();
  if (openCount === 0) return null;
  return <span className="ml-auto rounded-full bg-bad px-1.5 text-[10px] font-semibold text-canvas" aria-label={t('{n} alertas abiertas', { n: openCount })}>{openCount}</span>;
}

function AlertBanner() {
  const { open } = useAlerts();
  if (open.length === 0) return null;
  const top = [...open].sort((a, b) => severityRank(a.severity) - severityRank(b.severity))[0];
  const tone = top.severity === 'critical' ? 'border-bad/40 bg-bad/10 text-bad'
    : top.severity === 'warning' ? 'border-warn/40 bg-warn/10 text-warn' : 'border-accent/40 bg-accent/10 text-accent';
  return (
    <div role="status" className={`flex flex-wrap items-center gap-x-3 gap-y-1 border-b px-4 py-2 text-xs lg:px-6 ${tone}`}>
      <Icon name="bell" className="size-4 shrink-0" />
      <span className="font-semibold">{t(SEVERITY_LABEL[top.severity])}</span>
      <span className="min-w-0 truncate">{t(top.rule_name)}{open.length > 1 ? ` ${t('y {n} más', { n: open.length - 1 })}` : ''}</span>
      <NavLink to="/alerts" className="ml-auto underline">{t("Ver alertas")}</NavLink>
    </div>
  );
}

function LiveBadge() {
  const { connected, lastUpdated } = useFilters();
  return (
    <span className="inline-flex items-center gap-2 text-xs text-dim" title={connected ? t('Conectado al flujo en vivo') : t('Sin flujo en vivo; reintentando')}>
      <span className={`size-2 rounded-full ${connected ? 'bg-ok live-dot' : 'bg-warn'}`} />
      <span className="hidden sm:inline">{connected ? t('En vivo') : t('Reconectando…')}</span>
      {lastUpdated && <span className="hidden md:inline text-faint">· {clockTime(lastUpdated)}</span>}
    </span>
  );
}

function Header({ onMenu }: { onMenu: () => void }) {
  const { hours, setHours, env, setEnv, environments, refresh, loading } = useFilters();
  const { lang, setLang } = useLocale();
  const { pathname } = useLocation();
  const showFilters = pathname === '/' || pathname === '/providers';
  return (
    <header className="sticky top-0 z-20 flex min-h-14 flex-wrap items-center gap-x-3 gap-y-2 border-b py-2 border-line bg-canvas/85 px-4 backdrop-blur lg:px-6">
      <button type="button" onClick={onMenu} aria-label={t("Abrir menú")}
        className="lg:hidden rounded-md p-1.5 text-dim hover:bg-panel-2 hover:text-ink">
        <Icon name="menu" className="size-5" />
      </button>
      <div className="flex-1 max-sm:hidden" />
      {showFilters && (
        <>
          {environments.length > 0 && (
            <select aria-label={t("Entorno")} value={env} onChange={(e) => setEnv(e.target.value)}
              className="rounded-lg border border-line bg-panel px-2.5 py-1.5 text-xs text-ink">
              <option value="">{t("Todos los entornos")}</option>
              {environments.map((e) => <option key={e} value={e}>{e}</option>)}
            </select>
          )}
          <Segmented label={t("Rango de tiempo")} options={RANGES} value={hours} onChange={setHours} />
        </>
      )}
      <Segmented label={t('Idioma')} options={LANGS} value={lang} onChange={setLang} />
      <LiveBadge />
      <button type="button" onClick={refresh} aria-label={t("Actualizar")}
        className="rounded-md p-1.5 text-dim hover:bg-panel-2 hover:text-ink">
        <Icon name="refresh" className={`size-4 ${loading ? 'animate-spin' : ''}`} />
      </button>
    </header>
  );
}

function Sidebar({ open, onClose }: { open: boolean; onClose: () => void }) {
  return (
    <>
      {open && <div className="fixed inset-0 z-30 bg-black/60 lg:hidden" onClick={onClose} aria-hidden="true" />}
      <aside className={`fixed inset-y-0 left-0 z-40 flex w-56 flex-col border-r border-line bg-panel transition-transform lg:translate-x-0 ${
        open ? 'translate-x-0' : '-translate-x-full'
      }`}>
        <div className="flex h-14 items-center gap-2.5 px-5 border-b border-line">
          <span className="grid size-7 place-items-center rounded-lg bg-accent/20 text-accent">
            <svg viewBox="0 0 24 24" className="size-4" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
              <path d="M3 12h4l3-8 4 16 3-8h4" />
            </svg>
          </span>
          <span className="text-sm font-semibold tracking-tight">{t("Observatorio")}</span>
        </div>
        <nav className="flex-1 space-y-1 p-3" aria-label={t("Principal")}>
          {NAV.map((n) => (
            <NavLink key={n.to} to={n.to} end={n.end} className={navClass} onClick={onClose}>
              <Icon name={n.icon} />{t(n.label)}
              {n.to === '/alerts' && <AlertBadge />}
            </NavLink>
          ))}
        </nav>
        <div className="border-t border-line p-4 text-[11px] text-faint">
          
          {t("Solo metadatos · datos locales")}
        </div>
      </aside>
    </>
  );
}

function Shell() {
  const [open, setOpen] = useState(false);
  const { pathname } = useLocation();
  useEffect(() => { setOpen(false); }, [pathname]);
  return (
    <div className="min-h-full">
      <Sidebar open={open} onClose={() => setOpen(false)} />
      <div className="lg:pl-56 min-w-0">
        <Header onMenu={() => setOpen(true)} />
        <AlertBanner />
        <main className="mx-auto w-full max-w-[1800px] p-4 lg:p-6">
          <ErrorBoundary>
            <Suspense fallback={<div className="py-20 text-center text-sm text-faint">{t("Cargando…")}</div>}>
              <Routes>
                <Route path="/" element={<Overview />} />
                <Route path="/traces" element={<Traces />} />
                <Route path="/traces/:traceId" element={<TraceDetail />} />
                <Route path="/providers" element={<Providers />} />
                <Route path="/alerts" element={<Alerts />} />
                <Route path="/evaluations" element={<Evaluations />} />
                <Route path="*" element={
                  <div className="py-24 text-center">
                    <h1 className="text-3xl font-semibold">404</h1>
                    <p className="mt-2 text-dim">{t("Página no encontrada")}</p>
                  </div>
                } />
              </Routes>
            </Suspense>
          </ErrorBoundary>
        </main>
      </div>
    </div>
  );
}

export default function App() {
  return (
    <LocaleProvider>
      <FilterProvider>
        <AlertsProvider>
          <Shell />
        </AlertsProvider>
      </FilterProvider>
    </LocaleProvider>
  );
}
