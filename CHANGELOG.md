# Changelog

## Unreleased

### The backend wheel carries its migrations

`magic-lab-observer-backend` 0.3.0 shipped no Alembic migrations, so a backend installed with pip
could not create or upgrade its database without a clone of the repository. The wheel now includes
them under `app/_migrations`, and a new command, `observer-migrate`, runs them without
`alembic.ini` (`--database-url`, `--current`, `--sql`; `DATABASE_URL` as before). A checkout keeps
working with `alembic upgrade head` in `backend/`.

## 0.3.0 — 2026-11-07

First release since 0.2.0. It brings agent outcomes (how each turn ended and why), groundedness
checked by rules, event ingestion and test-case runs whose text expectations are checked against the
stored reply. The Python packages are published on PyPI; npm stays suspended (see
`docs/releasing.md`): the TypeScript SDK is installed from source.

### Groundedness numbers, OTLP content, `span()` (#100)
- Fixed (backend): groundedness compares numbers by value. A decimal from a tool (`1492650.0`) no
  longer reads as invented when the reply writes `1.492.650`; a repeated separator groups thousands,
  the last of two different separators is the decimal one, and `372.0` equals `372`.
- Fixed (backend): a number the user gave in the question counts as supported (the root span's input
  is used for the number check only).
- Fixed (backend): OTLP spans carry their input and output (OpenInference `input.value` /
  `output.value`, `gen_ai.*` messages, tool arguments and results), subject to the project's capture
  mode. Before, groundedness was always `unknown` on OTLP traces.
- Added (Python SDK): `span(...)` as a public `with` block.
- Docs: first-time setup gains a ready-to-run first trace from the Python SDK, and the trailing slash
  of `/v1/traces/` is explained.

### Declaring an abstention (#101)
- Docs (`docs/agent-spans.md`): a reply that only says it could not find the answer is `answered`
  unless the agent declares the abstention. Three places to do it, with SDK examples: a gate before
  the model, a `cannot_answer(reason)` tool, an `answered` field in a structured reply.

### Test cases check their text (#103)
- Fixed (backend): `must_contain` / `must_not_contain` are checked against the stored reply when the
  project stores content (case-insensitive, whitespace collapsed); without reply text they stay
  `unknown`, never a silent pass. Cases stay metadata only: the reply is not copied into the case.

### Cases follow the project (#105)
- Fixed (dashboard): the Cases page lists the datasets of the project chosen in the header, and Save
  as case uses the trace's own project. Before, both assumed `manitos`.

### npm suspended (#104)
- Docs and publishing: npm is no longer offered as an installation channel (access to the npm account
  is lost); the TypeScript SDK is built from source. The `all` publishing target is Python only.

### External evaluation closed (D1, P4, design limits)
- Docs: the findings matrix has no open row. D1: the Compose Quick Start was run as written with
  `podman-compose` on a clean clone (11/11 scenarios, a successful and a failed trace in the
  dashboard). P4: the judge ran live with `openai-compatible` against a local Ollama and returned a
  complete evaluation. A2, A3, A4, M6, P1 and P3 keep their limitation, now marked as a design
  decision with its reason; D4 is closed as an environment issue.
- Docs (README): the Compose containers have fixed names, so one stack runs per machine.

### Recharts 3 (external evaluation D6)
- Changed (dashboard): `recharts` 2.15.4 → 3.10.1. Recharts 2 is no longer maintained (`npm ci`
  warned on every install). No chart code had to change. Checked with headless screenshots of
  every chart before and after, at 1440 and 390 px, tooltips included: on the test bed each chart
  is identical or differs in under 0.5 % of its pixels (axis ticks, and bars of height zero that
  Recharts 3 no longer draws). The browser tests pass.

### Dashboard browser tests (external evaluation B3)
- Added: Playwright tests of the dashboard (`dashboard/e2e`, `npm run e2e`) and the CI job
  `dashboard-e2e`. They load every page in Spanish and English and fail on any JavaScript error,
  and walk the main flows: the overview shows traces, a trace opens with its «why did it end»
  panel, an evaluation the judge could not complete reads «Incomplete», the groundedness mode is
  saved and survives a reload, Explore runs a query. The backend is loaded with the test bed;
  the tests plant their own project `e2e`. Before, the dashboard had unit tests of its helpers
  only.

### ISO timestamps, verified prices, loop alert (external evaluation A1, A6, P6)
- Fixed (backend): ISO 8601 timestamps are read by one parser, `parse_iso8601`, with the same
  result on Python 3.10 and later. Before, 3.10 refused `Z` in event mappings, a fraction other
  than 3 or 6 digits, `+0000` and the basic format, so those spans were a 422 there only. CI gains
  a `python-310` job running the backend, Python SDK and CLI suites on 3.10.
