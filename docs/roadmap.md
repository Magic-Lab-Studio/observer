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
| 1 | Separate operational health from quality | done | #51 |
| 2 | Datasets from traces | done | #52 |
| 3 | Compare runs by version | done | #53 |
| 4 | Regression command for CI | done | #54 |
| 5 | Standard OTLP ingestion | done | #56 |
| 6 | Read-only MCP server | done | #57 |
| 7 | Configurable capture privacy | done | #58 |
| 8 | Ingestion reliability (persistent queue, backoff, metrics) | done | #59 |
| 9 | Groundedness, configurable per project (hallucinations) | done | `rules` mode, then the judge modes; see `docs/groundedness.md` |
| 10 | Outcomes and an agent span contract (why it did not answer) | done | #88, #89 |
| 11 | Ingest the events a system already emits | done | JSONL, `/v1/ingest/events`, `observer-ingest`; Loki and Azure not built |
| 12 | Non-determinism: same input, N runs | in progress | repetitions, instability, gate, panel built; `observer-replay` not yet |
| 13 | Cost per outcome, not per token | in progress | 13a built; owned capacity built; cost per merged change (`vcs_events`) not yet |
| 14 | Gate funnel and critical path | planned | |

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
- **Dependency found, tool half resolved:** the Manitos integration did not export tool-call spans
  (`span_type = tool`). It now exports one per executed call (name, outcome, duration; no arguments
  or results), so tool expectations are verifiable on traces recorded since that version. Still
  open: it exports no reply text, so text phrases stay `unknown`; exporting a redacted reply would
  break the metadata-only rule and is a privacy decision for the operator.

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

## Step 8: ingestion reliability

Findings: the Python SDK exporter retried instantly with no backoff, retried permanent 4xx errors,
dropped silently and kept no counters; `/v1/traces/batch` was capped at 10/minute while the SDK
flushes every 5 s, and answered 500 when a retried batch re-sent span ids already stored; nothing
said that a project had stopped sending. The Manitos exporter already had retries, an encrypted
spool and a circuit breaker.

- **Built (SDK):** `OTLPExporter` retries only what can succeed later (network errors, 408/425/429/
  5xx) with exponential backoff, jitter and `Retry-After`; permanent 4xx are `rejected`, not
  retried; optional persistent spool (`spool_dir`, 0600, bounded, oldest evicted, drained oldest
  first, survives restarts); `stats()` with `exported, retried, rejected, dropped, spooled,
  recovered, spool_evicted, pending`.
- **Built (backend):** `/v1/traces/batch` is idempotent and its limit is `RATE_LIMIT_BATCH`
  (default 120/minute); `GET /v1/ingest/health` (last trace and 5 m / 1 h / 24 h counts per
  project); alert metric `ingest_silence_minutes`; quota and ingest alerts no longer depend on
  `min_samples` (a quota rule created in the UI with the default 10 could never fire).
- **Built (dashboard):** a "Telemetry intake" card on Overview; the alert form hides window and
  sample fields for the silence metric.
- Not done: encrypting the SDK spool, and sender-reported drop counters inside the Observer (the
  SDK exposes `stats()` locally).


## Step 9: groundedness (hallucination check)

Problem: Observer does not measure hallucinations. The judge's `output_quality` includes
"accuracy", but it sees only the prompt and the reply, with no source to check them against: it
catches an error it happens to know, not a plausible invented fact. Test cases catch a known wrong
answer (`must_contain` / `must_not_contain`, a human label), not an unknown one.

- A `groundedness` criterion in the rubric, scored by the judge: every factual claim of the reply
  is checked against the context the agent had in that trace (the output of its tool spans:
  retrieval, search, file reads, API results, plus the system and user input). Levels from
  "every claim supported" to "contradicts the context"; the unsupported claims are listed in the
  evaluation detail, short, as evidence.
- No context in the trace (no tool spans, or a capture mode that kept no content): the criterion
  is `unknown` with the reason, never a pass. A trace whose reply needs no source (a poem, a
  greeting) can be marked not applicable by the rubric, not scored as grounded.
- The context goes to the judge, so a cloud judge receives it; bounded size (truncate per tool
  output, say how much was dropped) and the project's capture mode decide what can be sent.
- Shown per model on Evaluations next to quality, and usable as a case expectation
  (`groundedness >= level`) so a run can fail on it.
