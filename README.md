# Vantra SDK

Open source SDKs for [Vantra](https://vantra.dev) — AI agent observability with traces, costs, and anomaly alerts in 3 lines of code.

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
    return agent.run(message)  # your existing code, zero changes
```

### Streaming

Streaming is captured automatically — no changes needed:

```python
stream = client.chat.completions.create(
    model="gpt-4o", messages=[...], stream=True
)
for chunk in stream:   # tokens, cost, and content captured in the background
    print(chunk)
```

Works the same for Anthropic streaming.

### Prompt versioning

```python
@vantra.trace(prompt_version="v2")
def run_agent(message: str):
    ...
```

### Options

```python
vantra.init(
    api_key="van_live_...",
    project="my-agent",
    capture_io=False,   # don't log message content (just tokens + cost)
)
```

### Manual spans

```python
with vantra.span("retrieve_docs", kind="tool") as s:
    docs = search(query)
    s.set_output(docs)
```

---

## Node.js / TypeScript

[![npm version](https://img.shields.io/npm/v/vantra-sdk)](https://www.npmjs.com/package/vantra-sdk)

```bash
npm install vantra-sdk
```

```typescript
import { init, trace } from 'vantra-sdk'

init({ apiKey: 'van_live_...', project: 'my-agent' })

const runAgent = trace(async function runAgent(message: string) {
  return agent.run(message)  // your existing code, zero changes
})
```

### Streaming

```typescript
const stream = await client.chat.completions.create({
  model: 'gpt-4o', messages: [...], stream: true,
})
for await (const chunk of stream) {  // tokens, cost, and content captured
  process.stdout.write(chunk.choices[0]?.delta?.content ?? '')
}
```

Works the same for Anthropic streaming. All SDK methods (`.controller`, `.toReadableStream()`) are preserved via Proxy.

### Prompt versioning

```typescript
const runAgent = trace(myFn, { promptVersion: 'v2' })
```

### Options

```typescript
init({
  apiKey: 'van_live_...',
  project: 'my-agent',
  captureIo: false,   // don't log message content (just tokens + cost)
})
```

### Manual spans

```typescript
await span('retrieve_docs', async (ctx) => {
  const docs = await search(query)
  ctx.setOutput(docs)
}, { kind: 'tool' })
```

---

## What gets captured automatically

- Every LLM call (OpenAI, Anthropic) — tokens, cost, latency, model
- Streaming calls — full token counts and assembled output
- Waterfall trace view of your full agent run
- Errors with stack traces
- Cost per session and per model

## Tests

- Python: 141 unit tests + 53 real-API integration tests
- TypeScript: 31 unit tests + 18 real-API integration tests

## Docs

Full documentation at [vantra.dev/docs](https://vantra.dev/docs)

## License

MIT
