# LLM Observatory - TypeScript SDK

Open-source LLM observability for Node.js/TypeScript applications.

## Installation

```bash
npm install @magic-lab-studio/observer
```

Optional peer dependencies for auto-instrumentation:

```bash
npm install openai      # for OpenAI auto-instrumentation
npm install anthropic   # for Anthropic auto-instrumentation
```

## Quick Start

```typescript
import { instrument, trace, asyncTrace, Tracer, OTLPExporter } from '@magic-lab-studio/observer';

// Auto-instrument LLM libraries
instrument({ openai: true, anthropic: true });

// Set up exporter to send traces to the backend
const tracer = new Tracer({ serviceName: 'my-app' });
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
  openai: true,      // patches OpenAI SDK
  anthropic: true,   // patches Anthropic SDK
});
```

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

Supported models: GPT-4o, GPT-4o-mini, GPT-4-turbo, GPT-4, GPT-3.5-turbo, o1, o1-mini, o3, o3-mini, Claude 4 Opus/Sonnet, Claude 3.5 Sonnet/Haiku, Claude 3 Opus/Sonner/Haiku.

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
