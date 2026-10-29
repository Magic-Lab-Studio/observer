# Observer findings matrix (external evaluation of 2026-10-18)

Source: independent evaluation by Robinson Miranda, 2026-10-18, of commit
`f1d25a543a7d42fbe3102bb3142d332962aab712` (33 findings). The report PDF and the evaluator's
scripts (`observer_stress.py`, `agente_claude.py`) were **not available** in this workspace; each
row is built from the finding list of the stabilization assignment and checked against the code.
`scripts/agent_scenarios.py` is an independent reimplementation of the six synthetic scenarios,
not the evaluator's script. Report identifiers are kept; nothing was reclassified.

Baseline: branch `fix/observer-stabilization-2026-10` from `audit-2026-10-17` at `6e176d3`
(4 commits after the evaluated `f1d25a5`). Merged to `main` in #64 and #62 (2026-10-19) and released
in 0.2.0. End: see the plan document.

Statuses: `TESTED` = a regression test fails on the old code (evaluated commit or baseline) and
passes now; `FIXED` = changed and covered by tests that do not reproduce the old failure, or
verified by inspection/run only; `PARTIAL`; `DEFERRED`; `CONFIRMED_BY_CODE`; `NOT_APPLICABLE`.

| ID | Sev. | Status | Root cause | Change (commit) | Tests / evidence | Remaining limitations |
|---|---|---|---|---|---|---|
| C1 | Critical | TESTED | Bound instrumentor method assigned as class attribute | Already fixed in `245b2ee`; shared `_patching.py`, async wrapper mirrors original, exception recorded and re-raised, extraction errors no longer mark a call failed (`7efd450`) | `sdk/python/tests/test_instrumentors_real_clients.py` (real `anthropic` 1.15 / `openai` 3.35 clients, mocked transport): on `f1d25a5` fails with the reported `TypeError ... missing 'self'` and a `RecursionError` on double instrumentation | Streaming responses: span ends when the stream object is returned, before it is consumed (no tokens) |
| C2 | Critical | TESTED | Span serialized and queued at `start_*`; `end()` never enqueued; `end()` default OK overwrote ERROR | Export at `end()`, idempotent `end()`, ERROR never downgraded, background flush outside the lock, bounded queue, flush at shutdown/exit (`7efd450`); backend completes an incomplete stored span, never overwrites a finished one (`a39ab31`) | `test_span_lifecycle.py` (10 of 13 fail on `f1d25a5`), backend `test_generic_ingest_integrity.py` (SDK → exporter → backend test included) | Unfinished spans are not exported at shutdown (documented). `Tracer._spans` keeps the last 10 000 spans in memory |
| C3 | Critical | TESTED | Overview cards read the Manitos quality summary (project `manitos`) and the turns view | Generic summary metrics (`acf823c`); generic Overview, Manitos panels only with Manitos data (`4af78d8`) | `backend/tests/test_generic_analytics.py`; dashboard checked in headless Chromium on a throwaway instance with generic and mixed data | Histogram, lists and stage card still use the client-side sample (see B5) |
| A1 | High | TESTED | Schema took epoch floats only | ISO 8601 (with zone) or epoch seconds, validated (`a39ab31`); README example works (`35d5f96`) | `test_a1_*` | Python 3.10 `fromisoformat` accepts fewer ISO variants than 3.11+ |
| A2 | High | TESTED | Batch accepted any ID; detail routes demanded UUIDs | Opaque IDs ≤128 chars mapped deterministically to UUIDv5 (span IDs per trace), same mapping on detail routes; raw legacy IDs still readable (`a39ab31`) | `test_a1_a2_readme_example...`, `test_a2_*` | Responses carry the stored UUID, not the client's original ID |
| A3 | High | TESTED | `_safe_uuid()` dropped bad parents; self-FK rejected children sent first (500 with SQLite FKs on); OTLP dropped unseen parents | Parents mapped like IDs; `parent_span_id` became a plain reference (migration `20261019_parent_ref`, reversible, verified on SQLite with data and PostgreSQL 16); OTLP keeps pending parents | `test_a3_*`, `test_child_sent_before_its_parent...`, OTLP `test_a_child_exported_before...` | A parent that never arrives stays a dangling reference (shown as orphan) |
| A4 | High | TESTED | Trace status set from the first batch; OTLP overwrote with each batch's own status | `app/trace_summary.py` derives status/interval/name from all stored spans after each generic and OTLP batch | `test_a4_*`, OTLP `test_a_failed_root_stays_failed...` | Manitos envelopes keep the producer's status (published contract) |
| A5 | High | TESTED | `datetime.fromtimestamp` on unvalidated input | Finite, >0, ≤2100-01-01 → 422 | `test_a5_*` (8 cases) | — |
| A6 | High | TESTED | Exact model-ID match | Official Anthropic prices (checked 2026-10-19, source on each entry), dated/Bedrock/Vertex normalization for `claude-*` only, aliases, Haiku 5.5 long-prompt tier; same in TS SDK (`32222be`) | `sdk/python/tests/test_pricing_resolution.py`, `sdk/typescript/src/pricing.test.ts`, scenario check "llm span priced" | OpenAI and Gemini prices remain unverified (carried over); retired Claude 3.x prices unverified |
| M1 | Medium | TESTED | `all(status == "ok")` else error | UNSET never an error; ended root without status = ok | `test_m1_*` | — |
| M2 | Medium | TESTED | End taken from the last span of the batch | Earliest start; once every root ended, latest span end; parallel spans not summed | `test_m2_*` | — |
| M3 | Medium | TESTED | No bounds | `ge=0` on tokens and cost | `test_m3_*` | — |
| M4 | Medium | TESTED | No interval check | 422 "end_time cannot precede start_time" | `test_m4_*` | — |
| M5 | Medium | TESTED | First span to arrive named the trace | Earliest root's name | `test_m2_m5_*`, `test_child_sent_before...` | — |
| M6 | Medium | TESTED | Any error span failed the trace | Root status authoritative; failed attempt stays an error span; `span_error_count` in summary | `test_m6_*`, scenario "429 retry" | Manitos turn error metrics follow the Manitos trace status, unchanged |
| M7 | Medium | TESTED | Every failure shown as "check the backend" | `ApiError` kinds (network, auth, not found, invalid with server detail, rate limited, server) | `dashboard/tests/format.test.mjs` | — |
| B1 | Low | TESTED | Free-form status | `unset|ok|error` | `test_b1_*` | — |
| B2 | Low | TESTED | Missing TTFT/duration returned as 0 → "<1 ms" | Null from the API, "–" in the UI; TTFT card only with Manitos data | `test_generic_analytics.py`, `format.test.mjs` | — |
| B3 | Low | FIXED | Lists showed turn IDs | Trace names in recent/slowest lists | Headless Chromium screenshots | No automated UI test |
| B4 | Low | TESTED | Fixed decimals | Three significant digits under a cent; "–" when no span reported a cost | `format.test.mjs` | — |
| B5 | Low | PARTIAL | Dashboard samples 1 000 traces per load | KPIs now come from server aggregates; the sample still feeds histogram, lists and stage card | — | Needs aggregated histogram/top-N endpoints; deferred |
| D1 | Docs | FIXED | Quick Start omitted `POSTGRES_PASSWORD` | `.env` step before `up` (Docker and Podman) | README | Compose not run in this session |
| D2 | Docs | FIXED | Table listed `localhost:5432` | "not published", loopback ports | README | — |
| D3 | Docs | FIXED | — | Python 3.10+ in prerequisites | README | — |
| D4 | Docs | FIXED | Node 20 used by the evaluator; `engines` says 22.12+ | Node 22.12+ stated | README | Note: Ubuntu's `/usr/bin/node` 22.22.1 lacks TypeScript stripping, so `npm test` fails there (environment, not code) |
| D5 | Docs | FIXED | Judge default `gpt-4` in code vs `gpt-4o` in docs; OTLP limit comment 120 vs 600 | Code default `gpt-4o`; `.env.example` aligned; judge provider variables documented | `.env.example` | `RATE_LIMIT_BATCH` was already aligned in `245b2ee` |
| D6 | Docs | DEFERRED | Recharts 2.15.4 installed, 3.10.1 current | Assessed only: a major migration touching every chart; not mixed into this program | `npm ls recharts` | Plan as a separate change with visual checks |
| P1 | Product | FIXED | Providers page built only from `llm.chain` + Manitos snapshot | `GET /v1/providers/models`; generic table always, chain views only with chain telemetry (`79fbbd9`) | `backend/tests/test_provider_models.py`, screenshot | No ordering or fallback depth is inferred for generic data (by design) |
| P2 | Product | FIXED | SDK never sent its service; batch set no project/env/session | SDK exports `service.name` / `deployment.environment.name`; batch fills empty trace fields from them and `session.id`; project never changes once set | `test_p2_*` | TS SDK does not send `service.name` yet |
| P3 | Product | FIXED | Instrumentors set only `gen_ai.system` / request model | `gen_ai.provider.name`, `operation.name`, `response.model`, `usage.*`, `finish_reasons`; analytics read both old and new keys | real-client tests, provider tests | Semantic-convention versions handled by reading both names, not by a version switch |
| P4 | Product | TESTED | Hardcoded OpenAI URL | `openai`, `openai-compatible` (local, keyless), `anthropic` (official SDK, optional extra) (`2ee003a`) | `test_judge_reliability.py` (all transports mocked) | No live provider call was made |
| P5 | Product | TESTED | No key/failure/invalid level silently dropped or replaced criteria; deterministic-only could PASS | `evaluation_status`, skipped criteria with reason/retryable, `passed: null` without evidence, summary counts incomplete apart | `test_judge_reliability.py`; the old `test_composite_with_all_deterministic_passing` pinned the bug and was changed | Dashboard does not yet show "incomplete" distinctly |
| P6 | Product | FIXED | No detection | `GET /v1/traces/{id}/diagnostics`, warning on the trace page (`5b7d3f4`) | `test_loop_detection.py`, scenario trace screenshot | Heuristic; no alert rule built on it |

## Found during the work (not in the report)

| Finding | Severity (engineering) | Status | Evidence |
|---|---|---|---|
| With SQLite foreign keys enabled on 2026-10-17, any child span sent before its parent (even in one batch) was an HTTP 500 | Critical in combination with C2 | TESTED | Probe on a migrated DB; `test_child_sent_before_its_parent...` |
| `POST /v1/evaluations/` for a missing trace: 500 with foreign keys on | Medium | TESTED | `test_evaluations_summary` |
| `/v1/traces/{id}/evaluations` answered 200 `[]` for a missing trace | Low | TESTED | `test_trace_evaluations_invalid_id` |
| Test engine did not enable foreign keys, unlike production | Medium (test gap) | FIXED | `tests/conftest.py` |
| `cost-by-model` ignored `gen_ai.*` model attributes, so SDK traces never appeared | Medium | TESTED | `test_cost_by_model_reads_gen_ai_model_attributes` |
| CI skipped the real-client instrumentor tests (packages not installed) | Medium (gate gap) | FIXED | `.github/workflows/ci.yml` |
| The SDK exporter ran the network call (with retries and sleeps) inside the tracer lock, in the caller's thread | High | TESTED | `test_end_does_not_wait_for_the_network...` |