- **Configurable per project** (stored with the project's settings, editable on the Privacy page next
  to the capture mode, and readable over the API):
  - `mode`: `off` (default) · `rules` (no model: share of claims with a citation, number of sources,
    citations that point to a source the trace really fetched) · `judge` · `rules+judge`.
  - `context`: which spans count as evidence (span types, tool-name patterns, the system prompt or
    not) and a size cap per span and per trace.
  - `judge`: provider and model (a local judge keeps the context on the machine), and a sampling rate
    so cost stays bounded on busy projects.
  - `applies_to`: intents or tags that need a source; everything else is `not applicable`.
  - `threshold` and an alert metric `ungrounded_rate`, plus a case expectation
    (`groundedness >= level`).
- Done when: a trace whose reply invents a number absent from its tool output scores low with that
  claim listed; the same reply with the number present in the tool output scores high; a trace
  without context stays `unknown`; `rules` mode needs no model; a project with `mode: off` sends
  nothing anywhere; tests cover these, plus one real run with a model judge.

- **Built (2026-11-05, `rules` mode):** `app/groundedness.py` and `project_configs`
  (`/v1/projects/{id}/config/groundedness`), evaluations of type `groundedness`
  (`groundedness@1`), `ungrounded_rate`, Explore view `groundedness`, the case expectation
  `groundedness.min_level`, the Privacy page editor and the bed's step 9 check. See
  `docs/groundedness.md`.
- **Not built:**
  - the `judge` modes: the judge must first receive the spans' input and output;
  - sampling;
  - one real run with a model judge.

## Phase 2: why the agent did not answer, and what each result cost

Steps 10 to 14 come from two sources: product feedback on agent systems of a common shape (a
planner that routes to specialists, tool search, verification gates, answers that may abstain or
degrade, a model layer of their own instead of the OpenAI client, structured JSON events already
emitted), and the shift in how teams account for agent spend (cost per shipped
result instead of cost per token). The question they share is the one these systems ask most and
answer worst: *why did the agent not answer, and was it worth what it cost?*

What already exists and is reused: tool spans and repeated-call (loop) warnings, governance spans
for one integration (decision, security, approval, skill: `/v1/governance`), `session.id` from
OTLP, datasets, runs per version and the CI gate, capture modes per project, alerts with desktop
notifications, and the price table.

## Step 10: outcomes and an agent span contract

Problem: a trace ends `ok` or `error`. An agent that abstained, asked for clarification, refused
or answered degraded looks `ok`, so "why did it not answer?" means reading logs.

- **Outcome per turn**: `answered`, `degraded`, `abstained`, `clarification`, `refused`,
  `failed`, from an attribute the agent sets (`observer.outcome`) or derived by rules; rates over
  time on Overview and Explore, and alert metrics for each (a jump in abstentions is an alert).
- **An open span contract** (OTLP attributes plus SDK helpers), generalising the governance spans:
  `decision` (candidates, chosen, rejected with reason), `tool` (name, argument names, result size,
  error code), `retrieval` (passages, sources), `gate` (requirement, status, reason codes),
  `verification` (claims accepted, rejected, pruned), `replan` / `close` (clarify, abstain,
  refuse). Names and codes only by default; content follows the capture mode.
- **"Abstained with evidence"**: a turn that abstained although its retrieval returned passages,
  as a flag on the trace and an alert metric. It points at gates that are too strict.
- Done when: a synthetic agent emitting each outcome and span type shows the reason for an
  abstention on its trace page without reading content; the abstained-with-evidence alert fires
  on a planted case; the contract is documented in `docs/agent-spans.md`.

## Step 11: ingest the events a system already emits

Problem: many production agents already write structured JSON events (`job_id`, event name,
`duration_ms`, started/completed pairs) and call models through their own layer, so neither the
SDK instrumentors nor OTLP see them. Re-instrumenting is the main reason not to adopt.

- A declarative mapping in YAML: correlation keys (job, run, conversation), which event pairs form
  a span, which fields become attributes, outcome and span type (step 10), and what is content.
- Connectors that read: JSONL files first (also for backfill: observe last month without touching
  the system), then Loki and Azure Log Analytics. Idempotent like `/v1/traces/batch`.
