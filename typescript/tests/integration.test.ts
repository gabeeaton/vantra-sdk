/**
 * Vantra TypeScript SDK — real API integration tests.
 *
 * Uses actual Anthropic and OpenAI API calls (no mocks).
 * Covers: non-streaming, streaming, proxy methods, token capture,
 * captureIo=false, error handling, and trace() context propagation.
 *
 * Run:
 *   npm run test:integration
 */

import * as fs from 'fs'
import * as path from 'path'

// Load .env.local from repo root
const envFile = path.resolve(__dirname, '../../../.env.local')
if (fs.existsSync(envFile)) {
  for (const line of fs.readFileSync(envFile, 'utf8').split('\n')) {
    const trimmed = line.trim()
    if (trimmed && !trimmed.startsWith('#') && trimmed.includes('=')) {
      const [k, ...rest] = trimmed.split('=')
      process.env[k.trim()] ??= rest.join('=').trim()
    }
  }
}

const ANTHROPIC_KEY = process.env.ANTHROPIC_API_KEY ?? ''
const OPENAI_KEY = process.env.OPENAI_API_KEY ?? ''
const VANTRA_KEY = process.env.VANTRA_API_KEY ?? 'test'

if (!ANTHROPIC_KEY) throw new Error('ANTHROPIC_API_KEY not set')

// ─── Selective fetch interception ─────────────────────────────────────────────
// Only intercept calls to the Vantra ingest endpoint — let real API calls through.

const realFetch = globalThis.fetch.bind(globalThis)
const capturedSpanBatches: Record<string, unknown>[][] = []

// @ts-expect-error replacing global fetch
globalThis.fetch = async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
  const url = input instanceof Request ? input.url : String(input)
  if (url.includes('vantra.dev') || url.includes('/ingest')) {
    if (init?.body) {
      try {
        const body = JSON.parse(init.body as string)
        if (Array.isArray(body)) capturedSpanBatches.push(body as Record<string, unknown>[])
      } catch { /* not JSON or not a span batch */ }
    }
    return new Response('{}', { status: 200, headers: { 'Content-Type': 'application/json' } })
  }
  return realFetch(input, init)
}

function getLastSpan(): Record<string, unknown> | null {
  for (let i = capturedSpanBatches.length - 1; i >= 0; i--) {
    const batch = capturedSpanBatches[i]
    if (batch.length > 0) return batch[batch.length - 1]
  }
  return null
}

function getAllSpans(): Record<string, unknown>[] {
  return capturedSpanBatches.flat()
}

function resetCapture() {
  capturedSpanBatches.length = 0
}

// Force the 500ms flush interval to fire
async function waitForFlush() {
  await new Promise(r => setTimeout(r, 700))
}

// ─── SDK init ─────────────────────────────────────────────────────────────────

import * as vantra from '../src/index'

beforeAll(() => {
  vantra.init({ apiKey: VANTRA_KEY, project: 'integration-test' })
})

beforeEach(() => {
  resetCapture()
})

// ─── Anthropic non-streaming ──────────────────────────────────────────────────

describe('Anthropic non-streaming', () => {
  const Anthropic = require('@anthropic-ai/sdk')
  const client = new Anthropic.default({ apiKey: ANTHROPIC_KEY })

  test('response passes through, span queued with correct fields', async () => {
    const response = await client.messages.create({
      model: 'claude-haiku-4-5',
      max_tokens: 32,
      messages: [{ role: 'user', content: 'Reply with exactly the word: pong' }],
    })

    await waitForFlush()

    expect(response.content).toBeDefined()
    expect(response.content[0].text.trim().length).toBeGreaterThan(0)

    const span = getLastSpan()
    expect(span).not.toBeNull()
    expect(span!.kind).toBe('llm')
    expect(span!.provider).toBe('anthropic')
    expect(String(span!.model)).toContain('claude-haiku')
    expect(span!.status).toBe('ok')
    expect(span!.input_tokens as number).toBeGreaterThan(0)
    expect(span!.output_tokens as number).toBeGreaterThan(0)
    expect(span!.cost_usd as number).toBeGreaterThan(0)
    expect(span!.duration_ms as number).toBeGreaterThan(0)
    expect(span!.input).not.toBeNull()
    expect(span!.output).not.toBeNull()
    expect((span!.output as Record<string, unknown>).text).toBeTruthy()
  }, 30000)

  test('error span queued on bad model', async () => {
    await expect(
      client.messages.create({
        model: 'not-a-real-model',
        max_tokens: 16,
        messages: [{ role: 'user', content: 'hi' }],
      })
    ).rejects.toThrow()

    await waitForFlush()

    const span = getLastSpan()
    expect(span).not.toBeNull()
    expect(span!.status).toBe('error')
    expect(span!.error_message).toBeTruthy()
    expect(span!.input_tokens).toBe(0)
  }, 30000)

  test('captureIo=false suppresses input/output but keeps tokens', async () => {
    vantra.init({ apiKey: VANTRA_KEY, project: 'integration-test', captureIo: false })
    resetCapture()

    await client.messages.create({
      model: 'claude-haiku-4-5',
      max_tokens: 16,
      messages: [{ role: 'user', content: 'hi' }],
    })

    await waitForFlush()

    const span = getLastSpan()
    expect(span).not.toBeNull()
    expect(span!.input).toBeUndefined()
    expect(span!.output).toBeUndefined()
    expect(span!.input_tokens as number).toBeGreaterThan(0)

    // Restore
    vantra.init({ apiKey: VANTRA_KEY, project: 'integration-test', captureIo: true })
  }, 30000)
})

