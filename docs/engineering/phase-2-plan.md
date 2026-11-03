# Phase 2 (roadmap steps 9-14): autonomous work plan

What to build, in which order, how each step is verified and when to stop and ask. The product
reasons for each step are in [../roadmap.md](../roadmap.md); this file is how to execute them
without supervision between checkpoints.

## Ground rules (apply to every step)

- **Branches.** One branch and one pull request per step, stacked: `feat/phase2-10-outcomes` on
  `docs/roadmap-agent-systems`, which sits on `feat/meta-model-api` (#85). #85 stays open and
  untouched until the hackathon is judged; when it merges, the stack is retargeted to `main`.
- **PRs are drafts.** Push and open as draft; never merge, never force-push a branch that has a PR,
  never rewrite #85. Merging is the maintainer's call.
- **Authorship.** Commits and PR bodies by the repository's author only: no co-author lines, no
  tool or assistant attribution, no "generated with" text.
- **Public repository.** No employer or client names, figures or findings in code, tests, fixtures,
  docs or commit messages. Fixtures are synthetic.
- **Contracts are additive.** No breaking change to published endpoints, the SDKs or
  `manitos.telemetry.v1`. New attributes and routes only; old data keeps working.
- **Migrations** run on SQLite and PostgreSQL (CI jobs `sqlite-migrations`, `postgres-migrations`),
  are reversible and are rehearsed on a copy of a real database before the PR leaves draft.
- **Privacy.** Every new field that can hold content follows the project's capture mode; names,
  codes and counts are metadata.
- **Model spend.** CI never calls a paid model. Real-model checks use the local Ollama first; Meta
  Model API calls are capped at USD 2 for the whole phase (log each run's estimated cost in the
  step's PR).
- **Definition of done** for every step: the roadmap's "Done when" holds and is shown by tests plus
  one end-to-end run (below); `ruff check backend cli sdk/python examples`, backend, SDK, CLI and
  dashboard tests and build pass locally and in CI; docs updated (README section, `docs/`,
  `CHANGELOG.md` Unreleased); roadmap status table and iteration log updated in the same PR.

## Shared test bed (built in step 10, reused by 11-14)

`examples/orchestrator/`: a synthetic agent orchestrator, deterministic by seed, with no model
needed by default (`--model ollama:llama3.2:3b` optional):

- a planner that routes to two specialists, a retrieval tool, gates (retrieval -> coverage ->
  drafting -> verification -> publication) with reason codes, and every outcome (`answered`,
  `degraded`, `abstained`, `clarification`, `refused`, `failed`);
- planted situations with known answers: an abstention that had evidence, one gate that is too
  strict, a flaky case (correct in 3 of 5 runs), sequential independent model calls, a reply that
  invents a number its tool output does not contain, a retry loop, a heavy user;
- three outputs: OTLP traces, SDK traces, and a JSONL file of its own structured events (the input
  for step 11).

Every step's end-to-end check runs this bed against a scratch backend (SQLite, a free port, never
the private instances on 8000/8100) and compares Observer's answer with the planted truth.

## Steps, in execution order

### Step 10 · outcomes and the agent span contract (first: 13 and 14 read it)

- Contract: `docs/agent-spans.md`. Attributes `observer.outcome`, `observer.outcome.reason`;
  span types `decision`, `tool`, `retrieval`, `gate`, `verification`, `replan`, `close`, with their
  `observer.*` keys, generalising the governance keys in `backend/app/api/governance.py`.
- Backend: derive the outcome in `metrics_core` (attribute first, rules second, `unknown` last);
  outcome rates as Explore measures and alert metrics (`abstained_rate`, ...); the
  "abstained with evidence" flag (abstain + retrieval with passages > 0) on the trace and as
  `abstained_with_evidence_rate`.
- SDK: helpers `obs.outcome(...)`, `obs.gate(...)`, `obs.decision(...)` in Python (TypeScript in
  the same PR if small, else a follow-up).
- Dashboard: outcome chips on Traces and the trace page, a "why" panel built from gate, decision
  and close spans, outcome rates on Overview.
- Done: the bed's planted abstention shows its reason on the trace page without content; the
  alert fires on it.

### Step 11 · ingest existing events

- `backend/app/ingest_mapping.py`: a validated YAML mapping (correlation keys, start/end event
  pairs, attributes, outcome, span type, content fields) compiled to the existing batch envelope.
- CLI `observer-ingest --mapping m.yaml --from events.jsonl [--dry-run]` (backend scripts), sending
  through `/v1/traces/batch` so idempotency and capture modes apply unchanged.
- Connectors behind one interface; this phase ships JSONL only. Loki and Azure Log Analytics get
  the interface, docs and recorded-response tests, and are marked "not verified against a live
  service" until someone runs them.
- Done: the bed's JSONL becomes the same tree as its OTLP output (compared span by span); a second
  run adds nothing; an unmapped field never reaches the database.

### Step 9 · groundedness, configurable

- Settings: extend `project_settings` (migration) with a JSON `groundedness` column validated by a
  pydantic model (`mode`, `context`, `judge`, `sample_rate`, `applies_to`, `threshold`); API on the
  privacy routes; editor on the Privacy page.
- `rules` mode in `backend/app/evaluators/grounding_rules.py`: claims with a citation, number of
  sources, citations that match a source the trace fetched. `judge` mode as a rubric criterion with
  the context assembled from the configured spans, capped, and a listed-claims evidence field.
- Alert metric `ungrounded_rate`; case expectation `groundedness >= level` in `case_check.py`.
- Real check: a local Ollama judge on the bed's invented-number reply; one Muse Spark run, within
  budget, recorded in the PR.
- Done: the roadmap's step 9 criteria, plus `mode: off` provably sends nothing.

### Step 12 · non-determinism

- `observer-replay --cases dataset.json|sessions.jsonl --repeat N --endpoint URL` (CLI) records a
  run per repetition with a shared `replay_id`.
- Backend: per case outcome/check distribution and an instability score (normalised entropy of the
  outcome distribution); dataset share of unstable cases; the regression gate reports unstable
  cases apart from regressions (exit code unchanged for existing users unless `--fail-on-unstable`).
- Dashboard: distribution column and instability on the Cases comparison.
- Done: the bed's flaky case is reported as unstable 3/5; deterministic cases score 0.

### Step 13 · cost per outcome

- Trace attributes `vcs.commit`, `vcs.branch`, `vcs.change`; `POST /v1/vcs/events` for CI to report
  merged or closed changes (authenticated like the other write routes).
- Analytics: cost per outcome (answered turn, passed case, merged change) with rework (cases that
  regressed after the change); waste share (failed, retried, fallback, looping spend); cost per
  active `user.id` and top-5% share; spend and outcomes per model and task type, premium vs local.
- Owned capacity: an hourly fully loaded cost per local provider in project settings, spread over
  the provider's busy time, shown apart from list-price spend.
- Alert metrics: `spend_per_day`, `cost_per_outcome`, `waste_share`.
- Done: on the bed, waste and top-5% share match a hand count; merged and abandoned changes give a
  cost per merged change that excludes nothing silently; a local provider with an hourly cost is
  not shown as free.

### Step 14 · gate funnel and critical path

- Funnel from step 10 gate spans: per gate, turns entering, stopped, reason codes, over time.
- Critical path per trace: sequential model calls, their share of latency, and independent calls
  (no data dependency by parent/order and inputs) as parallelisation candidates.
- Time budget per stage (project setting) and turns over budget.
- Done: the bed's over-strict gate tops the funnel with its reason; its sequential independent
  calls are flagged with their latency share.

## Per-step loop (what one autonomous iteration does)

1. Re-read the step here and in the roadmap; write the step's design note at the top of its PR
   body (contract, schema, routes, what is out of scope).
2. Tests first for the "Done" criteria; then backend, migration, SDK, CLI, dashboard, docs.
3. Run the full local suite and the end-to-end check on the bed; fix until green.
4. Independent review of the diff by a fresh reviewer (code-review at high effort), fix what is
   confirmed.
5. Commit in small, described commits; push; open or update the draft PR; wait for CI.
6. Update the roadmap (status, iteration log with what was learned) and report: what was built,
   commits, tests with real counts, the end-to-end result, spend, open questions.

## Stop and ask (do not work around)

- A product decision not settled below, or a "Done" criterion that turns out to be wrong.
- CI red twice for the same cause with no clear fix; a migration that fails on PostgreSQL or would
  rewrite existing rows.
- Any change needed in #85, in a published contract, or in the ManitOS contract.
- Model spend about to pass the budget; any need for a credential not already configured.
- Anything that would put employer or client information in the repository.

## Decisions settled up front

| Decision | Choice |
|---|---|
| Order | 10 -> 11 -> 9 -> 12 -> 13 -> 14 |
| Outcome source | agent attribute first, rules second, `unknown` otherwise (never guessed as `answered`) |
| Groundedness default | `mode: off`; a local judge is recommended in docs |
| Connectors this phase | JSONL verified; Loki and Azure interface-only, marked unverified |
| Local capacity cost | opt-in hourly cost per provider; without it local stays 0 and is labelled "list price 0" |
| Unstable cases in CI | reported apart; failing on them is opt-in |

## Rough size

| Step | Size | Main risk |
|---|---|---|
| 10 | large (contract, backend, SDK, dashboard, the bed) | getting the contract right the first time |
| 11 | medium | mapping expressiveness without becoming a language |
| 9 | medium | judge cost and context size |
| 12 | medium | run volume in SQLite |
| 13 | large | attributing spend to merged changes without double counting |
| 14 | medium | detecting independence of calls without content |