- `observer-ingest --mapping map.yaml --from events.jsonl` with a dry run that prints the traces it
  would create.
- Done when: a JSONL file of a sample orchestrator becomes traces with the right parent/child
  structure, outcomes and timings; re-running it creates no duplicates; a field outside the mapping
  never reaches the database.
- **Built (2026-11-05):** `app/event_mapping.py` (closed mapping, deterministic ids, partial spans
  for steps that never ended), `POST /v1/ingest/events` through `apply_envelope`, and
  `observer-ingest` with `--dry-run`. The bed's `bed-events` rendering passes step 10's outcome
  checks and a new step 11 check: every step hangs from its root, and the unmapped field is
  nowhere. A second send stores nothing. Loki and Azure exist only as the `EventSource`
  interface; they are not built or verified. See [event-ingest.md](event-ingest.md).

## Step 12: non-determinism

Problem: the same input passes once and fails the next time, and nobody can tell a flaky case from
a broken one.

- Run a case N times (`observer-replay --cases dataset.json --repeat 5 --endpoint URL`, which also
  replays a JSONL of real sessions against an HTTP endpoint) and record each run.
- Per case: the distribution of outcomes and check results, and an instability score (0 = always
  the same, 1 = a coin flip). Per dataset: the share of unstable cases. The regression gate can
  treat an unstable case apart from a regression.
- Done when: a case answered correctly in 3 of 5 runs is reported as unstable with that
  distribution, not as pass or fail; a deterministic case scores 0.

- **Built (2026-11-12):** `run_results.repetition` (migration `20261112_repetitions`), a run may
  report the same case several times; `case_check.aggregate_repetitions` folds them (`unstable` with
  the distribution and a binary entropy, 3 of 5 = 0.971); run summaries count `unstable` apart and
  comparisons mark it as its own change; `observer-regression --fail-on-unstable / --max-unstable`
  (unstable is not a regression by default); the runs panel shows unstable counts. **Not built:**
  `observer-replay` (running a dataset N times against an HTTP endpoint) and the bed's planted
  unstable case.

## Step 13: cost per outcome, not per token

Problem: Observer prices every call, but a cost per token measures consumption, not results.
Retries, loops and failed turns cost the same as useful work, and a few heavy users or one
premium route can drive most of the spend.

- **Cost per outcome**: per answered turn, per passed case and per shipped change. A trace can
  carry `vcs.commit` / `vcs.branch` / `vcs.change` (pull request) and a CI hook reports when a
  change merged, so a project shows the cost per merged change next to its rework (cases that
  regressed after it).
- **Waste**: the share of spend on failed turns, retries, fallbacks and repeated calls (step 10
  outcomes and the existing loop warning).
- **Concentration**: cost per active user (`user.id`) and the share of the top 5%, because
  averages over all users hide who drives spend.
- **Routing**: spend and outcomes per model and task type, premium versus local, to see which work
  could move to a cheaper or self-hosted model without losing outcomes (checked with a run, step
  12, before switching).
- **Owned capacity**: local models cost 0 per call today. A project can set a fully loaded hourly
  cost per local provider (hardware amortisation, power, operation) so owned and rented capacity
  compare on the same cost per outcome.
- Alert metrics: spend per day, cost per outcome, waste share.
- Done when: a project with merged and abandoned changes shows a cost per merged change that
  excludes nothing silently; waste and top-5% share match a hand count on synthetic data; a local
  provider with an hourly cost is no longer shown as free.

- **Built (2026-11-12, 13a, no migration):** `app/cost_outcomes.py` puts every costed span in one
  bucket (useful, failed, error, repeated, unknown; they add up to the total), the cost per answered
  turn, the waste share, the top-5% users' share (by `user.id`, else the actor; unattributed spend
  reported apart) and spend and outcomes per model with its pricing status;
  `GET /v1/costs/summary`; alert metrics `spend_per_day`, `cost_per_outcome`, `waste_share`; a
  `/costs` dashboard page.
- **Built (2026-11-12, 13b, owned capacity):** a project `cost` section,
  `{"hourly_usd": {"local": 0.5}}` (keys: a model, a provider, or `local` for every local model); a
  local call with no cost then costs its duration at that rate, computed when read and never written
  to `cost_usd`; the summary reports `paid_usd` and `owned_usd` apart and the buckets cover both.
  **Not built:** cost per merged change (`vcs_events`, `vcs.*` attributes, a CI hook) and rework: it
  needs a migration, kept until #112's migration has merged so Alembic keeps one head.

