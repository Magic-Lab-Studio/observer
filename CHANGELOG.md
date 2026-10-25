# Changelog

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
