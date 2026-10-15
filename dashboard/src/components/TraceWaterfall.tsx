import { useEffect, useMemo, useState } from 'react';
import { api } from '../api';
import type { Span } from '../types';
import { parseApiDate } from '../dates';
import { fmtMs } from '../helpers/format';
import { Card, EmptyState } from './ui';
import { t } from '../i18n';

interface TraceWaterfallProps {
  traceId: string;
  spans?: Span[];
  selectedId?: string | null;
  onSelect?: (span: Span) => void;
}

const TYPE_COLOR: Record<string, string> = {
  turn: '#6c8cff', llm: '#9b7bea', tool: '#e5a33b', event: '#4fb7d6', retrieval: '#3fb68b',
};

function depthOf(span: Span, byId: Map<string, Span>): number {
  let d = 0;
  let cur = span;
  while (cur.parent_id && byId.has(cur.parent_id) && d < 12) { cur = byId.get(cur.parent_id)!; d += 1; }
  return d;
}

function TraceWaterfall({ traceId, spans: spansProp, selectedId, onSelect }: TraceWaterfallProps) {
  const [fetched, setFetched] = useState<Span[]>([]);
  const spans = spansProp ?? fetched;

  useEffect(() => {
    if (spansProp) return;
    api.getTraceSpans(traceId).then(setFetched).catch(() => {});
  }, [traceId, spansProp]);

  const rows = useMemo(() => {
    const byId = new Map(spans.map((s) => [s.id, s]));
    const toMs = (t: string | null | undefined, fallback: number) => (t == null ? fallback : parseApiDate(t).getTime());
    const base = spans.map((s) => {
      const start = toMs(s.start_time, Date.now());
      return { span: s, start, end: toMs(s.end_time, start), depth: depthOf(s, byId) };
    }).sort((a, b) => a.start - b.start);
    const min = base.length ? Math.min(...base.map((r) => r.start)) : 0;
    const max = base.length ? Math.max(...base.map((r) => r.end)) : 0;
    return { base, min, total: Math.max(1, max - min) };
  }, [spans]);

  if (spans.length === 0) {
    return <Card title={t("Cascada")}><EmptyState title={t("Esta traza no tiene spans")} /></Card>;
  }

  const ticks = [0, 0.25, 0.5, 0.75, 1];

  return (
    <Card title={t("Cascada")} subtitle={t('{n} spans · {v} en total', { n: spans.length, v: fmtMs(rows.total) })} bodyClass="p-0 pt-2">
      <div className="overflow-x-auto">
        <div className="min-w-[640px]">
          <div className="relative ml-[240px] mr-[84px] h-6 border-b border-line">
            {ticks.map((t) => (
              <span key={t} className="absolute top-1 whitespace-nowrap -translate-x-1/2 text-[10px] text-faint first:translate-x-0 last:-translate-x-full"
                style={{ left: `${t * 100}%` }}>{fmtMs(rows.total * t)}</span>
            ))}
          </div>
          <ul>
            {rows.base.map(({ span, start, end, depth }) => {
              const left = ((start - rows.min) / rows.total) * 100;
              const dur = end - start;
              const width = Math.max((dur / rows.total) * 100, 0.6);
              const color = span.status === 'error' ? '#ef5b5b' : TYPE_COLOR[span.span_type] ?? '#8a94a8';
              const selected = selectedId === span.id;
              return (
                <li key={span.id}>
                  <button type="button" onClick={() => onSelect?.(span)} aria-pressed={selected}
                    className={`flex w-full items-center text-left text-xs transition-colors hover:bg-panel-2 ${selected ? 'bg-raised' : ''}`}>
                    <span className="w-[240px] shrink-0 truncate py-1.5 pr-3 text-dim" style={{ paddingLeft: 16 + depth * 14 }} title={span.name}>
                      <span className={selected ? 'text-ink' : ''}>{span.name}</span>
                    </span>
                    <span className="relative h-5 flex-1">
                      {ticks.slice(1, -1).map((t) => (
                        <span key={t} className="absolute inset-y-0 w-px bg-line" style={{ left: `${t * 100}%` }} />
                      ))}
                      <span className="absolute top-1 h-3 rounded-[3px]" style={{ left: `${left}%`, width: `${width}%`, background: color, opacity: 0.9 }} />
                    </span>
                    <span className="w-[84px] shrink-0 pr-4 text-right font-medium text-ink">{fmtMs(dur)}</span>
                  </button>
                </li>
              );
            })}
          </ul>
        </div>
      </div>
      <div className="flex flex-wrap gap-x-4 gap-y-1 border-t border-line px-4 py-2 text-[11px] text-faint">
        {Object.entries(TYPE_COLOR).map(([k, c]) => (
          <span key={k} className="inline-flex items-center gap-1.5"><span className="size-2 rounded-sm" style={{ background: c }} />{k}</span>
        ))}
        <span className="inline-flex items-center gap-1.5"><span className="size-2 rounded-sm bg-bad" />{t("error")}</span>
      </div>
    </Card>
  );
}

export default TraceWaterfall;
