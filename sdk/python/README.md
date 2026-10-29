# LLM Observatory Python SDK

Open-source LLM observability SDK for tracing and monitoring AI applications.

## Installation

```bash
pip install magic-lab-observer
```

With optional integrations:

```bash
pip install magic-lab-observer[openai,anthropic,langchain]
```

## Quick Start

The distribution name is `magic-lab-observer`; the stable Python import remains
`llm_observatory`.

```python
from llm_observatory import instrument, trace

# Auto-instrument LLM libraries
instrument(openai=True, anthropic=True)

# Or manually trace functions
@trace(name="summarize")
def summarize(text: str):
    return openai.chat.completions.create(
        model="gpt-4o",
        messages=[{"role": "user", "content": text}]
    )
```

Send the spans to an Observer backend with an exporter:

```python
from llm_observatory import OTLPExporter, Tracer, set_tracer

tracer = Tracer(service_name="my-app", environment="production")
tracer.add_exporter(OTLPExporter(endpoint="http://localhost:8000", spool_dir=".observer-spool"))
set_tracer(tracer)
```

* A span is exported when it **ends**, with its end time and final status; `end()` never turns an
  error into OK. `service_name` and `environment` travel as `service.name` and
  `deployment.environment.name`.
* Finished spans wait in a bounded queue (`max_queue_size`; overflow is counted in
  `dropped_spans`) and are sent from a background thread. The exporter retries with backoff and,
  with `spool_dir`, keeps undelivered batches on disk; `OTLPExporter.stats()` reports them.
* `tracer.shutdown()` (also run at exit) flushes what is pending. A span still open at exit is not
  exported.

## License

Apache License 2.0