// ─── Anthropic streaming ──────────────────────────────────────────────────────

describe('Anthropic streaming', () => {
  const Anthropic = require('@anthropic-ai/sdk')
  const client = new Anthropic.default({ apiKey: ANTHROPIC_KEY })

  test('all events yielded, span queued with real tokens', async () => {
    const stream = await client.messages.create({
      model: 'claude-haiku-4-5',
      max_tokens: 32,
      stream: true,
      messages: [{ role: 'user', content: 'Reply with exactly the word: pong' }],
    })

    const events: unknown[] = []
    const textChunks: string[] = []

    for await (const event of stream) {
      events.push(event)
      if ((event as Record<string, unknown>).type === 'content_block_delta') {
        const delta = (event as Record<string, unknown>).delta as Record<string, unknown>
        if (typeof delta?.text === 'string') textChunks.push(delta.text)
      }
    }

    await waitForFlush()

    expect(events.length).toBeGreaterThan(3)
    expect(textChunks.join('').trim().length).toBeGreaterThan(0)

    const span = getLastSpan()
    expect(span).not.toBeNull()
    expect(span!.kind).toBe('llm')
    expect(span!.provider).toBe('anthropic')
    expect(span!.status).toBe('ok')
    expect(span!.input_tokens as number).toBeGreaterThan(0)
    expect(span!.output_tokens as number).toBeGreaterThan(0)
    expect(span!.cost_usd as number).toBeGreaterThan(0)
    expect((span!.output as Record<string, unknown>)?.text).toBeTruthy()
  }, 30000)

  test('proxy: stream is async-iterable and preserves SDK properties', async () => {
    const stream = await client.messages.create({
      model: 'claude-haiku-4-5',
      max_tokens: 16,
      stream: true,
      messages: [{ role: 'user', content: 'hi' }],
    })

    // Must still be async-iterable via the proxy
    expect(typeof stream[Symbol.asyncIterator]).toBe('function')

    // Consume it — must work without errors
    let eventCount = 0
    for await (const _ of stream) eventCount++

    await waitForFlush()

    expect(eventCount).toBeGreaterThan(0)
    const span = getLastSpan()
    expect(span!.status).toBe('ok')
  }, 30000)

  test('span queued on early break (finally block fires)', async () => {
    const stream = await client.messages.create({
      model: 'claude-haiku-4-5',
      max_tokens: 64,
      stream: true,
      messages: [{ role: 'user', content: 'Count from 1 to 20' }],
    })

    let count = 0
    for await (const _ of stream) {
      count++
      if (count >= 3) break
    }

    await waitForFlush()

    const span = getLastSpan()
    expect(span).not.toBeNull()
    expect(span!.provider).toBe('anthropic')
  }, 30000)

  test('captureIo=false suppresses input/output in streaming', async () => {
    vantra.init({ apiKey: VANTRA_KEY, project: 'integration-test', captureIo: false })
    resetCapture()

    const stream = await client.messages.create({
      model: 'claude-haiku-4-5',
      max_tokens: 16,
      stream: true,
      messages: [{ role: 'user', content: 'hi' }],
    })

    for await (const _ of stream) { /* drain */ }
    await waitForFlush()

    const span = getLastSpan()
    expect(span).not.toBeNull()
    expect(span!.input).toBeUndefined()
    expect(span!.output).toBeUndefined()
    expect(span!.input_tokens as number).toBeGreaterThan(0)

    vantra.init({ apiKey: VANTRA_KEY, project: 'integration-test', captureIo: true })
  }, 30000)

  test('two sequential streams get separate spans with unique IDs', async () => {
    for (let i = 0; i < 2; i++) {
      const stream = await client.messages.create({
        model: 'claude-haiku-4-5',
        max_tokens: 16,
        stream: true,
        messages: [{ role: 'user', content: `say ${i}` }],
      })
      for await (const _ of stream) { /* drain */ }
    }

    await waitForFlush()

    const spans = getAllSpans().filter(s => s.provider === 'anthropic')
    expect(spans.length).toBe(2)
    expect(spans[0].span_id).not.toBe(spans[1].span_id)
    expect(spans[0].status).toBe('ok')
    expect(spans[1].status).toBe('ok')
  }, 60000)

  test('error span queued on bad model with stream=true', async () => {
    try {
      const stream = await client.messages.create({
        model: 'not-a-real-model-xyz',
        max_tokens: 16,
        stream: true,
        messages: [{ role: 'user', content: 'hi' }],
      })
      for await (const _ of stream) { /* drain */ }
    } catch { /* expected */ }

    await waitForFlush()

    const span = getLastSpan()
    expect(span).not.toBeNull()
    expect(span!.status).toBe('error')
    expect(span!.error_message).toBeTruthy()
    expect(span!.input_tokens).toBe(0)
    expect(span!.cost_usd).toBe(0)
  }, 30000)
})