- Changed (price table): every price is checked against the provider's official page, with its
  source and day. `o3` drops from 10/40 to 2/8 USD per 1M tokens. Models with no published price
  now cost `null` with the reason, instead of a price nobody had verified:
  - OpenAI `gpt-4`, `gpt-4-turbo`, `gpt-3.5-turbo`, `o1`, `o1-mini`, `o3-mini` (not on the page);
  - Claude 3.x (retired by Anthropic between 2025-07 and 2026-04);
  - Gemini 1.5 and 2.0 (2.0 Flash shut down on 2026-06-01).
- Changed (TypeScript SDK): the price table is the shared `pricing.json`, no longer a hand-kept
  copy. It gains the Gemini 2.5/3.8, Groq and Cloudflare prices the Python SDK had, dated price
  changes (`ratesOn`) and `UNPRICED`.
- Added (alerts): metric `loop_rate`, the share of the window's traces with a repeated-tool-call
  warning (the trace page's loop detection), over the newest 500 traces per pass.

### SDK span lifecycle (external evaluation C1 and C2)
- Fixed (Python and TypeScript SDKs): a streamed call (`stream=True`) ends its span when the
  stream does, with the text, finish reason, model, tokens and cost:
  - `ok` when exhausted;
  - `error` if it fails;
  - `unset` with `observer.stream.incomplete` when closed early.
  Before, the span ended when the stream object was returned, with neither text nor tokens.
- Fixed (Python and TypeScript SDKs): spans still open at shutdown are sent as partial (no end
  time, `observer.unfinished`) instead of being dropped. TypeScript gains `tracer.shutdown()`,
  run before the process exits. It exports each span once, and keeps at most 10 000 spans for
  `export()`.
- Fixed (TypeScript SDK): the OpenAI and Anthropic instrumentors traced nothing, because they
  patched members that do not exist:
  - `require("anthropic")` instead of `@anthropic-ai/sdk`;
  - `chat` on the client prototype;
  - their spans never reached an exporter.
  They now patch `OpenAI.Chat.Completions.prototype.create` and
  `Anthropic.Messages.prototype.create`, and keep the provider's `APIPromise`. `instrument()`
  accepts `modules` for ES modules. Tests run against the real clients (`openai` 7.41.0,
  `@anthropic-ai/sdk` 1.1.0, dev dependencies).

### Overview from the server (external evaluation B5)
- Added: `GET /v1/analytics/overview`. It returns the activity buckets, latency histogram, slowest
  traces and stage timings of every trace that started in the range, with the dashboard's own
  rules (start-time placement, nearest-rank percentiles, the same histogram edges). Capped at
  20 000 traces, and it says when it was.
- Added: `GET /v1/analytics/environments`.
- Changed (dashboard): the Overview's histogram, slowest list, stage card and generic activity
  chart come from that route. Before, they came from the last 1 000 traces loaded in the browser,
  so a busy 7-day range was a sample. The dashboard now loads only the 50 latest traces, for the
  recent list and the "new" flash.

### External evaluation follow-up (findings P2 and P5)
- Fixed (TypeScript SDK): every exported span now carries `service.name` (the trace's project),
  and `deployment.environment.name` when `environment` is set. Shared `attributes` behave as in
  the Python SDK: a value the span sets itself wins. Before, `serviceName` was stored and never
  sent, so TypeScript traces had no project.
- Fixed (dashboard): an evaluation the judge could not finish (`passed: null`) reads "Incomplete"
  in the list, and its detail lists the skipped criteria with their reason. The pass rate counts
  decided evaluations only. Before, a high score on the criteria that were judged showed it as
  passed.

### Groundedness (roadmap step 9, `rules` mode)
- Added: per-project configuration sections in a new table `project_configs` (migration
  `20261106_configs`); `GET /v1/projects/{id}/config` and `GET|PUT|DELETE
  /v1/projects/{id}/config/groundedness` with a closed schema. Mode `off` by default.
- Added: groundedness in `rules` mode, no model. Each claim of a reply is checked against the
  outputs of the trace's tool and retrieval spans:
  - a citation to a source the trace never fetched is unsupported;
  - so is a number the sources do not contain, and one such number makes the reply `ungrounded`;
  - without reply text or context the result is `unknown`.
  Results are evaluations of type `groundedness` (rubric `groundedness@1`), scored by the
  automatic sweep or on demand with `POST /v1/projects/{id}/groundedness/evaluate`. See
  `docs/groundedness.md`.
- Added: alert metric `ungrounded_rate`, Explore view `groundedness`, "Groundedness by model" on
  Evaluations (`/v1/evaluations/by-model` gains `groundedness`), case expectation
  `expect.groundedness.min_level`, and the editor on the Privacy page.
- Added: the test bed's step 9 check: an invented refund amount is `ungrounded` with that number
  listed, its twin is `grounded`, and formats that carry no content stay `unknown`.
- Not built: the `judge` and `rules+judge` modes (the API answers 422).

### Event ingestion (roadmap step 11)
- Added: `POST /v1/ingest/events` and the `observer-ingest` command (`--dry-run`). A system's own
  JSON events become traces through a closed YAML mapping: event pairs make spans, a field holds
  the parent, fields become attributes, and content follows the capture mode
  (`OBSERVATORY_EVENTS_CAPTURE`, default `metadata`). Ids are deterministic, so a resend stores
  nothing. Fields the mapping does not name are never stored. See `docs/event-ingest.md`.
- Added: the test bed's `bed-events` rendering and step 11 check (structure, unmapped field absent).

### Agent outcomes (roadmap step 10)
- Added: the agent span contract `observer.agent.v1` (`docs/agent-spans.md`, `GET /v1/agent/contract`):
  outcome attributes and typed `decision`, `tool`, `retrieval`, `gate`, `verification`, `replan`,
  `close` spans, on every ingestion path; OTLP reads `observer.span.type`.
- Added: each turn's outcome, source and reason derived on read (`unknown` without evidence);
  `GET /v1/agent/turns`; outcome fields in `/v1/traces/{id}/diagnostics`; contract steps in
  `/v1/governance/turns/{id}`; Explore measures and dimensions; alert metrics
  `outcome_<outcome>_rate` and `abstained_with_evidence_rate`.
- Added: `examples/orchestrator`, a synthetic orchestrator with planted answers, run in CI.
- Added (Python SDK): `llm_observatory.agent` helpers (`outcome`, `gate`, `decision`, `retrieval`,
  `tool_call`, `verification`, `replan`, `close`, `set_task`, `set_user`) and
  `Tracer(attributes=...)`, and `current_span()`.
- Added (TypeScript SDK): agent contract attribute builders (`gateAttributes`, `closeAttributes`...).
- Added (dashboard): the "why" panel on the trace page, outcome chips in the trace list (the list
  response gains `outcome`), and "How turns ended" on Overview.
- Added: the outcome `cancelled` (`close.kind` `cancel`) for a turn the user or the caller stopped:
  it no longer counts as `failed`; alert metric `outcome_cancelled_rate`, Explore measure
  `cancelled`, a neutral tone in the dashboard. Additive: the contract stays `observer.agent.v1`.

## 0.2.0 — 2026-10-25

First release since 0.1.1 (37 merged changes). Upgrading the SDKs is recommended: the 0.1.1
Python SDK's OpenAI and Anthropic instrumentors fail on the first call with
`TypeError: ... missing 1 required positional argument: 'self'`.

### Python SDK (`magic-lab-observer`)
- Fixed: instrumentors bind the client instance (the 0.1.1 `TypeError` above).
- Changed: reliable export. Finished spans go to a bounded queue (`max_queue_size`, counted in
  `dropped_spans` when full); `OTLPExporter` retries with backoff and can keep undelivered batches
  in an on-disk spool (`spool_dir`); `shutdown()` flushes what is pending. Spans still reach Observer
  through an exporter added with `add_exporter(OTLPExporter(endpoint=...))`.
- Added: `Tracer(environment=...)`, exported as `deployment.environment.name`.

### Backend (`magic-lab-observer-backend`)
- Added: OTLP/HTTP trace receiver, alert rules over sliding windows, automatic operational-health
  evaluations (separate from correctness), datasets ("save trace as case"), runs per agent version
  and a regression gate, a read-only MCP server, ingest health and a silence alert, capture mode per
  project.
- Added: Explore, Alerts and Evaluations work for any project, not only `manitos`: the model is read
  from `gen_ai.*` attributes, a generic rubric (`generic_turn@1`) scores any agent's turns, the
  automatic sweep covers every project, and `GET /v1/analytics/projects` lists them.
- Fixed: findings of the 2026-10-14 and 2026-10-17 audits and the 2026-10-18 external evaluation
  (SQLite foreign keys, `/health` returns 503 when the database is down, docs behind the API key,
  metadata redaction, `/ws/live` authentication).

### Dashboard
- Redesigned console with ES/EN, Providers, Explore, Alerts, Evaluations and Cases pages.
- Project selector in Explore, Alerts and Evaluations (`?project=`, then the last one chosen, then
  the project with the most recent trace).
- vite 8.3.5 (GHSA-9jrq-w75r-8gcw).

### Deployment
- One `compose.yaml` for Docker Compose and podman-compose (`podman-compose up` failed with two files).
- `OBSERVER_API_PORT` and `OBSERVER_DASHBOARD_PORT` choose the host ports.

### CLI (`magic-lab-observer-cli`) and TypeScript SDK (`@magic-lab-studio/observer`)
- Version aligned to 0.2.0.
