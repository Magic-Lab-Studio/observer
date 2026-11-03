# Meta models: Llama and Muse Spark

Observer traces, prices, checks and grades agents that run on Meta's models:

- **Llama** (open weights) served locally, for example by Ollama, through its OpenAI-compatible
  endpoint;
- **Muse Spark** through the **Meta Model API** (`https://api.meta.ai/v1`, OpenAI-compatible).

Both are called with the `openai` Python client, so the Python SDK's OpenAI instrumentor records
them. Muse Spark can also be Observer's LLM judge.

## Trace an agent

```python
import os

import llm_observatory as obs
from openai import OpenAI

tracer = obs.Tracer(service_name="my-agent")
tracer.add_exporter(obs.OTLPExporter(endpoint="http://127.0.0.1:8000"))
obs.set_tracer(tracer)
obs.instrument(openai=True)

muse = OpenAI(base_url="https://api.meta.ai/v1", api_key=os.environ["META_API_KEY"])
llama = OpenAI(base_url="http://127.0.0.1:11434/v1", api_key="ollama")
```

The provider of each call comes from the client's base URL:

| Base URL | `gen_ai.provider.name` | Cost |
|---|---|---|
| `https://api.meta.ai/v1` | `meta` | from the price table (below) |
| port `11434`, or a host with `ollama` in it | `ollama` | 0 (local) |
| `https://api.openai.com/v1` or anything else | `openai` | from the price table |

`server.address` carries the host. The Providers page and Explore group calls by this provider, so
Muse Spark and Llama no longer appear as OpenAI.

Muse Spark reasons before it answers, and those tokens count against `max_completion_tokens` (the
Meta Model API takes `max_completion_tokens`; `max_tokens` is a deprecated alias). Leave room for
them or lower `reasoning_effort` (`minimal`, `low`, `medium`, `high`, `xhigh`): a 200-token cap
with the default effort can return no text at all.

## Cost

`muse-spark-1.1`, `1.2` and `1.3` cost $1.25 per million input tokens and $4.25 per million output
tokens; the contributor tier (`muse-spark-1.2-contributor`, `muse-spark-1.3-contributor`) costs
$0.10 and $0.20, and Meta may train on what it receives. Source:
<https://dev.meta.ai/docs/pricing-rate-limits/>, checked 2026-11-02. Meta bills reasoning tokens at
the output rate and includes them in `completion_tokens`, so the recorded cost includes them.

## Muse Spark as the judge

```bash
export OBSERVATORY_JUDGE_PROVIDER=meta
export META_API_KEY=...                       # or MODEL_API_KEY / OBSERVATORY_JUDGE_API_KEY
# export OBSERVATORY_JUDGE_MODEL=muse-spark-1.3              # the default
# export OBSERVATORY_JUDGE_REASONING_EFFORT=minimal          # the default
```

The judge grades output quality, instruction following and safety from the input and output each
LLM span stored, so the project's capture mode must keep content (`full`); with `metadata` there is
nothing to grade. The judge reads that content, so it is sent to Meta. Each verdict is one JSON
line; with `minimal` effort a verdict used about 100 output tokens in our runs. An answer cut off
by the cap is recorded as `judge_truncated` (retryable) and never scored.

## Compare models on the same cases

[`examples/meta/compare.py`](../examples/meta/compare.py) runs one writing agent with each model,
traces every call, reports a dataset run per model and, with `--judge`, has the judge grade every
trace:

```bash
pip install magic-lab-observer openai httpx
export META_API_KEY=...
python examples/meta/compare.py --observer http://127.0.0.1:8000 \
    --models llama3.2:3b muse-spark-1.3 --out poemario.txt --judge
```

Each case asks for a short poem and names a word the poem must contain. Observer checks every case
against its trace (no error, the word is in the reply) and the Cases page compares the two runs case
by case. The reply is read to decide the check and is not copied into the case.
