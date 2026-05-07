# Vantra SDK

Open source SDKs for [Vantra](https://vantra.dev) — AI agent observability with traces, costs, and anomaly alerts.

## Python

[![PyPI version](https://img.shields.io/pypi/v/vantra)](https://pypi.org/project/vantra/)

```bash
pip install vantra
```

```python
import vantra

vantra.init(api_key="van_live_...", project="my-agent")

@vantra.trace
def run_agent(message: str):
    return agent.run(message)  # your existing code
```

## Node.js / TypeScript

[![npm version](https://img.shields.io/npm/v/vantra-sdk)](https://www.npmjs.com/package/vantra-sdk)

```bash
npm install vantra-sdk
```

```typescript
import { init, trace } from 'vantra-sdk'

init({ apiKey: 'van_live_...', project: 'my-agent' })

const runAgent = trace(async function runAgent(message: string) {
  return agent.run(message)  // your existing code
})
```

## What gets captured automatically

- Every LLM call (OpenAI, Anthropic) — tokens, cost, latency, model
- Waterfall trace view of your full agent run
- Errors with stack traces
- Cost per session and per model

## Docs

Full documentation at [vantra.dev/docs](https://vantra.dev/docs)

## License

MIT
