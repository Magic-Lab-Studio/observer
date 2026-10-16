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
| 3 | Compare runs by version | planned | |
| 4 | Regression command for CI | planned | |
| 5 | Standard OTLP ingestion | planned | |
| 6 | Read-only MCP server | planned | |
| 7 | Configurable capture privacy | later | |
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
- **Dependency found:** Manitos traces have no tool-call spans (`span_type = tool`) and do not
  export reply text, so tool and text expectations cannot be checked against real traces yet. Two
  changes are needed on the Manitos side before step 3 can verify them: emit one tool span per
  call (name, status), and decide whether a redacted reply may be exported for text checks.

## Step 3: compare runs by version

Attach commit, model, configuration and rubric version to each run, then compare runs over the
same cases with per-case differences.

- Done when: it is clear whether a change improved or worsened the agent, and where.

## Step 4: regression command for CI

A CLI command that runs a dataset (or re-scores stored runs) and exits non-zero when a
threshold or a per-case expectation regresses.

- Done when: a CI job can block a version using it.

## Step 5: standard OTLP ingestion

Today `OTLPExporter` posts Observer's own JSON to `/v1/traces/batch`; there is no OTLP receiver.

- Add an OTLP/HTTP receiver and a documented attribute mapping.
- Done when: an app instrumented with a stock OpenTelemetry exporter shows up without the SDK.

## Step 6: read-only MCP server

Tools to list failed traces, compare runs and fetch evidence, so Claude or Codex can
investigate without screenshots. Read-only first.

## Later

- **Capture privacy modes per project** (metadata only, redacted, full), filtered before export.
  For Manitos the exporter already filters at the source, so the work is mostly there.
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
