import { useEffect, useState } from 'react';
import { api } from '../api';
import type { Span } from '../types';

interface TraceWaterfallProps {
  traceId: string;
  spans?: Span[];
}

function TraceWaterfall({ traceId, spans: spansProp }: TraceWaterfallProps) {
  const [spans, setSpans] = useState<Span[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (spansProp) {
      setSpans(spansProp);
      setLoading(false);
      return;
    }
    api.getTraceSpans(traceId).then(setSpans).catch(() => {}).finally(() => setLoading(false));
  }, [traceId, spansProp]);

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <div className="text-gray-400">Loading spans...</div>
      </div>
    );
  }

  if (spans.length === 0) {
    return (
      <div className="flex items-center justify-center h-64">
        <div className="text-gray-400">No spans found</div>
      </div>
    );
  }

  const toMs = (t: string | null | undefined) => {
    if (t == null) return Date.now();
    return new Date(t).getTime();
  };
  const spansMs = spans.map((s) => ({
    ...s,
    _start: toMs(s.start_time),
    _end: toMs(s.end_time ?? s.start_time),
  }));

  const minStart = Math.min(...spansMs.map((s) => s._start));
  const maxEnd = Math.max(...spansMs.map((s) => s._end));
  const totalDuration = maxEnd - minStart;

  return (
    <div className="bg-gray-800 rounded-lg p-4">
      <h3 className="text-lg font-semibold mb-4">Trace Waterfall</h3>
      <div className="space-y-2">
        {spansMs.map((span) => {
          const startOffset = span._start - minStart;
          const duration = span._end - span._start;
          const leftPercent = totalDuration > 0 ? (startOffset / totalDuration) * 100 : 0;
          const widthPercent = totalDuration > 0 ? (duration / totalDuration) * 100 : 100;

          return (
            <div key={span.id} className="flex items-center">
              <div className="w-1/3 text-sm text-gray-300 truncate pr-4">
                {span.name}
              </div>
              <div className="w-2/3 relative h-6">
                <div
                  className={`absolute h-full rounded ${
                    span.status === 'ok' ? 'bg-green-500' : 'bg-red-500'
                  }`}
                  style={{
                    left: `${leftPercent}%`,
                    width: `${Math.max(widthPercent, 1)}%`,
                  }}
                />
              </div>
              <div className="w-24 text-right text-xs text-gray-400 pl-2">
                {duration.toFixed(0)}ms
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

export default TraceWaterfall;