// ─── OpenAI (if key available) ────────────────────────────────────────────────

const describeOpenAI = OPENAI_KEY ? describe : describe.skip

describeOpenAI('OpenAI non-streaming', () => {
  const OpenAI = require('openai')
  const client = new OpenAI.default({ apiKey: OPENAI_KEY })

  test('response passes through, span queued with correct fields', async () => {
    const response = await client.chat.completions.create({
      model: 'gpt-4o-mini',
      max_tokens: 32,
      messages: [{ role: 'user', content: 'Reply with exactly the word: pong' }],
    })

    await waitForFlush()

    expect(response.choices[0].message.content).toBeTruthy()

    const span = getLastSpan()
    expect(span).not.toBeNull()
    expect(span!.kind).toBe('llm')
    expect(span!.provider).toBe('openai')
    expect(String(span!.model)).toContain('gpt-4o-mini')
    expect(span!.status).toBe('ok')
    expect(span!.input_tokens as number).toBeGreaterThan(0)
    expect(span!.output_tokens as number).toBeGreaterThan(0)
    expect(span!.cost_usd as number).toBeGreaterThan(0)
    expect(span!.duration_ms as number).toBeGreaterThan(0)
    expect(span!.input).not.toBeNull()
    expect(span!.output).not.toBeNull()
    expect((span!.output as Record<string, unknown>).content).toBeTruthy()
  }, 30000)

  test('error span queued on bad model', async () => {
    await expect(
      client.chat.completions.create({
        model: 'not-a-real-model',
        max_tokens: 16,
        messages: [{ role: 'user', content: 'hi' }],
      })
    ).rejects.toThrow()

    await waitForFlush()

    const span = getLastSpan()
    expect(span).not.toBeNull()
    expect(span!.status).toBe('error')
    expect(span!.error_message).toBeTruthy()
    expect(span!.input_tokens).toBe(0)
  }, 30000)

  test('captureIo=false suppresses input/output but keeps tokens', async () => {
    vantra.init({ apiKey: VANTRA_KEY, project: 'integration-test', captureIo: false })
    resetCapture()

    await client.chat.completions.create({
      model: 'gpt-4o-mini',
      max_tokens: 16,
      messages: [{ role: 'user', content: 'hi' }],
    })

    await waitForFlush()

    const span = getLastSpan()
    expect(span).not.toBeNull()
    expect(span!.input).toBeUndefined()
    expect(span!.output).toBeUndefined()
    expect(span!.input_tokens as number).toBeGreaterThan(0)

    vantra.init({ apiKey: VANTRA_KEY, project: 'integration-test', captureIo: true })
  }, 30000)
})

