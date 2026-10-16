# Roadmap: from watching agents to validating them

Observer should help diagnose and validate local agents: detect a failure, understand it, and
check that the fix worked. More charts add less than closing that loop.

This file is the working plan. **Update it in every iteration**: tick the step, record the PR,
and write down what was learned or changed. Keep entries short.

## Principles

- Operational health and answer quality are different things. Missing data is shown as
  *unknown*, never as a good score.
- Privacy and self-hosting are requirements, not a differentiator (Phoenix also runs locally).
  What can set Observer apart is operational simplicity (SQLite, one process) and explaining
  agent behaviour: which agent acted, with what context, through which route, which tools ran,
  which permissions applied, why a fallback happened and what it cost, and what evidence says
  the task finished.
- One PR per step. Each step ends with CI green and this file updated.
- The competitor comparison below comes from vendor documentation only; no benchmarks.

## Status

| # | Step | State | PR |
|---|------|-------|----|
| 1 | Separate operational health from quality | done | this PR |
| 2 | Datasets from traces | done | this PR |
| 3 | Compare runs by version | done | this PR |
| 4 | Regression command for CI | done | this PR |
| 5 | Standard OTLP ingestion | done | #56 |
| 6 | Read-only MCP server | done | #57 |
| 7 | Configurable capture privacy | done | this PR |
| 8 | Ingestion reliability (persistent queue, backoff, metrics) | later | |

## Step 1: health vs quality

Problem: `evaluators/manitos_turn.py` scores a `quality` criterion (weight 0.20) from
`llm_degraded` alone. That is an operational signal; it does not show the answer is right, so a
fast wrong answer can still get an "A".

- Rename the criterion to `degradation`; bump the rubric to `manitos_turn@2`. Evaluation ids
  include the rubric, so new rows are created (no migration); the sweep deletes the replaced
  `manitos_turn@1` row of the same turn, so each turn keeps one score.
- Result carries two axes: `health` (latency, errors, degradation, truncation, fallback) and
  `correctness` (`null` = unknown until a real signal exists).
- The overall grade is capped while correctness is unknown, so health alone cannot reach "A".
- Evaluations page, Providers page and `/by-model` show both axes; unknown is labelled, not zero.
- Done when: a turn that is fast but has no correctness evidence cannot show an excellent
  overall grade; tests cover it.

## Step 2: datasets from traces

Save a failing trace as a reusable test case: input, expected outcome, tags, content reviewed
by a person before saving.

- New tables (`datasets`, `dataset_items`) with a migration and a complete downgrade.
- **Decided (2026-10-16):** an expected outcome is any mix of: tools that must / must not be
  called, health conditions (no error, truncation, degradation, fallback; max duration), phrases
  the reply must / must not contain, and a human verdict. Cases are **metadata only**: nothing is
  copied from a trace; phrases are typed by a person. Runs are checked by re-scoring traces Manitos
  already sends (Observer does not execute the agent).
- Done when: one action on the trace page creates an item and it can be listed and exported.
- **Dependency found, tool half resolved:** Manitos traces had no tool-call spans
  (`span_type = tool`). Manitos PR #36 (merged 2026-10-16) now exports one per executed call (name,
  outcome, duration; no arguments or results), so tool expectations are verifiable on traces
  recorded since its `manitos-server` restart. Still open: Manitos exports no reply text, so text
  phrases stay `unknown`; whether a redacted reply may be exported is a privacy decision for the
  user (it would break the metadata-only rule).

## Step 3: compare runs by version

Attach commit, model, configuration and rubric version to each run, then compare runs over the
same cases with per-case differences.

- **Built:** a run = a version (`commit`, `model`, `config`, plus the rubric version) and one trace
  per case, reported through the API by whoever ran the cases. Observer checks each case against its
  trace (`app/case_check.py`) and stores `pass` / `fail` / `unknown`. Comparing two runs of the same
  dataset gives regressed / improved / same / unknown / missing per case, with health and duration
  deltas and the configuration that differs.
- Done when: it is clear whether a change improved or worsened the agent, and where.

## Step 4: regression command for CI

A CLI command that runs a dataset (or re-scores stored runs) and exits non-zero when a
threshold or a per-case expectation regresses.

- **Built:** `observer-regression` (`app/ops/regression_gate.py`). It can record a run from a
  results file and gate it, or gate a stored run. Exit 0 pass / 1 regression / 2 undecidable.
  Blocks on regressions against a baseline, failed cases without one, optional pass-rate and
  health-drop thresholds; `unknown` is reported apart, and a run with nothing verified fails
  unless `--allow-unverified` (lesson of the 2026-07 soak: a gate must not pass on no evidence).
- Done when: a CI job can block a version using it.

## Step 5: standard OTLP ingestion

Today `OTLPExporter` posts Observer's own JSON to `/v1/traces/batch`; there is no OTLP receiver.

