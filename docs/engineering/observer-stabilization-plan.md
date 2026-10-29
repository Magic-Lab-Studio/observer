# Observer stabilization (2026-10-19): plan, results and readiness

Findings, root causes and tests per finding: [observer-findings-matrix.md](observer-findings-matrix.md).

## A. Engineering summary

* **Start:** `6e176d3` on `audit-2026-10-17` (evaluated commit `f1d25a5` + 4 audit commits; C1
  was already fixed there). Branch `fix/observer-stabilization-2026-10`.
* **End:** merged to `main` as `e433331` (#64) and `b3a5669` (#62) on 2026-10-19, released in
  0.2.0 (see `CHANGELOG.md`). Migration notes:
  [migration-20261019-parent-ref.md](migration-20261019-parent-ref.md).
* **Milestones in the order run:** 0 baseline → 1 SDK (C1, C2) → 2 ingestion integrity (A1–A5,
  M1–M6, B1, plus the parent foreign-key defect) → 3 generic analytics and dashboard (C3, M7,
  B2–B4) → A6 pricing (moved ahead of the docs milestone so the scenarios could pass end to end)
  → 4 decoupling (P1–P3) → 5 docs (D1–D5) → 6 evaluation (P4–P6) → CI gates.
* **Deferred:** B5 (partial), D6 (assessed only).

### Architectural decisions

1. **Spans export when they end.** One record per span, with end time and final status. The
   backend still completes a stored span that has no end (old SDKs, OTLP partial spans) and
   never overwrites a finished one; retries are duplicates; a different finished version is a
   counted conflict.
2. **`spans.parent_span_id` is a reference, not a foreign key** (migration
   `20261019_parent_ref`, reversible; downgrade clears dangling parents). Children finish first
   and OpenTelemetry parents can be remote; integrity stays on `spans.trace_id`.
3. **Identifier policy:** opaque strings up to 128 characters; UUIDs stored canonical, anything
   else mapped to a deterministic UUIDv5 (span IDs scoped to their trace), with the same mapping
   on the read routes. Chosen over rejecting non-UUIDs because those clients worked partly and
   the published contract test used `span-001`.
4. **Trace state is derived, not first-come:** status, interval and name come from all stored
   spans, root first (`app/trace_summary.py`). A failed attempt under a root that succeeded is a
   span error, not a trace error. Manitos envelopes keep the producer's trace status.
5. **Generic first, integrations optional:** generic metrics on every page; Manitos turn,
   quality, voice and fallback-chain views only when that telemetry exists.
6. **No PASS without evidence:** an evaluation that could not judge every criterion is
   `incomplete` with `passed: null`.

### Major risks

* The ID mapping and the parent-reference migration change stored representations. Existing
  rows stay readable (raw IDs looked up as a fallback). The migration was rehearsed on a copy of a
  production database (2,447 traces) and then applied to that instance; run it on a copy of
  yours first as well.
* Behaviour changes visible to clients: invalid input now returns 422 instead of being stored
  or failing with 500; a trace that is still running is `unset` (it used to be `error`); the
  judge default model is `gpt-4o` as documented (code said `gpt-4`).
* The dashboard changes were checked by screenshots on a throwaway instance, not by automated
  UI tests.
* Found later, fixed in this branch: the API acknowledged writes before committing them (the
  session committed after the response was sent). Every route now commits first.

## C. Test report (commands run in this session)

| Suite | Command | Result |
|---|---|---|
| Lint | `ruff check backend cli sdk/python` | clean |
| Backend | `cd backend && pytest tests -q` | 304 passed (baseline 236) |
| Python SDK | `cd sdk/python && pytest tests -q` | 90 passed (baseline 47) |
| CLI | `cd cli && pytest tests -q` | 63 passed (baseline 63) |
| TypeScript SDK | `npx tsc --noEmit && npm run test:run` | clean, 33 passed (baseline 30) |
| Dashboard | `npx tsc --noEmit -p . && npm test && npm run build` | clean, 48 passed (baseline 45 with Node 22.23.3), build ok |
| Migrations, SQLite | `alembic upgrade head / downgrade base / upgrade head`, plus data-preserving up/down of the new revision | ok |
| Migrations, PostgreSQL 16 (podman) | up to the previous head, up, down to base, up | ok; parent FK dropped, `trace_id` FK kept |
| End to end | `scripts/agent_scenarios.py` against a throwaway backend (Python 3.12, and a clean Python 3.13 install as CI will run it) | 19/19 checks |
| Regressions on the old code | new tests run against `f1d25a5` / `6e176d3` | instrumentor 7/7 fail on `f1d25a5`; lifecycle 10/13 fail; generic ingest 34/39 fail; 2 OTLP tests fail on `6e176d3` |

**Not executed:** live provider calls (all mocked; no keys used), the Docker/Podman Compose stack
itself, the MCP server against a live backend (its unit tests pass), PostgreSQL with the full
test suite (only migrations and an ingestion probe), the CI workflow on GitHub (only simulated
locally). **Environment note:** the system Node (`/usr/bin/node` 22.22.1, Ubuntu build) lacks
TypeScript type stripping, so the dashboard tests fail with it; they pass with the official
Node 22 build that CI uses.

## D. Compatibility report

| Surface | Status | Evidence |
|---|---|---|
| Python SDK | Compatible; `Span.end()` default changed from `OK` to `None` (an UNSET span still ends OK; an ERROR span stays ERROR). Spans now reach exporters at `end()` instead of at start | SDK tests |
| TypeScript SDK | Unchanged except pricing; it already exported at end | 33 tests |
| HTTP ingestion | Additive (ISO timestamps, opaque IDs, `X-Observer-Spans`); stricter validation returns 422 where garbage used to be stored | backend tests |
| OTLP | Parents kept across batches, trace state over all batches | OTLP tests (incl. 2 new) |
| SQLite | Migration verified with data, foreign keys on | probes + suite |
| PostgreSQL | Migrations and ingestion probe verified on PostgreSQL 16 | podman |
| Manitos | Ingestion contract unchanged; existing Manitos tests pass; Overview and Providers views identical when all traces are Manitos turns | `test_manitos_*`, screenshots |
| Generic agents | Traces, errors, latency, providers and loop warnings without any Manitos convention | scenarios, screenshots |

## E. Release readiness

| Target | Readiness | Reasons and blockers |
|---|---|---|
| Local development | Ready | All suites green; end-to-end scenarios pass |
| External beta | Released (0.2.0) | Merged with CI green; the Compose Quick Start was run with Podman, not Docker |
| Self-hosted production | Not yet | No automated UI tests; B5 sampling; authentication is optional by default |
| Package release | Done | 0.2.0 with `CHANGELOG.md` |

## F. Next five actions

1. ~~Review and merge~~ — done 2026-10-19 (#64, #62).
2. ~~Rehearse the migration on a copy of a live database~~ — done 2026-10-19, then applied.
3. **Run the Compose Quick Start on a clean machine** (depends on 1). Acceptance: README steps
   only, one successful and one failed trace visible with correct status and duration.
4. **Aggregated endpoints for the histogram and top-N lists** to finish B5 (independent).
   Acceptance: the Overview makes no 1 000-trace request; numbers match the current ones.
5. **Show `incomplete` evaluations distinctly in the dashboard and send `service.name` from the
   TypeScript SDK** (independent). Acceptance: an evaluation without a judge reads
   "incomplete", never "passed"; a TS trace shows its project.