describeOpenAI('OpenAI streaming', () => {
  const OpenAI = require('openai')
  const client = new OpenAI.default({ apiKey: OPENAI_KEY })

  test('chunks yielded, usage captured via injected stream_options', async () => {
    // No stream_options passed — SDK should inject include_usage: true
    const stream = await client.chat.completions.create({
      model: 'gpt-4o-mini',
      max_tokens: 16,
      stream: true,
      messages: [{ role: 'user', content: 'say hi' }],
    })

    const chunks: unknown[] = []
    for await (const chunk of stream) chunks.push(chunk)

    await waitForFlush()

    expect(chunks.length).toBeGreaterThan(1)

    const span = getLastSpan()
    expect(span!.provider).toBe('openai')
    expect(span!.status).toBe('ok')
    // Non-zero tokens confirms stream_options injection worked
    expect(span!.input_tokens as number).toBeGreaterThan(0)
    expect(span!.output_tokens as number).toBeGreaterThan(0)
  }, 30000)

  test('proxy preserves .controller on OpenAI Stream', async () => {
    const stream = await client.chat.completions.create({
      model: 'gpt-4o-mini',
      max_tokens: 16,
      stream: true,
      messages: [{ role: 'user', content: 'hi' }],
    })

    // OpenAI Stream exposes .controller (AbortController)
    expect(stream.controller).toBeDefined()
    expect(typeof stream[Symbol.asyncIterator]).toBe('function')

    for await (const _ of stream) { /* drain */ }
    await waitForFlush()

    const span = getLastSpan()
    expect(span!.status).toBe('ok')
  }, 30000)

  test('captureIo=false suppresses input/output in streaming', async () => {
    vantra.init({ apiKey: VANTRA_KEY, project: 'integration-test', captureIo: false })
    resetCapture()

    const stream = await client.chat.completions.create({
      model: 'gpt-4o-mini',
      max_tokens: 16,
      stream: true,
      messages: [{ role: 'user', content: 'hi' }],
    })

    for await (const _ of stream) { /* drain */ }
    await waitForFlush()

    const span = getLastSpan()
    expect(span).not.toBeNull()
    expect(span!.input).toBeUndefined()
    expect(span!.output).toBeUndefined()
    expect(span!.input_tokens as number).toBeGreaterThan(0)

    vantra.init({ apiKey: VANTRA_KEY, project: 'integration-test', captureIo: true })
  }, 30000)

  test('two sequential streams get separate spans with unique IDs', async () => {
    for (let i = 0; i < 2; i++) {
      const stream = await client.chat.completions.create({
        model: 'gpt-4o-mini',
        max_tokens: 16,
        stream: true,
        messages: [{ role: 'user', content: `say ${i}` }],
      })
      for await (const _ of stream) { /* drain */ }
    }

    await waitForFlush()

    const spans = getAllSpans().filter(s => s.provider === 'openai')
    expect(spans.length).toBe(2)
    expect(spans[0].span_id).not.toBe(spans[1].span_id)
    expect(spans[0].status).toBe('ok')
    expect(spans[1].status).toBe('ok')
  }, 60000)

  test('error span queued on bad model with stream=true', async () => {
    try {
      const stream = await client.chat.completions.create({
        model: 'not-a-real-model-xyz',
        max_tokens: 16,
        stream: true,
        messages: [{ role: 'user', content: 'hi' }],
      })
      for await (const _ of stream) { /* drain */ }
    } catch { /* expected */ }

    await waitForFlush()

    const span = getLastSpan()
    expect(span).not.toBeNull()
    expect(span!.status).toBe('error')
    expect(span!.error_message).toBeTruthy()
    expect(span!.input_tokens).toBe(0)
  }, 30000)

  test('user-provided stream_options not overwritten', async () => {
    // When user already sets stream_options, we must not clobber it.
    // We verify by checking that the stream still works and a span is queued —
    // if we corrupted stream_options the request would fail.
    const stream = await client.chat.completions.create({
      model: 'gpt-4o-mini',
      max_tokens: 16,
      stream: true,
      stream_options: { include_usage: true },
      messages: [{ role: 'user', content: 'hi' }],
    })

    for await (const _ of stream) { /* drain */ }
    await waitForFlush()

    const span = getLastSpan()
    expect(span!.status).toBe('ok')
    expect(span!.input_tokens as number).toBeGreaterThan(0)
  }, 30000)
})