- **Built:** `POST /v1/otlp/v1/traces` (`app/api/otlp.py`, translation in `app/otlp.py`), protobuf
  and JSON, gzip, partial success. It translates OTLP spans into the existing
  `manitos.telemetry.v1` envelope and reuses its ingestion (idempotency, upsert), so nothing else
  changes. Mapping, privacy modes and limits: `docs/otlp.md`. Metadata-only by default
  (`OBSERVATORY_OTLP_CAPTURE`); gRPC is not supported. New dependency: `opentelemetry-proto`.
- Done when: an app instrumented with a stock OpenTelemetry exporter shows up without the SDK.

## Step 6: read-only MCP server

Tools to list failed traces, compare runs and fetch evidence, so Claude or Codex can
investigate without screenshots. Read-only first.

- **Built:** `observer-mcp` (`app/ops/mcp_server.py`, stdio, optional extra `mcp`): 11 read-only
  tools over the HTTP API. Content (`input`/`output`) is never returned unless
  `OBSERVER_MCP_CONTENT=1`. Tools, setup and privacy: `docs/mcp.md`. A test fails if the reader can
  use any method other than `GET` or the metrics query.

## Step 7: capture privacy per project

- **Built:** `metadata` / `redacted` / `full` per project (`project_settings`, migration
  `20261018_settings`), applied on every ingestion path (`app/capture.py`): the SDK routes, the
  Manitos ingest and OTLP. A project without a mode keeps each path's default, so nothing changes
  silently; `OBSERVATORY_CAPTURE_DEFAULT` overrides the defaults. `/v1/privacy/projects` reports,
  from the latest 2000 spans per project, how many hold an input, an output, content-like
  attributes or sensitive patterns, and `…/enforce` (explicit `confirm`) rewrites stored data.
  Privacy page in the dashboard. Details: `docs/privacy.md`.
- **Limits:** redaction is pattern-based (emails, phones, Luhn-valid cards, SSN-shaped numbers,
  tokens/keys, secret-looking keys), not NER. SDK traces posted without a project id follow the
  path default or the env override (no project to configure). Source-side filtering for
  third-party exporters is the exporter's job; this is the server-side guarantee.

## Later
- **Ingestion reliability:** optional persistent queue, exponential backoff, counters for
  pending and dropped records.

## Reference comparison (documentation only)

| Product | Worth learning from |
|---|---|
| Langfuse | Datasets, comparable experiments, evaluations that gate CI |
| Phoenix | OpenTelemetry/OpenInference, experiments; direct local competitor |
| Opik | MCP access to failures, traces and experiments from coding assistants |
| LangSmith | Versioned datasets: record which data and version produced a result |
| LangWatch | Whole-task and tool-use scenarios, not only single answers |

## Iteration log

- 2026-10-15: plan written. No steps started.
- 2026-10-16: step 1 done. Rubric `manitos_turn@2`: `health` and `correctness` axes (correctness
  unknown), overall capped at 0.89; criteria renamed `reliability`/`degradation`; old v1 scores
  replaced by the sweep. Learned: the rubric already had a criterion called `health`, so the axis
  needed the criterion renamed to avoid two meanings.
- 2026-10-16: step 2 done. Tables `datasets`/`dataset_items` (migration `20261016_datasets`),
  `/v1/datasets`, "Save as case" on the trace page, a Cases page, JSON export. Learned: real
  Manitos traces carry no tool spans, so the tool expectations are stored but not yet checkable.
- 2026-10-16: step 3 done. Tables `dataset_runs`/`run_results` (migration `20261017_runs`), run API,
  `case_check`, comparison on the Cases page. Only health expectations can be verified on real
  traces today; tool and text checks come back `unknown` until Manitos exports tool spans and
  reply text. Step 4 (CI command) will fail on `fail` and report `unknown` separately.
- 2026-10-16: step 4 done. `observer-regression` CLI with exit codes 0/1/2, report
  `observer.regression.v1`. Next: step 5 (OTLP). Tool and text expectations still need Manitos to
  export tool spans and reply text before the gate can verify them on real traces.
- 2026-10-16: Manitos PR #36 merged: tool spans exported. Tool expectations are now verifiable on
  new traces; text phrases remain `unknown`. Observer services migrated and restarted.
- 2026-10-16: step 5 done. OTLP/HTTP receiver, verified with a stock `opentelemetry-sdk` +
  `opentelemetry-exporter-otlp-proto-http` app (protobuf default): trace, spans, token counts and a
  tool span arrive, prompt attributes are dropped. Learned: protobuf's JSON mapping writes ids as
  base64, OTLP/JSON as hex, so ids are normalised to hex before translation.
- 2026-10-16: step 6 done. `observer-mcp`: 11 read-only tools, no content by default. Verified over
  real stdio with an MCP client against a synthetic Observer (an OTLP-ingested trace with its tool
  span was listed and read). Steps 1-6 complete; what remains is under "Later".
- 2026-10-16: step 7 done. Capture modes per project on every ingestion path, a stored-data
  report and enforcement, Privacy page. Verified on a synthetic instance: a project storing emails
  in inputs showed 3 sensitive spans, switching to `metadata` and applying removed them. The
  `manitos` project stored 0 inputs/outputs (the Manitos exporter is metadata-only at the source).
