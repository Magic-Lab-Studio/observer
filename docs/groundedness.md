# Groundedness

Groundedness asks whether a reply stands on what the agent actually read in that trace. It is
configured per project and is **off by default**: with mode `off`, nothing is evaluated and
nothing is sent anywhere.

## Modes

| Mode | What runs | Status |
|---|---|---|
| `off` | nothing | default |
| `rules` | deterministic checks, no model | built |
| `judge` | a model judges each claim against the context | not built yet |
| `rules+judge` | both | not built yet |

`PUT` with `judge` or `rules+judge` answers 422 until the judge is built; the schema accepts the
names so stored configurations stay valid when it is.

## What `rules` checks

- **Reply:** the output of the trace's root span or, without one, of its last LLM span.
- **Context:** the outputs of the evidence spans. By default these are the `tool` and `retrieval`
  spans; `context.span_types` and `context.name_patterns` (glob on the span name) change that.
  `context.include_input` adds the root span's input. Context is capped per span
  (`max_chars_per_span`, 4000) and per trace (`max_chars_per_trace`, 20000), and the result says
  how many characters were dropped.
- **Claims:** sentences of four words or more. Questions and code blocks are not claims.

A claim is **supported** when:
- it cites a source the trace really fetched: a URL found in an evidence span's input or output,
  or an id from `retrieval.sources`;
- or every number it states (two digits or more) appears in the context;
- or at least 60 % of its content words appear in the context.

It is **unsupported** when:
- it cites something the trace never read (`unfetched_source`);
- or it states a number the context does not contain (`invented_number`). One such number makes
  the whole reply `ungrounded`: an invented figure is the clearest sign of an invented fact;
- or nothing supports it (`no_support`).

Results and their scores:

| Status | When | Score |
|---|---|---|
| `grounded` | share of supported claims ≥ `threshold` (0.8) | the share |
| `partial` | share ≥ `threshold` / 2 | the share |
| `ungrounded` | share below half the threshold, or an invented number | the share |
| `unknown` | no reply text (`no_reply_text`) or no context (`no_context`); never a pass | none |
| `not_applicable` | outside `applies_to`, or a reply with no claims | none |

`applies_to` lists the tasks (`observer.task`), intents or trace tags that need a source. Empty
means every trace.

The unsupported claims (up to 10, 160 characters each) are kept in the evaluation as evidence, each
with its code and detail.

## Privacy

Rules read content Observer already stored, so they follow the project's capture mode:
- a `metadata` project keeps no input or output, so its traces come out `unknown`;
- a `redacted` project is checked on the redacted text.

Nothing leaves the machine. ManitOS exports metadata only, so its turns are `unknown` here.

## Where it shows

- **Evaluations:**
  - each result is an evaluation of type `groundedness` with rubric `groundedness@1`, kept apart
    from the `rule_based` turn-health scores;
  - "Groundedness by model" shows, per model, the average score, the share ungrounded and how many
    replies could not be judged;
  - the detail lists the unsupported claims.
- **Explore:** the view `groundedness` (measures `score`, `ungrounded`, `claims`,
  `unsupported_count`; dimensions model, provider, status, reason, environment).
- **Alerts:** the metric `ungrounded_rate`, the share of judged replies that came out `ungrounded`
  in the window.
- **Test cases:**
  - `expect.groundedness.min_level` (`partial` or `grounded`) is checked when a run is recorded,
    under the project's current configuration;
  - `unknown` (mode off, or no content) is never a pass.

## API

- `GET /v1/projects/{id}/config`: every section.
- `GET|PUT|DELETE /v1/projects/{id}/config/groundedness`: a closed schema; an unknown key is a 422,
  and `DELETE` returns to the default (`off`).
- `POST /v1/projects/{id}/groundedness/evaluate` `{"trace_ids": [...], "force": false}`: scores now
  instead of waiting for the sweep (409 while off). Without `trace_ids` it scores the project's
  ended traces of the last 7 days that have no result yet.

The automatic sweep (the same loop as turn evaluations, `OBSERVATORY_AUTO_EVAL_INTERVAL_SEC`)
scores every project whose mode runs `rules`.

```json
{"mode": "rules", "threshold": 0.8, "applies_to": [],
 "context": {"span_types": ["tool", "retrieval"], "name_patterns": [], "include_input": false,
             "max_chars_per_span": 4000, "max_chars_per_trace": 20000}}
```

Configuration lives in the `project_configs` table (migration `20261106_configs`), one row per
project and section. It is not in `project_settings`, because resetting the capture mode deletes
that whole row.

## Limits

- Rules catch invented figures and citations to sources that were never read; they do not catch a
  plausible sentence that reuses the context's words to say something false. That needs the judge.
- Numbers are compared as digits (`1,234.5` and `1234.5` match). One-digit numbers are not checked.
- The `judge` modes are not built yet.
