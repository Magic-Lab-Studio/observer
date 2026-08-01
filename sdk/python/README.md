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
        model="gpt-4",
        messages=[{"role": "user", "content": text}]
    )
```

## License

Apache License 2.0
