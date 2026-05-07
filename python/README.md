# Vantra Python SDK

Observability for AI agents — traces, costs, and alerts in 3 lines of code.

[![PyPI version](https://img.shields.io/pypi/v/vantra)](https://pypi.org/project/vantra/)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

## Install

```bash
pip install vantra
```

## Quickstart

```python
import vantra

vantra.init(
    api_key="van_live_...",
    project="my-agent"
)

@vantra.trace
def run_agent(message: str):
    return agent.run(message)  # your existing code — untouched
```

Every call to `run_agent` appears in your [Vantra dashboard](https://vantra.dev/dashboard) with timing, token usage, cost, and status.

## OpenAI auto-patch

```python
import vantra
import openai

vantra.init(api_key="van_live_...", project="my-agent")
client = openai.OpenAI()

@vantra.trace
def ask(question: str) -> str:
    # captured automatically — tokens, cost, latency
    response = client.chat.completions.create(
        model="gpt-4o",
        messages=[{"role": "user", "content": question}]
    )
    return response.choices[0].message.content
```

## Anthropic auto-patch

```python
import vantra
import anthropic

vantra.init(api_key="van_live_...", project="my-agent")
client = anthropic.Anthropic()

@vantra.trace
def ask(question: str) -> str:
    response = client.messages.create(
        model="claude-opus-4-5",
        max_tokens=1024,
        messages=[{"role": "user", "content": question}]
    )
    return response.content[0].text
```

## Manual spans

```python
@vantra.trace
def run_agent(message: str):
    with vantra.span("search_kb", kind="tool") as span:
        results = search(message)
        span.set_output({"results": results, "count": len(results)})

    with vantra.span("generate", kind="llm"):
        return llm.chat(message, context=results)
```

## Options

| Parameter | Type | Default | Description |
|---|---|---|---|
| `api_key` | str | required | Your `van_live_...` key from Settings |
| `project` | str | required | Project name shown in dashboard |
| `patch_openai` | bool | `True` | Auto-instrument OpenAI calls |
| `patch_anthropic` | bool | `True` | Auto-instrument Anthropic calls |
| `enabled` | bool | `True` | Set `False` to disable in tests |

## Links

- [vantra.dev](https://vantra.dev)
- [Docs](https://vantra.dev/docs)
- [Dashboard](https://vantra.dev/dashboard)
- [GitHub](https://github.com/gabeeaton/vantra-sdk)
