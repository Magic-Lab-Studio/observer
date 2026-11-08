# LLM Observatory - TypeScript SDK

Open-source LLM observability for Node.js/TypeScript applications.

## Installation

**npm is not an installation channel for now.** The maintainers lost access to the npm account
that owns `@magic-lab-studio/observer`, so `0.2.0` and later were never published there and the registry still
serves an outdated `0.1.1` (no `service.name`, no `shutdown()`). Do not install it from npm; build
the package from source instead (Node.js 22.12 or newer). The import name is the same.

```bash
git clone --depth 1 https://github.com/Magic-Lab-Studio/observer.git
cd observer/sdk/typescript && npm ci && npm run build && npm pack
# then, in your project:
npm install /path/to/observer/sdk/typescript/magic-lab-studio-observer-0.3.0.tgz
```

Optional peer dependencies for auto-instrumentation:

```bash
npm install openai      # for OpenAI auto-instrumentation
npm install @anthropic-ai/sdk   # for Anthropic auto-instrumentation
```

## Quick Start

```typescript
import { instrument, trace, asyncTrace, Tracer, OTLPExporter } from '@magic-lab-studio/observer';

// Auto-instrument LLM libraries
instrument({ openai: true, anthropic: true });

// Set up exporter to send traces to the backend
// serviceName becomes the trace's project, environment its environment
const tracer = new Tracer({ serviceName: 'my-app', environment: 'production' });
tracer.addExporter(new OTLPExporter({
  endpoint: 'http://localhost:8000',
  apiKey: 'your-api-key',
}));

// Manually trace functions
const result = trace('summarize', (span) => {
  span.attributes['document_id'] = 'doc-123';
  return openai.chat.completions.create({
    model: 'gpt-4o',
    messages: [{ role: 'user', content: 'Summarize this document' }],
  });
});

// Async trace with context propagation
const summary = await asyncTrace('summarize-async', async (span) => {
  const response = await openai.chat.completions.create({
    model: 'gpt-4o',
    messages: [{ role: 'user', content: 'Summarize this' }],
  });
  return response.choices[0].message.content;
});
```

## API

### `instrument(options)`

Auto-instruments LLM libraries to capture traces automatically.

```typescript
instrument({
  openai: true,      // patches OpenAI.Chat.Completions.prototype.create
  anthropic: true,   // patches Anthropic.Messages.prototype.create (@anthropic-ai/sdk)
});

// ES modules or bundlers, where `require` is not available: pass the modules
import * as openai from "openai";
instrument({ openai: true, modules: { openai } });
```

Every client is traced, created before or after `instrument()`. The returned promise is still the
provider's, so `.withResponse()` keeps working. With `stream: true` the span ends when the
stream does, with the text, finish reason and tokens seen:
- status `ok` when the stream is exhausted;
- `error` if it fails;
- `unset` with `observer.stream.incomplete` if you break out early.

OpenAI reports tokens on a stream only with `stream_options: { include_usage: true }`.

### `tracer.shutdown()`

Sends every span still open as a partial one: no end time, status `unset` and
`observer.unfinished: true`. Then it flushes the exporters. It runs once on its own before the
process exits (`beforeExit`) for every tracer with an exporter. Each span is exported once, even
if `endSpan` is called twice. `tracer.export()` keeps the last 10 000 spans.

### `uninstrument(options)`

Removes instrumentation patches.

### `trace(name, fn)`

Synchronous trace wrapper. Creates a span, runs `fn`, and ends the span.

### `asyncTrace(name, fn)`

Async trace wrapper with `AsyncLocalStorage` context propagation. Child traces automatically link to their parent.

### `Tracer`

Core tracing class.

| Method | Description |
|---|---|
| `addExporter(exporter)` | Register an exporter for automatic span export |
| `removeExporter(exporter)` | Unregister an exporter |
| `startTrace(name)` | Create a root trace span |
| `startSpan(name, options)` | Create a child span |
| `endSpan(span)` | End a span and trigger export |
| `export()` | Get all spans in backend format |
| `flush()` | Flush all registered exporters |
| `clear()` | Clear all spans from memory |

### `OTLPExporter`

Sends traces to the LLM Observatory backend via HTTP.

```typescript
import { OTLPExporter } from '@magic-lab-studio/observer';

const exporter = new OTLPExporter({
  endpoint: 'http://localhost:8000',
  apiKey: 'optional-api-key',
  timeout: 10000,     // ms
  maxRetries: 3,
});

tracer.addExporter(exporter);
```

### `calculateCost(model, inputTokens, outputTokens)`

Calculate USD cost for a model using the built-in pricing table.

```typescript
import { calculateCost } from '@magic-lab-studio/observer';

const cost = calculateCost('gpt-4o', 1000, 500);
// Returns 0.0075 or null for unknown models
```

The table is `src/pricing.json`, the same one the Python SDK and the backend use: every price names its official source and the day it was checked (OpenAI, Anthropic, Google Gemini, Groq, Cloudflare). A model that is not in it returns `null`, not zero; `UNPRICED` says why for known models with no published price (for example retired Claude 3.x and Gemini 1.5/2.0). Prices that change on a known date apply from that day (`ratesOn`).

## TypeScript Types

```typescript
import type { Span, SpanStatus, TokenUsage, SpanExporter } from '@magic-lab-studio/observer';

interface Span {
  id: string;
  traceId: string;
  parentId?: string;
  name: string;
  spanType: string;
  startTime: number;
  endTime?: number;
  status: SpanStatus;
  input?: Record<string, unknown>;
  output?: Record<string, unknown>;
  tokens?: TokenUsage;
  costUsd?: number;
  metadata: Record<string, unknown>;
  attributes: Record<string, unknown>;
}
```

## Development

```bash
npm install
npm run build      # compile TypeScript
npm test           # run tests
npm run test:run   # run tests once
```

## License

Apache License 2.0
