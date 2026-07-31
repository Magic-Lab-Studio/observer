# Manitos Observer — Phase 8 Passive Gate Verdict

- **Status**: `FAIL` (non-promotional bounded probe — **no release certification**)
- **Owner**: OpenCode lane (Independent Verifier & Observer Owner)
- **Verdict issued**: 2026-07-31
- **Canonical evidence**: `backend/.observer-state/manitos-phase8.json` (gitignored runtime artifact)

## Summary

This is a non-promotional probe. It does **not** certify the Phase 8 release
gate: no 24-hour window with a live Manitos instance has been observed, so no
promotion decision is made from this run. The previous `PASS` verdict
(2026-07-23) certified a 24-hour gate from a 480-second run; that claim is
**retracted** and superseded by this doc (see "Supersession").

The corrected gate ran `2026-07-31T04:41:11Z` → `04:49:13Z` (480 s, 23 samples
@ 20 s interval) against the live Observer at `http://127.0.0.1:8000` with the
Manitos readiness probe at `http://127.0.0.1:8765/readyz`. It failed **closed**,
which is the correct outcome for the observed conditions.

| Metric | Result | Threshold | Status |
|---|---|---|---|
| Gate window | fixed `[started_at, now)`, 480 s | — | ✅ (corrected) |
| Samples | 23 @ 20 s | — | — |
| Observer availability | 1.0 | ≥ 0.99 | ✅ |
| Manitos availability | **0.0** | ≥ 0.99 | ❌ |
| Manitos ready at end | **false** | true | ❌ |
| Observed turns (fixed window) | **0** | ≥ 20 | ❌ |
| Failure rate (exporter) | 0 / 23 | ≤ 0.05 | ✅ |
| Persisted pending (spool) | 0 | 0 | ✅ |
| Circuit breaker | `closed`, 0 opens | closed/half_open | ✅ |
| Average turn duration | 0 ms (no turns in window) | ≤ 60 s | ✅ (vacuous) |
| Durable delivery | enabled, 0 spooled/evicted/retried | required | ✅ |
| Readiness failures gated | **yes** | yes | ✅ (corrected) |

`evaluation.passed = false`; failures:

- `manitos_availability_below_threshold` — readiness probe down 23/23 samples
- `manitos_not_ready_at_end` — last sample was not ready
- `insufficient_observed_turns` — 0 turns in the fixed window
- `manitos_readiness_unavailable` — no ready sample to read exporter state from

**Why it failed**: no live Manitos instance was reachable during the window
(`/readyz` `ConnectError` on every sample), so no turns were ingested into the
Observer in `[started_at, now)` and the readiness signal was absent throughout.
Observer health and the quality endpoint answered `200` on all 23 samples; the
failure is entirely on the Manitos side.

## Methodology fixes (2026-07-31, gate v1.1)

The gate was corrected in response to review; each fix changes what a verdict
means, so this run and the retracted one are not comparable:

1. **Fixed-window aggregation, not rolling-window counter subtraction.**
   `/v1/analytics/manitos-quality` now accepts `since`/`until` and aggregates a
   fixed half-open interval `[since, until)`. The gate samples that window with
   `since = started_at`, so counts are genuinely cumulative over the probe and
   stale errors can no longer age out and cancel live ones — a delta of two
   rolling snapshots could previously pass an unhealthy window.
2. **Readiness failures are part of the release decision.** Availability is now
   measured for the Manitos readiness probe as well as the Observer, and the
   final sample must be ready. A Manitos instance that is down most of the
   window now blocks the gate (this run is the proof: it failed on exactly that).
3. **Duration average scoped to the gate window.** The average is computed over
   the same `[since, until)` interval the gate observes, not the service's 720 h
   analytics horizon, so slow turns in the probe can no longer be diluted by
   older fast turns.

## Supersession

The 2026-07-23 `PASS` verdict described a 24-hour gate from a 480-second run
and is **retracted**. A fresh 24-hour passive run against a live Manitos
instance whose `/readyz` returns ready (LLM + TTS verified) is required before
any promotion decision. Until then, Phase 8 remains **not certified**.

## How to reproduce

```bash
# Run from observer/backend with the observer service up
../.venv/Scripts/python.exe -m app.ops.manitos_passive_gate \
  --observer-url http://127.0.0.1:8000 \
  --manitos-ready-url http://127.0.0.1:8765/readyz \
  --interval-seconds 20 --duration-seconds 480

# Inspect the saved run
python -c "import json; d=json.load(open('.observer-state/manitos-phase8.json')); print(d['status'], d['evaluation']['failures'])"
```
