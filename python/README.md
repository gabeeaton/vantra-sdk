# Vantra Python SDK

Observability for AI agents — traces, costs, and alerts in 3 lines of code.

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
    # your existing code — completely untouched
    return agent.run(message)
```

Every call to `run_agent` appears as a trace in your [Vantra dashboard](https://vantra.io/dashboard) with timing, token usage, cost, and status.

## Nested spans

```python
@vantra.trace
def run_agent(message: str):
    with vantra.span("search", kind="tool") as span:
        results = search(message)
        span.set_output({"results": results})

    with vantra.span("generate", kind="llm"):
        return llm.chat(message, context=results)
```

## Auto-patching

Vantra automatically captures OpenAI and Anthropic calls after `init()` — no extra code needed.

```python
import vantra
import openai

vantra.init(api_key="van_live_...", project="my-agent")
client = openai.OpenAI()

@vantra.trace
def ask(question: str) -> str:
    # This call is captured automatically
    response = client.chat.completions.create(
        model="gpt-4o",
        messages=[{"role": "user", "content": question}]
    )
    return response.choices[0].message.content
```

## Links

- [Dashboard](https://vantra.io/dashboard)
- [Full documentation](https://vantra.io/docs)
- [Pricing](https://vantra.io/#pricing)
