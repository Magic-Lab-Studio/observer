import { useState, useEffect, useCallback } from 'react';
import {
  LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer,
  BarChart, Bar,
} from 'recharts';
import { useWebSocket } from '../hooks/useWebSocket';
import { api } from '../api';
import type { SummaryData, TimelinePoint, CostByModel, ManitOSQualitySummary } from '../types';
import { StatCard } from '../components/shared';

const TIME_RANGES = [
  { label: '1h', hours: 1 },
  { label: '6h', hours: 6 },
  { label: '24h', hours: 24 },
  { label: '7d', hours: 168 },
  { label: '30d', hours: 720 },
];

function Overview() {
  const [summary, setSummary] = useState<SummaryData | null>(null);
  const [timeline, setTimeline] = useState<TimelinePoint[]>([]);
  const [costByModel, setCostByModel] = useState<CostByModel[]>([]);
  const [quality, setQuality] = useState<ManitOSQualitySummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [hours, setHours] = useState(168);

  const fetchData = useCallback(async () => {
    try {
      const [summaryData, timelineData, costData, qualityData] = await Promise.all([
        api.getSummary(hours),
        api.getTimeline(hours, hours <= 6 ? 30 : hours <= 24 ? 60 : 360),
        api.getCostByModel(hours),
        api.getManitOSQuality(hours),
      ]);
      setSummary(summaryData);
      setTimeline(timelineData);
      setCostByModel(costData);
      setQuality(qualityData);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unknown error');
    } finally {
      setLoading(false);
    }
  }, [hours]);

  useEffect(() => {
    setLoading(true);
    fetchData();
  }, [fetchData]);

  useWebSocket({
    onMessage: useCallback((msg: { type: string }) => {
      if (msg.type === 'new_trace' || msg.type === 'new_evaluation') {
        fetchData();
      }
    }, [fetchData]),
  });

  if (loading) {
    return (
      <div className="px-4 py-6 sm:px-0">
        <div className="flex items-center justify-center h-64">
          <div className="text-gray-400">Loading...</div>
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="px-4 py-6 sm:px-0">
        <div className="bg-red-900/20 border border-red-500 rounded-lg p-4">
          <p className="text-red-400">Error: {error}</p>
          <p className="text-gray-500 text-sm mt-2">Make sure the backend is running</p>
        </div>
      </div>
    );
  }

  return (
    <div className="px-4 py-6 sm:px-0">
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-2xl font-bold">Dashboard Overview</h1>
        <div className="flex gap-1">
          {TIME_RANGES.map((r) => (
            <button
              key={r.hours}
              onClick={() => setHours(r.hours)}
              className={`px-3 py-1 rounded text-sm ${
                hours === r.hours
                  ? 'bg-indigo-600 text-white'
                  : 'bg-gray-700 text-gray-300 hover:bg-gray-600'
              }`}
            >
              {r.label}
            </button>
          ))}
        </div>
      </div>

      <div className="grid grid-cols-1 gap-5 sm:grid-cols-2 lg:grid-cols-4">
        <StatCard title="Total Traces" value={summary?.total_traces.toLocaleString() ?? '0'} />
        <StatCard title="Total Spans" value={summary?.total_spans.toLocaleString() ?? '0'} />
        <StatCard title="Total Cost" value={`$${(summary?.total_cost_usd ?? 0).toFixed(4)}`} />
        <StatCard title="Avg Latency" value={`${Math.round(summary?.avg_latency_ms ?? 0)}ms`} />
      </div>

      {summary && summary.error_count > 0 && (
        <div className="mt-4 bg-red-900/20 border border-red-500 rounded-lg p-4">
          <p className="text-red-400">
            {summary.error_count} error{summary.error_count !== 1 ? 's' : ''}{' '}
            ({(summary.error_rate * 100).toFixed(1)}% error rate)
          </p>
        </div>
      )}

      {summary && summary.total_evaluations > 0 && (
        <div className="mt-4 bg-gray-800 border border-gray-700 rounded-lg p-4">
          <p className="text-gray-300">
            {summary.total_evaluations} evaluation{summary.total_evaluations !== 1 ? 's' : ''}{' '}
            &middot; avg score: {(summary.avg_evaluation_score ?? 0).toFixed(2)}
          </p>
        </div>
      )}

      <section className="mt-8" aria-labelledby="runtime-quality-heading">
        <div className="flex items-baseline justify-between mb-4">
          <h2 id="runtime-quality-heading" className="text-xl font-semibold">Runtime Quality</h2>
          <span className="text-sm text-gray-500">metadata only</span>
        </div>
        <div className="grid grid-cols-1 gap-5 sm:grid-cols-2 lg:grid-cols-4">
          <StatCard title="Turns" value={quality?.total_turns.toLocaleString() ?? '0'} />
          <StatCard title="Error Rate" value={`${((quality?.error_rate ?? 0) * 100).toFixed(1)}%`} />
          <StatCard title="Degraded Rate" value={`${((quality?.degraded_rate ?? 0) * 100).toFixed(1)}%`} />
          <StatCard title="Fallback Rate" value={`${((quality?.fallback_rate ?? 0) * 100).toFixed(1)}%`} />
          <StatCard title="Avg Turn Latency" value={`${Math.round(quality?.avg_duration_ms ?? 0)}ms`} />
          <StatCard title="Avg TTFT" value={`${Math.round(quality?.avg_ttft_ms ?? 0)}ms`} />
          <StatCard title="Truncated Rate" value={`${((quality?.truncated_rate ?? 0) * 100).toFixed(1)}%`} />
          <StatCard title="Tool Error Rate" value={`${((quality?.tool_error_rate ?? 0) * 100).toFixed(1)}%`} />
          <StatCard title="TTS Error Rate" value={`${((quality?.tts_error_rate ?? 0) * 100).toFixed(1)}%`} />
        </div>

        <div className="mt-5 grid grid-cols-1 gap-5 lg:grid-cols-2">
          {[
            { title: 'Models', values: quality?.models ?? [] },
            { title: 'Languages', values: quality?.languages ?? [] },
          ].map((group) => (
            <div key={group.title} className="bg-gray-800 rounded-lg p-6">
              <h3 className="text-lg font-semibold mb-4">{group.title}</h3>
              {group.values.length > 0 ? (
                <div className="space-y-3">
                  {group.values.map((item) => (
                    <div key={item.key} className="flex items-center justify-between text-sm">
                      <span className="text-gray-200">{item.key}</span>
                      <span className="text-gray-400">
                        {item.count} turn{item.count === 1 ? '' : 's'} · {(item.rate * 100).toFixed(1)}%
                      </span>
                    </div>
                  ))}
                </div>
              ) : (
                <div className="text-gray-500">No runtime quality data yet</div>
              )}
            </div>
          ))}
        </div>
      </section>

      <div className="mt-8 grid grid-cols-1 gap-5 lg:grid-cols-2">
        <div className="bg-gray-800 rounded-lg p-6">
          <h2 className="text-lg font-semibold mb-4">Cost Over Time</h2>
          {timeline.length > 0 ? (
            <ResponsiveContainer width="100%" height={256}>
              <LineChart data={timeline}>
                <CartesianGrid strokeDasharray="3 3" stroke="#374151" />
                <XAxis
                  dataKey="timestamp"
                  stroke="#9CA3AF"
                  tickFormatter={(ts) => {
                    const d = new Date(ts);
                    return `${d.getMonth() + 1}/${d.getDate()} ${d.getHours()}:00`;
                  }}
                />
                <YAxis stroke="#9CA3AF" />
                <Tooltip
                  contentStyle={{ backgroundColor: '#1F2937', border: '1px solid #374151' }}
                  labelStyle={{ color: '#F3F4F6' }}
                  formatter={(value: number) => [`$${value.toFixed(4)}`, 'Cost']}
                  labelFormatter={(ts) => new Date(ts).toLocaleString()}
                />
                <Line type="monotone" dataKey="cost_usd" stroke="#10B981" strokeWidth={2} dot={false} />
              </LineChart>
            </ResponsiveContainer>
          ) : (
            <div className="h-64 flex items-center justify-center text-gray-500">
              No data yet
            </div>
          )}
        </div>

        <div className="bg-gray-800 rounded-lg p-6">
          <h2 className="text-lg font-semibold mb-4">Cost by Model</h2>
          {costByModel.length > 0 ? (
            <ResponsiveContainer width="100%" height={256}>
              <BarChart data={costByModel}>
                <CartesianGrid strokeDasharray="3 3" stroke="#374151" />
                <XAxis dataKey="model" stroke="#9CA3AF" />
                <YAxis stroke="#9CA3AF" />
                <Tooltip
                  contentStyle={{ backgroundColor: '#1F2937', border: '1px solid #374151' }}
                  labelStyle={{ color: '#F3F4F6' }}
                  formatter={(value: number) => [`$${value.toFixed(4)}`, 'Cost']}
                />
                <Bar dataKey="cost_usd" fill="#6366F1" />
              </BarChart>
            </ResponsiveContainer>
          ) : (
            <div className="h-64 flex items-center justify-center text-gray-500">
              No data yet
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

export default Overview;
