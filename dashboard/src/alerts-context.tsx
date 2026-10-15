import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { api } from './api';
import { useWebSocket } from './hooks/useWebSocket';
import { notificationBody } from './helpers/alerts';
import { t } from './i18n';
import type { AlertEvent, AlertMetric } from './types';

const PREF_KEY = 'observer.desktopNotifications';

interface Ctx {
  open: AlertEvent[];
  openCount: number;
  metrics: AlertMetric[];
  refresh: () => void;
  notifySupported: boolean;
  notifyEnabled: boolean;
  setNotifyEnabled: (on: boolean) => Promise<void>;
}

const C = createContext<Ctx | null>(null);

export function useAlerts(): Ctx {
  const v = useContext(C);
  if (!v) throw new Error('useAlerts outside AlertsProvider');
  return v;
}

function readPref(): boolean {
  try {
    return localStorage.getItem(PREF_KEY) === '1';
  } catch {
    return false;
  }
}

function writePref(on: boolean): void {
  try {
    localStorage.setItem(PREF_KEY, on ? '1' : '0');
  } catch {
    /* storage may be blocked: the preference just does not persist */
  }
}

const supported = typeof window !== 'undefined' && 'Notification' in window;

export function AlertsProvider({ children }: { children: ReactNode }) {
  const [open, setOpen] = useState<AlertEvent[]>([]);
  const [metrics, setMetrics] = useState<AlertMetric[]>([]);
  const [notifyEnabled, setEnabled] = useState<boolean>(readPref);
  const seen = useRef<Set<string>>(new Set());
  const metricsRef = useRef<AlertMetric[]>([]);
  const enabledRef = useRef(notifyEnabled);
  enabledRef.current = notifyEnabled;

  // Alerts are secondary: every failure here is silent (no banner of errors on other pages).
  const refresh = useCallback(() => {
    api.listAlertEvents('open').then((d) => {
      d.events.forEach((e) => seen.current.add(e.id));
      setOpen(d.events);
    }).catch(() => undefined);
  }, []);

  useEffect(() => {
    refresh();
    api.getAlertMetrics().then((m) => { setMetrics(m); metricsRef.current = m; }).catch(() => undefined);
  }, [refresh]);

  const notify = useCallback((e: AlertEvent) => {
    if (!supported || !enabledRef.current || Notification.permission !== 'granted') return;
    try {
      const unit = metricsRef.current.find((m) => m.id === e.metric);
      new Notification(`${t('Observatorio')} · ${t(e.rule_name)}`, {
        body: notificationBody(e, unit),
        tag: e.id,
      });
    } catch {
      /* some browsers refuse constructed notifications: the banner still shows it */
    }
  }, []);

  useWebSocket({
    onMessage: useCallback((m: { type: string; data?: unknown }) => {
      if (m.type !== 'alert_event') return;
      const e = m.data as AlertEvent;
      setOpen((prev) => {
        const rest = prev.filter((x) => x.id !== e.id);
        return e.state === 'open' ? [e, ...rest] : rest;
      });
      if (e.state === 'open' && !seen.current.has(e.id)) {
        seen.current.add(e.id);
        notify(e);
      }
    }, [notify]),
  });

  const setNotifyEnabled = useCallback(async (on: boolean) => {
    if (on && supported && Notification.permission === 'default') {
      try {
        await Notification.requestPermission();
      } catch {
        /* ignored: treated as denied below */
      }
    }
    const granted = !on || (supported && Notification.permission === 'granted');
    const next = on && granted;
    writePref(next);
    setEnabled(next);
  }, []);

  const value = useMemo<Ctx>(() => ({
    open, openCount: open.length, metrics, refresh,
    notifySupported: supported, notifyEnabled, setNotifyEnabled,
  }), [open, metrics, refresh, notifyEnabled, setNotifyEnabled]);

  return <C.Provider value={value}>{children}</C.Provider>;
}
