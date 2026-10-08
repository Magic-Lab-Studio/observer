import type { Span } from '../types';

export default function SpanDetail({ span }: { span: Span }) {
  const attributes = Object.entries(span.attributes ?? {});
  const scalars = attributes.filter(([, value]) =>
    value === null || ['string', 'number', 'boolean'].includes(typeof value)
  );
  const structured = attributes.filter(([, value]) => value !== null && typeof value === 'object');

  return (
    <section className="mt-6 bg-gray-800 rounded-lg p-4 min-w-0" aria-label={`Span Detail: ${span.name}`}>
      <h3 className="text-lg font-semibold mb-4 break-all">Span Detail: {span.name}</h3>
      {attributes.length > 0 && (
        <div className="mb-4">
          <h4 className="text-sm font-medium text-gray-400 mb-2">Attributes</h4>
          {scalars.length > 0 && (
            <dl className="divide-y divide-gray-700">
              {scalars.map(([key, value]) => (
                <div key={key} className="grid grid-cols-1 sm:grid-cols-[minmax(0,12rem)_minmax(0,1fr)] gap-1 sm:gap-4 py-2">
                  <dt className="text-sm text-gray-400 font-mono break-all">{key}</dt>
                  <dd className="min-w-0 max-h-80 overflow-y-auto whitespace-pre-wrap [overflow-wrap:anywhere] text-sm text-gray-200 font-mono">
                    {String(value)}
                  </dd>
                </div>
              ))}
            </dl>
          )}
          {structured.length > 0 && (
            <pre className="mt-2 bg-gray-900 rounded p-3 text-sm text-gray-300 overflow-x-auto">
              {JSON.stringify(Object.fromEntries(structured), null, 2)}
            </pre>
          )}
        </div>
      )}
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        {(['input', 'output'] as const).map((field) => (
          <div key={field} className="min-w-0">
            <h4 className="text-sm font-medium text-gray-400 mb-2">{field === 'input' ? 'Input' : 'Output'}</h4>
            <pre className="bg-gray-900 rounded p-3 text-sm text-gray-300 overflow-x-auto">
              {JSON.stringify(span[field] ?? null, null, 2)}
            </pre>
          </div>
        ))}
      </div>
    </section>
  );
}
