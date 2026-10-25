// Pure helpers for the global project selector. No imports: the Node test runner loads this file
// directly.

export const PROJECT_PARAM = 'project';
export const PROJECT_KEY = 'observer.project';
/** Used only while no project has sent a trace (the backend's own default). */
export const FALLBACK_PROJECT = 'manitos';

export interface ProjectLike {
  project_id: string;
  last_trace_at: string | null;
}

/** A project id as typed in a URL or found in storage, or "" when it is not usable. */
export function cleanProject(value: string | null | undefined): string {
  const v = (value ?? '').trim();
  return v.length > 0 && v.length <= 128 ? v : '';
}

/**
 * The project to show: the URL's (a shared link wins), then the stored choice while that project
 * still has traces, then the project with the most recent trace.
 */
export function pickProject(projects: ProjectLike[], fromUrl: string | null | undefined, stored: string | null | undefined): string {
  const url = cleanProject(fromUrl);
  if (url) return url;
  const kept = cleanProject(stored);
  if (kept && projects.some((p) => p.project_id === kept)) return kept;
  const newest = [...projects].sort((a, b) => (b.last_trace_at ?? '').localeCompare(a.last_trace_at ?? ''))[0];
  return newest?.project_id ?? (kept || FALLBACK_PROJECT);
}

/** Options for the selector: every project with traces, plus the current one if it has none yet. */
export function projectOptions(projects: ProjectLike[], current: string): string[] {
  const ids = projects.map((p) => p.project_id);
  return current && !ids.includes(current) ? [current, ...ids] : ids;
}
