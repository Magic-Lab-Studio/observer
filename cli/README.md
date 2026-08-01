# LLM Observatory CLI

Command-line interface for LLM Observatory.

## Installation

```bash
pip install magic-lab-observer-cli
```

Before the distribution is published, install this directory from the checked-out
repository with `python -m pip install -e ./cli`.

## Usage

```bash
# Start the server
llm-observatory serve

# Run an evaluation
llm-observatory evaluate --trace-id abc123 --evaluator llm_judge --criteria accuracy,safety

# Export traces
llm-observatory export --format json --output traces.json

# Check server status
llm-observatory status
```

## License

Apache License 2.0
