# Standard OpenTelemetry (OTLP) ingestion

Observer accepts traces from any stock OpenTelemetry exporter, so an app can be instrumented
without the Observer SDK.

```bash
export OTEL_EXPORTER_OTLP_ENDPOINT=http://127.0.0.1:8100/v1/otlp   # the exporter appends /v1/traces
export OTEL_EXPORTER_OTLP_HEADERS="Authorization=Bearer <key>"      # only when OBSERVATORY_API_KEY is set
```

The endpoint is `POST /v1/otlp/v1/traces`. It takes OTLP/HTTP with `Content-Type:
application/x-protobuf` (the usual default) or `application/json`, optionally gzip- or
deflate-compressed, and answers with an `ExportTraceServiceResponse` in the same format
(`partialSuccess.rejectedSpans` when some spans were refused). gRPC is not supported.

## Where the data goes

| OTLP | Observer |
|---|---|
| `resource` `service.name` | project (`project_id`); override with the `X-Observer-Project` header |
| `deployment.environment.name` / `deployment.environment` | environment |
| `service.instance.id` | service instance |
| span attribute `session.id` / `gen_ai.conversation.id` | session (default: the trace id) |
| trace id (16 bytes) | trace id, as a UUID |
| span id (8 bytes) | a stable UUID derived from the trace and span id (a re-sent span updates, never duplicates) |
| `parentSpanId` | parent span (linked also when the parent arrived in an earlier batch) |
| span `name` | span name; for tool spans the tool's name (`gen_ai.tool.name`) |
| `startTimeUnixNano`, `endTimeUnixNano` | start and end time |
| status `ERROR` / `OK` / unset | `error` / `ok` / `unset`; a trace is `error` if any span is |
| root span (no parent) | names the trace; the trace spans the earliest start to the latest end |
| `gen_ai.usage.input_tokens` (or `prompt_tokens`) / `output_tokens` (or `completion_tokens`) | `tokens_input` / `tokens_output` |
| `gen_ai.response.model` / `gen_ai.request.model` | attribute `model` |

Span type comes from `openinference.span.kind` (`LLM`, `TOOL`, `AGENT`, `CHAIN`, `RETRIEVER`,
`EMBEDDING`, `RERANKER`) or from `gen_ai.operation.name`: `chat`, `text_completion`,
`generate_content` → `llm`; `execute_tool` → `tool`; `invoke_agent`, `create_agent` → `agent`;
`embeddings` → `embedding`. Anything else is `generic`. Tool spans therefore feed the same
test-case checks and `observer-regression` gate as Manitos' own tool spans.

Events and links are ignored.

## Privacy: metadata only by default

Observer is metadata-only. `OBSERVATORY_OTLP_CAPTURE` chooses what is stored:

* `metadata` (default): span attributes whose key looks like content are dropped before storage
  (anything containing `prompt`, `completion`, `content`, `message`, `input`, `output`, `query`,
  `statement`, `body`, `text`, `payload`, `argument`, `url`, `path`, `file`, `email`, `token`,
  `key`, `secret`, `authorization`, `cookie`, and similar). Standard GenAI attributes that are not
  content (`gen_ai.usage.*`, model, operation, tool and agent names, finish reasons) are kept. Strings
  are cut at 200 characters.
* `full`: attributes are stored as sent (strings up to 4000 characters). Use it only for data you
  are willing to keep in the Observer database.

The filter looks at attribute **names**, not values, so a name that does not look like content can
still carry content. Treat `metadata` as a safety net, not a guarantee, and keep prompts out of
span attributes you do not want stored. Per-project capture modes are planned (roadmap step 7).

## Limits and behaviour

* Body limit 16 MB after decompression; `RATE_LIMIT_OTLP` (default `600/minute`) rate-limits it.
* Up to 500 spans per trace per request are processed in chunks; a trace owned by another project
  is rejected and counted in `rejectedSpans`; a rejected trace leaves no partial data behind.
* A child that arrives before its parent is stored unlinked; it is linked only if its parent had
  already been stored when the child arrived (an earlier parent is found, a later one is not
  re-linked).