## Step 14: gate funnel and critical path

- **Gate funnel**: from step 10 gate spans, how many turns each gate stops (for example retrieval
  -> coverage -> drafting -> verification -> publication) and with which reason codes, over time.
- **Critical path**: how many model calls run one after another in a turn, their share of the
  latency, and which ones have no dependency between them (candidates to run in parallel).
- **Time budget per stage**: an expected duration per stage and the turns that exceed it.
- Done when: a synthetic orchestrator with one over-strict gate shows that gate first in the funnel
  with its reason code; a turn with sequential independent calls shows them as parallelisable with
  their share of latency.

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
- 2026-10-16: the Manitos integration now exports tool spans. Tool expectations are verifiable on
  new traces; text phrases remain `unknown`.
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
- 2026-10-16: step 8 done. Idempotent batch + configurable limit, SDK backoff/spool/stats, ingest
  health endpoint, silence alert, Overview card. Verified on a synthetic instance: a project quiet
  for 95 minutes opened the silence alert even though the rule's min_samples was 10.
- 2026-10-16: security hardening from an external audit: loopback-only compose, unpublished
  PostgreSQL with no default password, key lending limited to local requests, size limits on the
  generic ingestion routes, rate-limit identity by validated key or IP, MCP attribute allow-list,
  internal hand-off notes removed. See `docs/security.md`.
- 2026-10-16: decision on reply text: Manitos keeps exporting metadata only, so phrase checks stay
  `unknown` (the operator chose privacy over text evaluation). The ingestion card sets projects
  silent for over a week apart. Compose files verified on podman and image names fully qualified.
- 2026-10-19: the 2026-10-17 audit (#62) and the 2026-10-18 external evaluation (#64, 33
  findings) merged: spans exported when they end, opaque IDs mapped to UUIDs, trace status and
  interval derived from all spans, `parent_span_id` without a foreign key (migration
  `20261019_parent_ref`, rehearsed on a copy of a production database), generic Overview and
  Providers, provider-neutral judge with `incomplete` evaluations, loop diagnostics, writes
  committed before the response. Open: B5 (partial), D6. See `docs/engineering/`.
