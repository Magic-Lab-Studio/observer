import type { Span } from '../types';
import { fmtMs } from '../helpers/format';
import { parseApiDate } from '../dates';
import { Chip, StatusPill } from './ui';

function duration(span: Span): number | null {
  if (!span.start_time || !span.end_time) return null;
  return parseApiDate(span.end_time).getTime() - parseApiDate(span.start_time).getTime();
}

export default function SpanDetail({ span }: { span: Span }) {
  const attributes = Object.entries(span.attributes ?? {});
  const scalars = attributes.filter(([, value]) =>
    value === null || ['string', 'number', 'boolean'].includes(typeof value)
  );
  const structured = attributes.filter(([, value]) => value !== null && typeof value === 'object');
  const dur = duration(span);

  return (
    <section className="rounded-xl border border-line bg-panel min-w-0" aria-label={`Span Detail: ${span.name}`}>
      <header className="border-b border-line px-4 py-3">
        <h3 className="break-all text-sm font-semibold">{span.name}</h3>
        <div className="mt-2 flex flex-wrap items-center gap-2">
          {span.status && <StatusPill status={span.status} />}
          {span.span_type && <Chip tone="accent">{span.span_type}</Chip>}
          {dur != null && <Chip>{fmtMs(dur)}</Chip>}
          {span.tokens_input != null && <Chip>{span.tokens_input} → {span.tokens_output ?? 0} tokens</Chip>}
        </div>
      </header>
      <div className="space-y-4 p-4">
        {attributes.length > 0 && (
          <div>
            <h4 className="mb-1 text-[11px] font-semibold uppercase tracking-wider text-faint">Attributes</h4>
            {scalars.length > 0 && (
              <dl className="divide-y divide-line">
                {scalars.map(([key, value]) => (
                  <div key={key} className="grid grid-cols-1 gap-1 py-2 sm:grid-cols-[minmax(0,10rem)_minmax(0,1fr)] sm:gap-4">
                    <dt className="break-all font-mono text-xs text-faint">{key}</dt>
                    <dd className="max-h-80 min-w-0 overflow-y-auto whitespace-pre-wrap font-mono text-xs text-ink [overflow-wrap:anywhere]">{String(value)}</dd>
                  </div>
                ))}
              </dl>
            )}
            {structured.length > 0 && (
              <pre className="mt-2 max-h-72 overflow-auto rounded-lg bg-canvas p-3 text-xs text-dim">
                {JSON.stringify(Object.fromEntries(structured), null, 2)}
              </pre>
            )}
          </div>
        )}
        <div className="grid grid-cols-1 gap-4 2xl:grid-cols-2">
          {(['input', 'output'] as const).map((field) => (
            <div key={field} className="min-w-0">
              <h4 className="mb-1 text-[11px] font-semibold uppercase tracking-wider text-faint">{field === 'input' ? 'Input' : 'Output'}</h4>
              <pre className="max-h-72 overflow-auto rounded-lg bg-canvas p-3 text-xs text-dim">
                {JSON.stringify(span[field] ?? null, null, 2)}
              </pre>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}
