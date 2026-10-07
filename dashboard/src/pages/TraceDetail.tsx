import { useState, useEffect } from 'react';
import { useParams, Link } from 'react-router-dom';
import { api } from '../api';
import type { Trace, Span } from '../types';
import TraceWaterfall from '../components/TraceWaterfall';
import { StatusBadge, InfoCard, formatDuration } from '../components/shared';

function TraceDetail() {
  const { traceId } = useParams<{ traceId: string }>();
  const [trace, setTrace] = useState<Trace | null>(null);
  const [spans, setSpans] = useState<Span[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selectedSpan, setSelectedSpan] = useState<Span | null>(null);

  useEffect(() => {
    const fetchData = async () => {
      if (!traceId) return;
      try {
        const [traceData, spansData] = await Promise.all([
          api.getTrace(traceId),
          api.getTraceSpans(traceId),
        ]);
        setTrace(traceData);
        setSpans(spansData);
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Unknown error');
      } finally {
        setLoading(false);
      }
    };
    fetchData();
  }, [traceId]);

  if (loading) {
    return (
      <div className="px-4 py-6 sm:px-0">
        <div className="flex items-center justify-center h-64">
          <div className="text-gray-400">Loading...</div>
        </div>
      </div>
    );
  }

  if (error || !trace) {
    return (
      <div className="px-4 py-6 sm:px-0">
        <div className="bg-red-900/20 border border-red-500 rounded-lg p-4">
          <p className="text-red-400">Error: {error || 'Trace not found'}</p>
          <Link to="/traces" className="text-blue-400 hover:text-blue-300 text-sm mt-2 inline-block">
            Back to traces
          </Link>
        </div>
      </div>
    );
  }

  return (
    <div className="px-4 py-6 sm:px-0">
      <div className="mb-6">
        <Link to="/traces" className="text-blue-400 hover:text-blue-300 text-sm">
          &larr; Back to traces
        </Link>
        <h1 className="text-2xl font-bold mt-2">{trace.name}</h1>
      </div>

      <div className="grid grid-cols-1 gap-5 sm:grid-cols-2 lg:grid-cols-4 mb-6">
        <InfoCard label="Status" value={trace.status} />
        <InfoCard label="Duration" value={formatDuration(trace.start_time, trace.end_time)} />
        <InfoCard label="Spans" value={spans.length.toString()} />
        <InfoCard label="Created" value={new Date(trace.created_at).toLocaleString()} />
      </div>

      {(trace.project_id || trace.environment || trace.service_instance_id || trace.session_id || trace.turn_id) && (
        <div className="mb-6 bg-gray-800 rounded-lg p-4">
          <h3 className="text-sm font-medium text-gray-400 mb-3">Runtime correlation</h3>
          <dl className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {[
              ['Project', trace.project_id],
              ['Environment', trace.environment],
              ['Service instance', trace.service_instance_id],
              ['Session', trace.session_id],
              ['Turn', trace.turn_id],
              ['Schema', trace.schema_version],
            ].filter(([, value]) => value).map(([label, value]) => (
              <div key={label}>
                <dt className="text-xs uppercase tracking-wide text-gray-500">{label}</dt>
                <dd className="mt-1 break-all text-sm text-white font-mono">{value}</dd>
              </div>
            ))}
          </dl>
        </div>
      )}

      {trace.metadata && Object.keys(trace.metadata).length > 0 && (
        <div className="mb-6 bg-gray-800 rounded-lg p-4">
          <h3 className="text-sm font-medium text-gray-400 mb-2">Metadata</h3>
          <pre className="bg-gray-900 rounded p-3 text-sm text-gray-300 overflow-x-auto">
            {JSON.stringify(trace.metadata, null, 2)}
          </pre>
        </div>
      )}

      <div className="mb-6">
        <TraceWaterfall traceId={traceId!} spans={spans} />
      </div>

      <div className="bg-gray-800 rounded-lg overflow-hidden">
        <div className="px-4 py-3 border-b border-gray-700">
          <h2 className="text-lg font-semibold">Spans</h2>
        </div>
        <table className="min-w-full divide-y divide-gray-700">
          <thead className="bg-gray-700">
            <tr>
              <th className="px-4 py-3 text-left text-xs font-medium text-gray-400 uppercase tracking-wider">Name</th>
              <th className="px-4 py-3 text-left text-xs font-medium text-gray-400 uppercase tracking-wider">Type</th>
              <th className="px-4 py-3 text-left text-xs font-medium text-gray-400 uppercase tracking-wider">Status</th>
              <th className="px-4 py-3 text-left text-xs font-medium text-gray-400 uppercase tracking-wider">Duration</th>
              <th className="px-4 py-3 text-left text-xs font-medium text-gray-400 uppercase tracking-wider">Tokens</th>
              <th className="px-4 py-3 text-left text-xs font-medium text-gray-400 uppercase tracking-wider">Cost</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-700">
            {spans.map((span) => (
              <tr
                key={span.id}
                className={`hover:bg-gray-700 cursor-pointer ${selectedSpan?.id === span.id ? 'bg-gray-700' : ''}`}
                onClick={() => setSelectedSpan(selectedSpan?.id === span.id ? null : span)}
              >
                <td className="px-4 py-3 text-sm text-white">{span.name}</td>
                <td className="px-4 py-3 text-sm text-gray-300">{span.span_type}</td>
                <td className="px-4 py-3"><StatusBadge status={span.status} /></td>
                <td className="px-4 py-3 text-sm text-gray-300">{formatDuration(span.start_time, span.end_time)}</td>
                <td className="px-4 py-3 text-sm text-gray-300">
                  {span.tokens_input != null && span.tokens_output != null
                    ? `${span.tokens_input} → ${span.tokens_output}`
                    : '-'}
                </td>
                <td className="px-4 py-3 text-sm text-gray-300">
                  {span.cost_usd != null ? `$${span.cost_usd.toFixed(6)}` : '-'}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {selectedSpan && (
        <div className="mt-6 bg-gray-800 rounded-lg p-4">
          <h3 className="text-lg font-semibold mb-4">Span Detail: {selectedSpan.name}</h3>
          <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
            <div>
              <h4 className="text-sm font-medium text-gray-400 mb-2">Input</h4>
              <pre className="bg-gray-900 rounded p-3 text-sm text-gray-300 overflow-x-auto">
                {selectedSpan.input ? JSON.stringify(selectedSpan.input, null, 2) : 'null'}
              </pre>
            </div>
            <div>
              <h4 className="text-sm font-medium text-gray-400 mb-2">Output</h4>
              <pre className="bg-gray-900 rounded p-3 text-sm text-gray-300 overflow-x-auto">
                {selectedSpan.output ? JSON.stringify(selectedSpan.output, null, 2) : 'null'}
              </pre>
            </div>
          </div>
          {selectedSpan.attributes && Object.keys(selectedSpan.attributes).length > 0 && (
            <div className="mt-4">
              <h4 className="text-sm font-medium text-gray-400 mb-2">Attributes</h4>
              <pre className="bg-gray-900 rounded p-3 text-sm text-gray-300 overflow-x-auto">
                {JSON.stringify(selectedSpan.attributes, null, 2)}
              </pre>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export default TraceDetail;
