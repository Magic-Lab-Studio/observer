# Phase 2 (roadmap steps 9-14): work plan

How to build steps 9-14 of [../roadmap.md](../roadmap.md): order, shared decisions, the synthetic
test bed, and what "done" means for each delivery. Product reasons are in the roadmap.

## Ground rules

- **Branches.** One draft pull request per delivery, stacked: `docs/roadmap-agent-systems` ->
  `feat/phase2-10a-outcomes` -> ... The stack sits on `main`; the Meta work (#85) merges on
  its own. Drafts are never merged or force-pushed by the author of the work.
- **Contracts are additive.** No breaking change to `/v1/traces/batch`,
  `/v1/ingest/manitos/traces` (`manitos.telemetry.v1`) or the SDK APIs.
- **Privacy.** New fields that can hold content follow the project's capture mode; names, codes
  and counts are metadata.
- **Models.** CI never calls a paid model. Real-model checks use a local Ollama or free-tier keys,
  with synthetic data only; the model is picked from the provider's `/models` at run time.
- **Synthetic data only** in code, tests, fixtures and docs.
- **Done** for a delivery: tests written first pass; `ruff check backend cli sdk/python examples`,
  backend, SDK, CLI and dashboard tests and build pass; migrations go up/down/up on SQLite and
  PostgreSQL; the end-to-end check on the bed passes; an independent review of the diff; README,
  `docs/`, `CHANGELOG.md` (Unreleased) and the roadmap updated; CI green.

## Shared decisions

1. **Outcomes are derived on read, not stored.** Contract in `backend/app/agent_contract.py`
   (`OUTCOMES`, `AGENT_SPAN_TYPES`, keys, aliases such as `retriever` -> `retrieval`);
   `backend/app/outcomes.py` `derive_outcome(signals)` runs from `metrics_core._finish`. Rules, in
   order: the `observer.outcome` attribute; a `close` span's `close.kind`; an error trace is
   `failed`; degraded or truncated is `degraded`; `answered` only with positive evidence; otherwise
   `unknown`. Spans arrive in several batches and rules may change, so nothing is backfilled.
2. **`metrics_core._signals_from_rows` loads more scalar columns once** (id, parent, span type, end
   time, cost, tokens), never input or output (guarded by a test), and gains an `_apply_agent_span`
   hook that later steps extend.
3. **Project configuration in a new table** `project_configs(project_id, section, value JSON,
   updated_at)` with sections `groundedness`, `cost`, `budgets`, and routes
   `/v1/projects/{id}/config[/{section}]`. Not `project_settings`: its `capture_mode` is NOT NULL and
   the privacy DELETE removes the whole row.
4. **Event ingestion reuses `apply_envelope`** (`api/manitos_ingest.py`) through a new
   `POST /v1/ingest/events`: idempotency receipt, upsert of corrected spans, and the same storage
   path as OTLP.
5. **New routers** (`/v1/agent`, `/v1/costs`, `/v1/vcs/events`, `/v1/projects/{id}/config`) take a
   `project_id`; the older `/v1/analytics/*` routes that ignore the project are not extended.
6. **Alert metrics** go in `metrics_core.METRICS` / `compute_metric` (`outcome_<x>_rate`, ...);
   `ungrounded_rate` is a new kind `groundedness` with its own branch in `alerts.evaluate_rules`.
7. **Contract keys must survive `metadata` mode** (`capture.filter_attributes`): `tool.arg_names`,
   `retrieval.sources` (ids, not URLs). Backend labels need an entry in `dashboard/src/i18n-en.ts`,
   enforced by a backend test.
8. **New commands** follow `app/ops/regression_gate.py` (argparse, `main(argv) -> int`, exit codes
   0/1/2, injectable transport, `[project.scripts]`): `observer-ingest`, `observer-replay`.
9. **Repetitions** are a `run_results.repetition` column, not one run per repetition, so
   `compare_results` and the regression gate keep comparing one run per side.

## Shared synthetic test bed

`examples/orchestrator/`, built in the first delivery and extended by each step:
`orchestrator_bed/model.py` (seeded episodes), `emit.py` (OTLP/JSON, the SDK wire format, and a
JSONL of the bed's own events with a mapping), `truth.py` (planted answers), `checks.py` (checks
against the public API), `agent_server.py` (an HTTP agent for step 12) and
`run_bed.py --endpoint URL --check 10,11,...|all`. Each rendering goes to its own project
(`bed-otlp`, `bed-batch`, `bed-events`).

Planted: every outcome; an abstention whose retrieval returned 4 passages; a `verification` gate
that stops 7 of 12 turns (`claim_unsupported`); a flaky case (3 of 5); three sequential sibling
model calls with no declared dependency (~70% of latency) plus a declared pair as a negative
control; a reply that invents a number its tool output does not contain, and a correct twin; a
loop of 6 repeated tool calls; one user with ~60% of spend among 40; changes merged, closed and
without an event; a turn over its stage budget.

It runs in CI (a step in the `agent-scenarios` job), in pytest (`backend/tests/test_bed_e2e.py`)
and locally against a scratch SQLite backend on a free port.

## Deliveries

| # | Branch | Content | Migration |
|---|---|---|---|
| 0 | `docs/roadmap-agent-systems` | roadmap phase 2 and this plan | - |
| 1 | `feat/phase2-10a-outcomes` | contract v1 (`docs/agent-spans.md`), derived outcomes, outcome alert metrics and Explore measures, `/v1/agent/turns`, `/v1/agent/contract`, outcome in diagnostics, new types in the turn path, the bed | - |
| 2 | `feat/phase2-10b-agent-helpers` | Python SDK helpers (`outcome`, `gate`, `decision`, `retrieval`, `tool_call`, `verification`, `replan`, `close`, `set_user`, `Tracer(attributes=...)`), TypeScript attribute builders, dashboard "why" panel, outcome chips and rates | - |
| 3 | `feat/phase2-11-event-ingest` | closed YAML mapping, deterministic ids, JSONL source (Loki and Azure as interface only, marked unverified), `/v1/ingest/events`, `observer-ingest --dry-run` | - |
| 4 | `feat/phase2-09-groundedness` | project config, groundedness `rules` and `judge` modes, sampling, `ungrounded_rate`, Explore view, case expectation, editor | `project_configs` |
| 5 | `feat/phase2-12-replay` | repetitions, instability (binary entropy), `unstable` in comparisons and the gate, `observer-replay` | run repetitions |
| 6 | `feat/phase2-13a-cost-outcomes` | one spend bucket per span (failed turn, failed call, repeated call, useful), cost per answered turn, top-5% share, routing, `/v1/costs/summary`, cost alert metrics, Costs page | - |
| 7 | `feat/phase2-13b-vcs-capacity` | `/v1/vcs/events`, `vcs.*` attributes, cost per merged change with rework, cost per passed case, owned-capacity hourly cost computed on read | `vcs_events` |
| 8 | `feat/phase2-14-funnel` | gate funnel, critical path (sequential calls with no declared dependency), stage budgets and `over_budget_rate` | - |

## Stop and ask

A product decision not settled here or a "done" criterion that proves wrong; CI red twice for the
same cause; a migration that fails on PostgreSQL or would rewrite existing rows; any change needed
in #85, a published contract or the ManitOS contract; a free-tier key exhausted or asking for
payment; anything that could put non-synthetic third-party information in the repository.
