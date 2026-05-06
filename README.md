# Vantra SDK

Open source SDKs for [Vantra](https://vantra.dev) — AI agent observability with traces, costs, and anomaly alerts.

## Python

```bash
pip install vantra
```

```python
import vantra

vantra.init(api_key="van_live_...", project="my-agent")

@vantra.trace
def run_agent(message):
    return agent.run(message)  # your existing code
```

## Node.js / TypeScript

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
