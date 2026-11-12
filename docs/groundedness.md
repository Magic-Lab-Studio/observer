# Groundedness

Groundedness asks whether a reply stands on what the agent actually read in that trace. It is
configured per project and is **off by default**: with mode `off`, nothing is evaluated and
nothing is sent anywhere.

## Modes

| Mode | What runs | Status |
|---|---|---|
| `off` | nothing | default |
| `rules` | deterministic checks, no model | built |
| `judge` | a model lists the reply's claims the context does not support | built |
| `rules+judge` | both; the worse result wins, both kept in the detail | built |

## The judge

The judge gets the same bounded context as the rules (evidence spans, user message) and the reply,
and answers JSON: how many claims the reply makes, which are unsupported (`not_in_context` or
`contradicted`) and a level. Observer then:

- drops any "unsupported claim" the reply does not contain (a quote, not a paraphrase: three quarters
  of its words and numbers must be in the reply). A small local judge listed the user's own question
  as a claim of a correct reply; `judge_claims_dropped` counts them;
- scores the share of supported claims; the level is the judge's;
- makes the result `unknown`, never a pass, when the judge fails (`judge_unavailable:<reason>`) or
  answers something that is not the expected JSON (`judge_invalid`). In `rules+judge` a failed judge
  makes the combined result `unknown` too.

Configuration (`judge`, `sample_rate`, `max_per_pass` in the section; keys only in the server's
environment, `OBSERVATORY_JUDGE_*`, never in the project's settings):

```json
{"mode": "judge",
 "judge": {"provider": "openai-compatible", "model": "llama3.2:3b",
           "base_url": "http://127.0.0.1:11434/v1", "allow_remote": false},
 "sample_rate": 1.0, "max_per_pass": 20}
```

Unset judge fields fall back to `OBSERVATORY_JUDGE_PROVIDER`, `_MODEL`, `_BASE_URL`. `sample_rate`
picks traces by a hash of their id (the same trace is always in or out); traces left out are
`not_applicable` (`not_sampled`). `max_per_pass` caps the traces one pass sends to the judge.

Measured on 2026-11-12 with `llama3.2:3b` on a local Ollama (CPU, no GPU), the planted twins (a refund
of 180 USD the tool output does not contain; the same reply with the tool's 120 USD), three runs:
the invented one `ungrounded` with «180 USD» listed every time, the twin `grounded` every time.
`qwen3.5:4b` did not answer within the judge's 60 s on this machine.

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
- or every number it states (two digits or more) appears in the context or in what the user said
  (the root span's input: a reply may repeat "12 people" without inventing it);
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

The judge adds:
- `off` sends nothing anywhere;
- a `metadata` project is `unknown` (`metadata_capture`) without calling the judge;
- a judge that is not on this machine (anything but an OpenAI-compatible endpoint on loopback)
  receives the context only when the project sets `judge.allow_remote`; otherwise the result is
  `unknown` (`judge_remote_not_allowed`).

With `rules`, or a local judge, nothing leaves the machine. ManitOS exports metadata only, so its
turns are `unknown` here.

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
scores every project whose mode is not `off`.

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
  plausible sentence that reuses the context's words to say something false. The judge can, as well
  as its model reads; a small local model is a weak reader, so pair it with the rules.
- Numbers are compared as values, however they are written: a tool's `1492650.0` matches
  `1.492.650`, `1,492,650` and `1492650`; `4012.5` matches `4.012,5`. A single separator before
  exactly three digits (`1.492`) counts both ways. One-digit numbers are not checked.
- The user's numbers only clear the number check: they never make a sentence supported by its words.
