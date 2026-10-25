import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { useSearchParams } from 'react-router-dom';
import { api } from './api';
import { useWebSocket } from './hooks/useWebSocket';
import { parseApiDate } from './dates';
import { PROJECT_KEY, PROJECT_PARAM, cleanProject, pickProject } from './helpers/projects';
import { t } from './i18n';
import type { ProjectInfo, Trace } from './types';

export const SAMPLE_LIMIT = 1000;

interface Ctx {
  hours: number;
  setHours: (h: number) => void;
  env: string;
  setEnv: (e: string) => void;
  environments: string[];
  /** Project of Explore, Alerts and Evaluations; "" until the project list has loaded. */
  project: string;
  setProject: (p: string) => void;
  projects: ProjectInfo[];
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

function readStoredProject(): string {
  try {
    return cleanProject(localStorage.getItem(PROJECT_KEY));
  } catch {
    return '';
  }
}

function writeStoredProject(p: string): void {
  try {
    localStorage.setItem(PROJECT_KEY, p);
  } catch {
    /* storage may be blocked: the choice then lives only in the URL */
  }
}

export function FilterProvider({ children }: { children: ReactNode }) {
  const [hours, setHours] = useState(24);
  const [env, setEnv] = useState('');
  const [params, setParams] = useSearchParams();
  const urlProject = cleanProject(params.get(PROJECT_PARAM));
  const [storedProject, setStoredProject] = useState(readStoredProject);
  const [projects, setProjects] = useState<ProjectInfo[] | null>(null);
  const [sample, setSample] = useState<Trace[]>([]);
  const [totalTraces, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [lastUpdated, setLastUpdated] = useState<number | null>(null);
  const [newIds, setNewIds] = useState<Set<string>>(new Set());
  const [tick, setTick] = useState(0);
  const known = useRef<Set<string>>(new Set());
  const timer = useRef<ReturnType<typeof setTimeout>>();

  const loadProjects = useCallback(() => {
    api.listProjects().then(setProjects).catch(() => setProjects((prev) => prev ?? []));
  }, []);

  const load = useCallback(async () => {
    loadProjects();
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
      setError(e instanceof Error ? e.message : t('Error desconocido'));
    } finally {
      setLoading(false);
    }
  }, [loadProjects]);

  useEffect(() => { void load(); }, [load]);

  // A project named in the URL (a shared link) becomes the remembered choice too.
  useEffect(() => {
    if (urlProject && urlProject !== storedProject) {
      writeStoredProject(urlProject);
      setStoredProject(urlProject);
    }
  }, [urlProject, storedProject]);

  const project = projects === null ? '' : pickProject(projects, urlProject, storedProject);
  const setProject = useCallback((p: string) => {
    const next = cleanProject(p);
    if (!next) return;
    writeStoredProject(next);
    setStoredProject(next);
    setParams((prev) => {
      const q = new URLSearchParams(prev);
      q.set(PROJECT_PARAM, next);
      return q;
    }, { replace: true });
  }, [setParams]);
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
    const oldest = sample.length ? Math.min(...sample.map((t) => parseApiDate(t.start_time).getTime())) : requested;
    const windowStart = truncated ? Math.max(requested, oldest) : requested;
    const windowTraces = scoped.filter((t) => parseApiDate(t.start_time).getTime() >= windowStart);
    return {
      hours, setHours, env, setEnv, environments, project, setProject, projects: projects ?? [], sample: scoped, windowTraces, windowStart, windowEnd: now,
      sampleTruncated: truncated && oldest > requested, totalTraces, loading, error,
      connected: isConnected, lastUpdated, newIds, refresh: () => void load(),
    };
    // tick re-evaluates "now" so the window slides
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [hours, env, project, setProject, projects, sample, environments, totalTraces, loading, error, isConnected, lastUpdated, newIds, load, tick]);

  return <C.Provider value={value}>{children}</C.Provider>;
}