- 2026-10-22 to 2026-10-26: one `compose.yaml` for Docker and Podman with configurable host ports
  (#68, #70), vite 8.3.5 (#69), release 0.2.0 (#74, see `CHANGELOG.md`), Explore, Alerts and
  Evaluations for any project with the `generic_turn@1` rubric (#75), publish fixes (#76, #77).
- 2026-11-03: phase 2 planned (steps 10-14) from product feedback on agent orchestrators and from cost-per-outcome accounting; step 9 made configurable per project
  (`off` / `rules` / `judge`). Suggested order: 10 (outcomes and spans are what 13 and 14 read),
  11 (adoption), 9, 12, 13, 14.
- 2026-11-05: steps 10a and 10b merged (#88). Outcome `cancelled` added so a turn the user
  stopped is not a failure (ManitOS reports its stop button this way).
- 2026-11-05: step 11 built. A system's own JSONL events become traces through a YAML mapping
  (`observer-ingest`, `/v1/ingest/events`). Learned: batches must never split a trace, because a
  span's start and end must arrive together.
- 2026-11-05: step 9 `rules` mode built (project configuration table, `ungrounded_rate`, Explore
  view, case expectation, editor). Groundedness results got their own evaluator type, because
  four readers took every `rule_based` row as a turn-health score. Learned: a URL at the end of a
  sentence kept its full stop and read as a citation the trace never fetched.
- 2026-11-06: step 9 numbers compared as values. Found with a synthetic support agent on six
  providers: a tool's `1492650.0` lost its decimal point and became `14926500`, so every faithful
  reply ("1.492.650 COP") read as an invented number (4 of 5 false positives on correct answers).
  A number the user gave ("12 people") no longer counts as invented either. Learned: compare what a
  number means, not its digits; a single separator before three digits is ambiguous, so both
  readings count.
- 2026-11-06: OTLP spans carry their input and output. Found with a synthetic agent instrumented
  with plain OpenTelemetry and OpenInference (as teams coming from Phoenix or Langfuse arrive): the
  receiver kept only attributes, so groundedness was always `unknown` (`no_reply_text`) even under
  `full` capture. Content is read from `input.value`/`output.value`, `gen_ai.*.messages`, GenAI tool
  calls and the deprecated prompt/completion keys; `metadata` still keeps none.
- 2026-11-06: Python SDK `span()` / `Tracer.span()`, a `with` block that makes the span current.
  Found writing an adapter for a synthetic agent: opening a turn's root without a decorator needed
  the private `_current_span`; Phoenix and Langfuse both have this.

- 2026-11-06: how to declare an abstention (`docs/agent-spans.md`). A synthetic support agent whose
  models answered "I could not find the price" showed every such turn as `answered`: rule 5 does
  not read the reply. Decision: keep it that way (works under `metadata`, any language) and document
  three places an agent declares it (a gate before the model, a `cannot_answer` tool, a structured
  field).
- 2026-11-06: test cases check their text phrases. `case_check` answered `unknown` ("reply text not
  exported") for every `must_contain` / `must_not_contain`, even when the project stored the reply
  (`full` or `redacted` capture): the README promised the check "when reply text is available" and
  it was never built. A run now reads the stored reply (root output, else the last LLM output) only
  to check, case-insensitive with whitespace collapsed; cases stay metadata only. Found building a
  version comparison (v1 with a defect, v2 fixed) for a course video.
- 2026-11-06: the Cases page follows the project selector, and "Save as case" saves into the
  trace's own project. The dashboard listed `/v1/datasets` without a project, so the backend's
  default (`manitos`) applied: another project's datasets were invisible, and a case saved from its
  trace landed in a `manitos` dataset. Found building a course video on a non-Manitos agent.
- 2026-11-07: the external evaluation of 2026-10-18 is closed
  (`docs/engineering/observer-findings-matrix.md`): Recharts 3 (#106), the Compose Quick Start run
  with `podman-compose`, the judge run live against a local Ollama, and six limits documented as
  design decisions. The status table marks step 10 done (#88, #89); the «Later» item about phrase
  checks staying `unknown` was removed, since #103 built them.
- 2026-11-12: step 9 done: the `judge` and `rules+judge` groundedness modes. The judge sees the
  rules' bounded context, its failures are `unknown`, a remote judge needs `allow_remote`, a
  `metadata` project never calls it, and `sample_rate`/`max_per_pass` bound the calls. Live with
  `llama3.2:3b` on a local Ollama: the planted twins came out right three times in three. Learned:
  the small judge listed the user's own question as an unsupported claim of a correct reply, so a
  listed claim must be a quote of the reply (words and numbers: «180 USD» has no long word).
- 2026-11-12: step 12 in progress: repetitions in runs and the instability of a case (unstable,
  distribution, entropy), in run summaries, comparisons, the regression gate and the runs panel.
  The migration was rehearsed on a copy of the live database (no runs stored there, so it checks
  the schema only). `observer-replay` is left for a later change.
- 2026-11-12: step 13a: cost per outcome without a migration. Waste and the top-5% share are
  checked against a hand count on synthetic traces. Learned: the loop detector needs five identical
  calls by default, so a fixture with three repeats showed no repeated spend.
## Later

Not scheduled; recorded here because the text above refers to this section.

- Encrypt the SDK spool on disk and give it a maximum age.
- Drop counters reported by the sender (what never reached the Observer).
- OTLP over gRPC.
- Conversations as an object: turn index, follow-ups, history state and one timeline per
  conversation (today: `session.id` only).
- Privacy per field: an allow-list of attributes, redaction or hashing per field, metadata only by
  default and content capture per environment.
- Correlation ids as first-class search (`job_id`, `run_id`, `conversation_id`, `trace_id`) with
  links out to external logs.
- An embeddable trace viewer (web component or iframe with a deep link to one turn).
- Export to OTLP and Langfuse, and full OpenTelemetry `gen_ai.*` conventions on export.
- Alert delivery to Slack and Teams (webhooks), beyond desktop notifications.
- Tag every trace with the version (git SHA, manifest hash, branch), not only runs.
- The SQLite foreign keys and WAL are enabled per connection since 2026-10-17; the LLM judge should become an explicit opt-in.
