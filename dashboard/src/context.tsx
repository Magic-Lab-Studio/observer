import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { api } from './api';
import { useWebSocket } from './hooks/useWebSocket';
import { parseApiDate } from './dates';
import type { Trace } from './types';

export const SAMPLE_LIMIT = 1000;

interface Ctx {
  hours: number;
  setHours: (h: number) => void;
  env: string;
  setEnv: (e: string) => void;
  environments: string[];
  sample: Trace[];
  windowTraces: Trace[];
  windowStart: number;
  windowEnd: number;
  sampleTruncated: boolean;
  totalTraces: number;
  loading: boolean;
  error: string | null;
  connected: boolean;
  lastUpdated: number | null;
  newIds: Set<string>;
  refresh: () => void;
}

const C = createContext<Ctx | null>(null);

export function useFilters(): Ctx {
  const v = useContext(C);
  if (!v) throw new Error('useFilters outside FilterProvider');
  return v;
}

export function FilterProvider({ children }: { children: ReactNode }) {
  const [hours, setHours] = useState(24);
  const [env, setEnv] = useState('');
  const [sample, setSample] = useState<Trace[]>([]);
  const [totalTraces, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [lastUpdated, setLastUpdated] = useState<number | null>(null);
  const [newIds, setNewIds] = useState<Set<string>>(new Set());
  const [tick, setTick] = useState(0);
  const known = useRef<Set<string>>(new Set());
  const timer = useRef<ReturnType<typeof setTimeout>>();

  const load = useCallback(async () => {
    try {
      const data = await api.listTraces({ limit: SAMPLE_LIMIT });
      const fresh = new Set<string>();
      if (known.current.size > 0) for (const t of data.traces) if (!known.current.has(t.id)) fresh.add(t.id);
      known.current = new Set(data.traces.map((t) => t.id));
      setNewIds(fresh);
      setSample(data.traces);
      setTotal(data.total);
      setError(null);
      setLastUpdated(Date.now());
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Error desconocido');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { void load(); }, [load]);
  useEffect(() => {
    const id = setInterval(() => setTick((n) => n + 1), 30_000);
    return () => clearInterval(id);
  }, []);

  const { isConnected } = useWebSocket({
    onMessage: useCallback((m: { type: string }) => {
      if (m.type === 'new_trace' || m.type === 'new_evaluation') {
        clearTimeout(timer.current);
        timer.current = setTimeout(() => void load(), 1200);
      }
    }, [load]),
  });

  const environments = useMemo(
    () => Array.from(new Set(sample.map((t) => t.environment).filter((e): e is string => !!e))).sort(),
    [sample],
  );

  const value = useMemo<Ctx>(() => {
    const now = Date.now();
    const requested = now - hours * 3600_000;
    const scoped = env ? sample.filter((t) => t.environment === env) : sample;
    const truncated = sample.length >= SAMPLE_LIMIT && totalTraces > sample.length;
    const oldest = sample.length ? parseApiDate(sample[sample.length - 1].created_at).getTime() : requested;
    const windowStart = truncated ? Math.max(requested, oldest) : requested;
    const windowTraces = scoped.filter((t) => parseApiDate(t.created_at).getTime() >= windowStart);
    return {
      hours, setHours, env, setEnv, environments, sample: scoped, windowTraces, windowStart, windowEnd: now,
      sampleTruncated: truncated && oldest > requested, totalTraces, loading, error,
      connected: isConnected, lastUpdated, newIds, refresh: () => void load(),
    };
    // tick re-evaluates "now" so the window slides
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [hours, env, sample, environments, totalTraces, loading, error, isConnected, lastUpdated, newIds, load, tick]);

  return <C.Provider value={value}>{children}</C.Provider>;
}
