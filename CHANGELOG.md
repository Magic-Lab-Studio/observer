# Changelog

## Unreleased

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
