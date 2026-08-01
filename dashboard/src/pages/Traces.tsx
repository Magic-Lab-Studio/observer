import { useState, useEffect, useCallback, useRef } from 'react';
import { Link } from 'react-router-dom';
import { api } from '../api';
import type { Trace } from '../types';
import { StatusBadge, formatDuration, formatTimeAgo } from '../components/shared';

function Traces() {
  const [traces, setTraces] = useState<Trace[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [page, setPage] = useState(0);
  const [hasMore, setHasMore] = useState(true);
  const [total, setTotal] = useState(0);
  const [search, setSearch] = useState('');
  const [statusFilter, setStatusFilter] = useState('');
  const [projectFilter, setProjectFilter] = useState('');
  const [environmentFilter, setEnvironmentFilter] = useState('');
  const [sessionFilter, setSessionFilter] = useState('');
  const [debouncedSearch, setDebouncedSearch] = useState('');
  const limit = 20;

  useEffect(() => {
    const timer = setTimeout(() => setDebouncedSearch(search), 300);
    return () => clearTimeout(timer);
  }, [search]);

  const fetchTraces = useCallback(async (offset: number) => {
    setLoading(true);
    try {
      const data = await api.listTraces({
        limit,
        offset,
        status: statusFilter || undefined,
        search: debouncedSearch || undefined,
        project_id: projectFilter.trim() || undefined,
        environment: environmentFilter.trim() || undefined,
        session_id: sessionFilter.trim() || undefined,
      });
      setTraces(data.traces);
      setTotal(data.total);
      setHasMore(offset + data.traces.length < data.total);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unknown error');
    } finally {
      setLoading(false);
    }
  }, [statusFilter, debouncedSearch, projectFilter, environmentFilter, sessionFilter]);

  useEffect(() => {
    setPage(0);
    fetchTraces(0);
  }, [fetchTraces]);

  const prevPageRef = useRef(page);
  useEffect(() => {
    if (prevPageRef.current !== page) {
      prevPageRef.current = page;
      fetchTraces(page * limit);
    }
  }, [page, fetchTraces]);

  return (
    <div className="px-4 py-6 sm:px-0">
      <h1 className="text-2xl font-bold mb-6">Traces</h1>

      <div className="bg-gray-800 rounded-lg p-4 mb-6">
        <div className="flex flex-wrap gap-4">
          <input
            type="text"
            placeholder="Search traces..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="bg-gray-700 text-white px-4 py-2 rounded-md flex-1 min-w-[200px]"
          />
          <select
            value={statusFilter}
            onChange={(e) => setStatusFilter(e.target.value)}
            className="bg-gray-700 text-white px-4 py-2 rounded-md"
          >
            <option value="">All Statuses</option>
            <option value="ok">OK</option>
            <option value="error">Error</option>
          </select>
          <input
            type="text"
            aria-label="Project filter"
            placeholder="Project (for example my-app)"
            value={projectFilter}
            onChange={(e) => setProjectFilter(e.target.value)}
            className="bg-gray-700 text-white px-4 py-2 rounded-md min-w-[220px]"
          />
          <input
            type="text"
            aria-label="Environment filter"
            placeholder="Environment"
            value={environmentFilter}
            onChange={(e) => setEnvironmentFilter(e.target.value)}
            className="bg-gray-700 text-white px-4 py-2 rounded-md min-w-[160px]"
          />
          <input
            type="text"
            aria-label="Session filter"
            placeholder="Session ID"
            value={sessionFilter}
            onChange={(e) => setSessionFilter(e.target.value)}
            className="bg-gray-700 text-white px-4 py-2 rounded-md min-w-[220px]"
          />
        </div>
      </div>

      {error ? (
        <div className="bg-red-900/20 border border-red-500 rounded-lg p-4">
          <p className="text-red-400">Error: {error}</p>
          <p className="text-gray-500 text-sm mt-2">Make sure the backend is running</p>
        </div>
      ) : (
        <div className="bg-gray-800 rounded-lg overflow-hidden">
          <table className="min-w-full divide-y divide-gray-700">
            <thead className="bg-gray-700">
              <tr>
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-400 uppercase tracking-wider">Name</th>
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-400 uppercase tracking-wider">Project</th>
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-400 uppercase tracking-wider">Session</th>
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-400 uppercase tracking-wider">Status</th>
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-400 uppercase tracking-wider">Duration</th>
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-400 uppercase tracking-wider">Created</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-700">
              {loading ? (
                <tr><td colSpan={6} className="px-6 py-8 text-center text-gray-400">Loading...</td></tr>
              ) : traces.length === 0 ? (
                <tr><td colSpan={6} className="px-6 py-8 text-center text-gray-400">No traces found</td></tr>
              ) : (
                traces.map((trace) => (
                  <tr key={trace.id} className="hover:bg-gray-700">
                    <td className="px-6 py-4 whitespace-nowrap text-sm text-white">
                      <Link to={`/traces/${trace.id}`} className="hover:text-blue-400">{trace.name}</Link>
                    </td>
                    <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-300">
                      <div>{trace.project_id || '-'}</div>
                      {trace.environment && <div className="text-xs text-gray-500">{trace.environment}</div>}
                    </td>
                    <td className="px-6 py-4 max-w-[240px] truncate text-sm text-gray-300 font-mono" title={trace.session_id || undefined}>
                      {trace.session_id || '-'}
                    </td>
                    <td className="px-6 py-4 whitespace-nowrap">
                      <StatusBadge status={trace.status} />
                    </td>
                    <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-300">
                      {formatDuration(trace.start_time, trace.end_time)}
                    </td>
                    <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-300">
                      {formatTimeAgo(trace.created_at)}
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      )}

      <div className="mt-4 flex items-center justify-between">
        <div className="text-sm text-gray-400">
          Page {page + 1} of {Math.max(1, Math.ceil(total / limit))} ({total} traces total)
        </div>
        <div className="flex gap-2">
          <button
            onClick={() => setPage((p) => Math.max(0, p - 1))}
            disabled={page === 0}
            className="px-3 py-1 bg-gray-700 text-white rounded-md hover:bg-gray-600 disabled:opacity-50 disabled:cursor-not-allowed"
          >
            Previous
          </button>
          <button
            onClick={() => setPage((p) => p + 1)}
            disabled={!hasMore}
            className="px-3 py-1 bg-gray-700 text-white rounded-md hover:bg-gray-600 disabled:opacity-50 disabled:cursor-not-allowed"
          >
            Next
          </button>
        </div>
      </div>
    </div>
  );
}

export default Traces;
