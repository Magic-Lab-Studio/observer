# Agent span contract (`observer.agent.v1`)

An agent tells Observer **how a turn ended and why** with plain span attributes. Observer then
answers "why did the agent not answer?" on the trace page, in Explore and in alerts, without
reading any content.

The contract works on every ingestion path: the SDK batch (`/v1/traces/batch`), OTLP and the
ManitOS envelope. Every key is metadata (names, codes, counts, ids), so it is kept under the
`metadata` capture mode. Unknown keys are ignored, never rejected; the contract only grows.
`GET /v1/agent/contract` returns it as data.

## Outcome of a turn

| Attribute | Value |
|---|---|
| `observer.outcome` | `answered`, `degraded`, `abstained`, `clarification`, `refused`, `failed` |
| `observer.outcome.reason` | a short reason code, e.g. `coverage_below_min` |
| `observer.task` | the kind of task, e.g. `support_answer` (an Explore dimension) |
| `user.id` / `enduser.id` | an opaque user id |

Set them on the root span. When a turn does not declare its outcome, Observer derives it on read.
In order, the first rule that applies wins:

1. **`observer.outcome`** on a span. The root span's value wins; otherwise the span that ended
   last.
2. The `kind` of a **`close`** span (`answer`, `clarify`, `abstain`, `refuse`, `fail`).
3. A trace in **error** is `failed`.
4. A **degraded** reply is `degraded`: the ManitOS degraded or truncated flags, or the **last**
   LLM span cut off by its token limit (`length` / `max_tokens`). An early cut-off the agent
   recovered from does not count.
5. **`answered`** needs positive evidence: a ManitOS `model.answered` span, or the last LLM span
   finished with `stop` / `end_turn` on a trace that did not fail.
6. Otherwise the outcome is **`unknown`**. An ok trace is not counted as an answer.

The reason comes from `observer.outcome.reason`, else the close span's `reason_code`, else the
reason codes of the last gate that stopped the turn (only for turns that did not answer). Every turn reports which rule gave its
outcome (`outcome_source`: `attribute`, `close`, `status`, `flags`, `evidence`, `none`).

**Abstained with evidence:** a turn that abstained although its retrieval returned passages. It
is usually a gate that is too strict. It is flagged on the turn and has its own alert metric.

## Agent spans

The span type comes from the transport's span type (SDK batch, ManitOS envelope). OTLP has no
span type, so set `observer.span.type` there. OpenInference `RETRIEVER` spans count as
`retrieval`.

| Type | Keys |
|---|---|
| `decision` | `decision.kind`, `decision.options`, `decision.choice`, `decision.rejected` (`name:reason_code` items), `decision.scores`, `decision.confidence`, `decision.final_by` |
| `tool` | `tool.name`, `tool.arg_names`, `tool.result_size`, `tool.error_code`, `tool.args_digest`, `tool.call_id` |
| `retrieval` | `retrieval.name`, `retrieval.passages`, `retrieval.sources` (ids, not URLs), `retrieval.top_score`, `retrieval.index` |
| `gate` | `gate.name`, `gate.stage`, `gate.status` (`pass`, `stop`, `skip`), `gate.requirement`, `gate.reason_codes` |
| `verification` | `verification.claims_accepted`, `verification.claims_rejected`, `verification.claims_pruned`, `verification.reason_codes` |
| `replan` | `replan.reason`, `replan.attempt` |
| `close` | `close.kind` (`answer`, `clarify`, `abstain`, `refuse`, `fail`), `close.reason_code` |

`observer.depends_on` on a span names the span ids it needs. Step 14 of the roadmap uses it to
tell calls that must run in sequence from calls that could run in parallel.

Reason codes can be a list or a comma-separated string. Keep values short: a code, not a sentence.
Argument values, passages and replies are content and belong in the span's input and output,
where the capture mode decides what is stored.

## Where it shows

- **`GET /v1/agent/turns?project_id=…&hours=…[&outcome=…]`**: each turn's outcome, source,
  reason, gates and retrieved passage count, plus the counts per outcome.
- **`GET /v1/traces/{id}/diagnostics`**: the same fields for one trace, next to the repeated-call
  warning.
- **`GET /v1/governance/turns/{id}`**: retrieval, gate, verification, replan and close steps in
  the turn's path, by stored type or by `observer.span.type`. ManitOS steps are unchanged.
- **Explore** (turns view):
  - measures `answered`, `abstained`, `clarification`, `refused`, `failed`, `outcome_degraded`
    and `abstained_with_evidence` (0/1 each);
  - dimensions `outcome`, `outcome_source` and `task`.
- **Alert metrics:**
  - `outcome_<outcome>_rate` for each outcome, and `outcome_unknown_rate`. Every turn is in the
    denominator, so the rates add up to 1.
  - `abstained_with_evidence_rate`: the share of abstentions whose retrieval did return
    passages.

## Example (OTLP attributes)

```text
root      observer.task=support_answer  user.id=u-41
retrieval observer.span.type=retrieval  retrieval.passages=4  retrieval.sources=[kb:12, kb:40]
gate      observer.span.type=gate  gate.name=coverage  gate.status=stop  gate.reason_codes=[coverage_below_min]
close     observer.span.type=close  close.kind=abstain
```

Observer reports this turn as `abstained`, source `close`, reason `coverage_below_min`, and flags
it as abstained with evidence.

The synthetic orchestrator in [`examples/orchestrator`](../examples/orchestrator) plants every
outcome and checks Observer's answer:

```bash
python examples/orchestrator/run_bed.py --endpoint http://127.0.0.1:8000
```
