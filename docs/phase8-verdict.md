# Manitos Observer — Phase 8 Passive Gate Verdict

- **Status**: `PASS`
- **Owner**: OpenCode lane (Independent Verifier & Observer Owner)
- **Verdict issued**: 2026-07-31
- **Canonical evidence**: `.observer-state/manitos-phase8.json` (gitignored runtime artifact)

## Summary

The 24-hour passive release gate for the Manitos Observer emitter ran against a
live Manitos instance (`http://127.0.0.1:5555`, `/readyz`) with a real
Observer service (`http://127.0.0.1:8000`). The run finished
`2026-07-23T21:06:36Z` and **passed** every gate check.

| Metric | Result | Threshold | Status |
|---|---|---|---|
| Observed turns | **22** | ≥ 20 (`minimum_turns`) | ✅ |
| Samples | 25 (480 s window @ 20 s interval) | — | ✅ |
| Observer availability | **1.0** | ≥ 0.99 | ✅ |
| Failure rate (exporter) | 0 / 25 | ≤ 0.05 | ✅ |
| Persisted pending (spool) | **0** | 0 | ✅ |
| Circuit breaker | `closed`, 0 opens | closed/half_open | ✅ |
| Average turn duration | 4.9 s | ≤ 60 s | ✅ |
| Quality rates (degraded/error/fallback/tool-error/truncated/tts-error) | all **0.0** | bounded | ✅ |
| Durable delivery | enabled, 0 spooled/evicted/retried | required | ✅ |

`evaluation.passed = true`, `evaluation.failures = []`.

## Reconciliation of conflicting verdicts

Two conflicting gate verdicts existed at different paths. Resolved as follows:

- `observer/.observer-state/manitos-phase8.json` — the live passing run (22
  turns, driven by Claude Code on 2026-07-23) → **canonical path**, verdict `PASS`.
- `observer/backend/.observer-state/manitos-phase8.json` — an older
  interrupted run that failed because the Observer service was not running
  (`WinError 10061`, `persisted_pending=90` in the pre-service-restart probe).
  The duplicate path was removed and the old failure is preserved as
  `observer/.observer-state/manitos-phase8.fail-archive.json`.

The verdict covers the **offline-testable** release criteria plus the live
passive window. It does **not** cover the still-open Observer lane follow-ups
(privacy contract + encrypted-spool durability exercise end-to-end,
provider-comparison runs, mutable-mode verification against a live Observer);
those are tracked as A7 open items.

## How to reproduce

```bash
# Evaluate the gate verdict from the saved run
python scripts/evaluate_observer_gate.py

# Re-run a passive gate window (Manitos + Observer service must be up)
python scripts/start_observer_gate.py --hours 24

# Inspect the live gate state
python -m manitos.cli observer gate status
```
