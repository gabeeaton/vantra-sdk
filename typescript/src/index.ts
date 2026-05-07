import { calculateCost } from './costs'

const VANTRA_ENDPOINT = process.env.VANTRA_ENDPOINT ?? 'https://vantra.dev/api/v1/ingest'

const config = {
  apiKey: null as string | null,
  project: null as string | null,
  enabled: true,
  captureIo: true,
}

let asyncLocalStorage: import('async_hooks').AsyncLocalStorage<{ traceId: string; spans: SpanData[] }> | null = null
try {
  const { AsyncLocalStorage } = require('async_hooks')
  asyncLocalStorage = new AsyncLocalStorage()
} catch {
  // not available
}

interface SpanData {
  span_id: string
  trace_id: string | null
  name: string
  kind: string
  provider?: string
  model?: string
  project: string | null
  start_time: number
  end_time: number
  duration_ms: number
  status: string
  error_message?: string | null
  input_tokens?: number
  output_tokens?: number
  cost_usd?: number
  input?: unknown
  output?: unknown
  metadata?: Record<string, unknown>
}

const queue: SpanData[] = []
let flushTimer: ReturnType<typeof setInterval> | null = null

function post(path: string, data: unknown): void {
  if (!config.apiKey) return
  const url = `${VANTRA_ENDPOINT}${path}`
  const body = JSON.stringify(data)
  const headers = {
    'Authorization': `Bearer ${config.apiKey}`,
    'Content-Type': 'application/json',
  }
  if (typeof fetch !== 'undefined') {
    fetch(url, { method: 'POST', headers, body }).catch(() => {})
  } else {
    try {
      const https = require('https')
      const http = require('http')
      const u = new URL(url)
      const mod = u.protocol === 'https:' ? https : http
      const req = mod.request({ hostname: u.hostname, path: u.pathname, method: 'POST', headers: { ...headers, 'Content-Length': Buffer.byteLength(body) } }, () => {})
      req.on('error', () => {})
      req.write(body)
      req.end()
    } catch { /* silent */ }
  }
}

function flush(): void {
  if (!queue.length) return
  const items = queue.splice(0)
  post('/spans', items)
}

function queueSpan(span: SpanData): void {
  const store = asyncLocalStorage?.getStore()
  if (store) {
    store.spans.push(span)
  } else {
    queue.push(span)
  }
}

function truncate(data: unknown, maxChars = 2000): unknown {
  try {
    const s = JSON.stringify(data)
    if (s.length <= maxChars) return data
    return { _truncated: true, preview: s.slice(0, maxChars) }
  } catch {
    return String(data).slice(0, maxChars)
  }
}

// Proxy the original stream so all SDK methods (.controller, .toReadableStream(), etc.)
// remain accessible while iteration flows through our tracking generator.
function _proxyStream<T extends object>(original: T, gen: AsyncGenerator<unknown>): T {
  return new Proxy(original, {
    get(target, prop) {
      if (prop === Symbol.asyncIterator) {
        return () => gen[Symbol.asyncIterator]()
      }
      const val = (target as Record<string | symbol, unknown>)[prop]
      return typeof val === 'function' ? (val as (...args: unknown[]) => unknown).bind(target) : val
    },
  })
}

export interface InitOptions {
  apiKey: string
  project: string
  enabled?: boolean
  captureIo?: boolean
  patchOpenAI?: boolean
  patchAnthropic?: boolean
}

export function init(options: InitOptions): void {
  config.apiKey = options.apiKey
  config.project = options.project
  config.enabled = options.enabled ?? true
  config.captureIo = options.captureIo ?? true

  if (!flushTimer) {
    flushTimer = setInterval(flush, 500)
    if (flushTimer.unref) flushTimer.unref()
  }

  if (options.patchOpenAI !== false) patchOpenAI()
  if (options.patchAnthropic !== false) patchAnthropic()
}

export function trace<T extends (...args: unknown[]) => unknown>(
  fn: T,
  options?: { name?: string; promptVersion?: string }
): T {
  const traceName = options?.name ?? fn.name ?? 'trace'
  const promptVersion = options?.promptVersion ? String(options.promptVersion).slice(0, 100) : undefined

  const wrapped = function (this: unknown, ...args: unknown[]) {
    if (!config.enabled) return fn.apply(this, args)

    const traceId = randomId()
    const start = Date.now()
    let status = 'ok'
    let errorMsg: string | null = null

    const store = { traceId, spans: [] as SpanData[] }

    const finish = () => {
      const end = Date.now()
      const collected = store.spans
      const totalTokens = collected.reduce((s, sp) => s + (sp.input_tokens ?? 0) + (sp.output_tokens ?? 0), 0)
      const totalCost = collected.reduce((s, sp) => s + (sp.cost_usd ?? 0), 0)
      post('/traces', {
        trace_id: traceId,
        name: traceName,
        project: config.project,
        start_time: start / 1000,
        end_time: end / 1000,
        duration_ms: end - start,
        status,
        error_message: errorMsg,
        total_tokens: totalTokens,
        total_cost_usd: totalCost,
        ...(promptVersion ? { prompt_version: promptVersion } : {}),
      })
      if (collected.length) post('/spans', collected)
    }

    const run = (): unknown => {
      let result: unknown
      let syncErr: unknown = undefined
      try {
        result = fn.apply(this, args)
      } catch (e) {
        syncErr = e
        status = 'error'
        errorMsg = e instanceof Error ? e.message : String(e)
      }
      if (syncErr !== undefined) { finish(); throw syncErr }

      if (result !== null && result !== undefined && typeof (result as Promise<unknown>).then === 'function') {
        return (result as Promise<unknown>)
          .catch((e: unknown) => {
            status = 'error'
            errorMsg = e instanceof Error ? e.message : String(e)
            throw e
          })
          .finally(finish)
      }

      finish()
      return result
    }

    if (asyncLocalStorage) {
      return asyncLocalStorage.run(store, run)
    }
    return run()
  }

  return wrapped as T
}

export interface SpanContext {
  setOutput(data: unknown): void
  setTokens(inputTokens: number, outputTokens: number): void
}

export async function span<T>(
  name: string,
  fn: (ctx: SpanContext) => Promise<T> | T,
  options?: { kind?: string; model?: string; metadata?: Record<string, unknown> }
): Promise<T> {
  const spanId = randomId()
  const store = asyncLocalStorage?.getStore()
  const start = Date.now()
  let status = 'ok'
  let errorMsg: string | null = null
  let output: unknown
  let inputTokens: number | undefined
  let outputTokens: number | undefined

  const ctx: SpanContext = {
    setOutput(data) { output = truncate(data) },
    setTokens(i, o) { inputTokens = i; outputTokens = o },
  }

  try {
    const result = await fn(ctx)
    return result
  } catch (e) {
    status = 'error'
    errorMsg = e instanceof Error ? e.message : String(e)
    throw e
  } finally {
    const end = Date.now()
    queueSpan({
      span_id: spanId,
      trace_id: store?.traceId ?? null,
      name,
      kind: options?.kind ?? 'chain',
      model: options?.model,
      project: config.project,
      start_time: start / 1000,
      end_time: end / 1000,
      duration_ms: end - start,
      status,
      error_message: errorMsg,
      input_tokens: inputTokens,
      output_tokens: outputTokens,
      output,
      metadata: options?.metadata,
    })
  }
}

// ─── OpenAI patch ─────────────────────────────────────────────────────────────

function patchOpenAI(): void {
  try {
    const openaiModule = require('openai')
    const Completions = openaiModule?.OpenAI?.Chat?.Completions ?? openaiModule?.default?.Chat?.Completions
    if (!Completions) return
    const original = Completions.prototype.create
    if (!original || (original as { __vantra?: boolean }).__vantra) return

    const patched = async function (this: unknown, ...args: unknown[]) {
      let kwargs = args[0] as Record<string, unknown>
      const start = Date.now()
      const model = (kwargs?.model as string) ?? 'unknown'
      const messages = (kwargs?.messages as unknown[]) ?? []
      const captureIo = config.captureIo

      if (kwargs?.stream) {
        if (!kwargs.stream_options) {
          kwargs = { ...kwargs, stream_options: { include_usage: true } }
          args = [kwargs, ...args.slice(1)]
        }
        let rawStream: AsyncIterable<Record<string, unknown>> & object
        try {
          rawStream = await original.apply(this, args) as AsyncIterable<Record<string, unknown>> & object
        } catch (e) {
          queueSpan(_openaiErrorSpan(model, messages, captureIo, start, e instanceof Error ? e.message : String(e)))
          throw e
        }
        const gen = _trackOpenAIStream(rawStream, start, model, messages, captureIo)
        return _proxyStream(rawStream, gen)
      }

      // Non-streaming
      let status = 'ok'
      let errorMsg: string | null = null
      let response: Record<string, unknown> | null = null

      try {
        response = await original.apply(this, args) as Record<string, unknown>
        return response
      } catch (e) {
        status = 'error'
        errorMsg = e instanceof Error ? e.message : String(e)
        throw e
      } finally {
        const end = Date.now()
        let inputTokens = 0, outputTokens = 0, cost = 0
        const usage = response && (response.usage as Record<string, number>)
        if (usage) {
          inputTokens = usage.prompt_tokens ?? 0
          outputTokens = usage.completion_tokens ?? 0
          cost = calculateCost(model, inputTokens, outputTokens)
        }
        const choices = response && (response.choices as Array<{ message: { content?: string } }>)
        const outputContent = choices?.[0]?.message?.content
        queueSpan({
          span_id: randomId(),
          trace_id: asyncLocalStorage?.getStore()?.traceId ?? null,
          name: `openai.chat (${model})`,
          kind: 'llm',
          provider: 'openai',
          model,
          project: config.project,
          start_time: start / 1000,
          end_time: end / 1000,
          duration_ms: end - start,
          status,
          error_message: errorMsg,
          input_tokens: inputTokens,
          output_tokens: outputTokens,
          cost_usd: cost,
          input: captureIo ? truncate({ messages: messages.slice(-1) }) : undefined,
          output: captureIo && outputContent ? { content: outputContent.slice(0, 1000) } : undefined,
        })
      }
    };
    (patched as { __vantra?: boolean }).__vantra = true
    Completions.prototype.create = patched
  } catch { /* openai not installed */ }
}

function _openaiErrorSpan(
  model: string, messages: unknown[], captureIo: boolean,
  start: number, errorMsg: string
): SpanData {
  const end = Date.now()
  return {
    span_id: randomId(),
    trace_id: asyncLocalStorage?.getStore()?.traceId ?? null,
    name: `openai.chat (${model})`,
    kind: 'llm',
    provider: 'openai',
    model,
    project: config.project,
    start_time: start / 1000,
    end_time: end / 1000,
    duration_ms: end - start,
    status: 'error',
    error_message: errorMsg,
    input_tokens: 0,
    output_tokens: 0,
    cost_usd: 0,
    input: captureIo ? truncate({ messages: messages.slice(-1) }) : undefined,
    output: undefined,
  }
}

async function* _trackOpenAIStream(
  stream: AsyncIterable<Record<string, unknown>>,
  start: number,
  model: string,
  messages: unknown[],
  captureIo: boolean,
): AsyncGenerator<Record<string, unknown>> {
  const contentChunks: string[] = []
  let inputTokens = 0, outputTokens = 0
  let status = 'ok'
  let errorMsg: string | null = null

  try {
    for await (const chunk of stream) {
      const usage = chunk.usage as Record<string, number> | undefined
      if (usage) {
        inputTokens = usage.prompt_tokens ?? 0
        outputTokens = usage.completion_tokens ?? 0
      }
      const choices = chunk.choices as Array<{ delta?: { content?: string } }> | undefined
      const content = choices?.[0]?.delta?.content
      if (content) contentChunks.push(content)
      yield chunk
    }
  } catch (e) {
    status = 'error'
    errorMsg = e instanceof Error ? e.message : String(e)
    throw e
  } finally {
    const end = Date.now()
    const cost = calculateCost(model, inputTokens, outputTokens)
    const fullContent = contentChunks.join('')
    queueSpan({
      span_id: randomId(),
      trace_id: asyncLocalStorage?.getStore()?.traceId ?? null,
      name: `openai.chat (${model})`,
      kind: 'llm',
      provider: 'openai',
      model,
      project: config.project,
      start_time: start / 1000,
      end_time: end / 1000,
      duration_ms: end - start,
      status,
      error_message: errorMsg,
      input_tokens: inputTokens,
      output_tokens: outputTokens,
      cost_usd: cost,
      input: captureIo ? truncate({ messages: messages.slice(-1) }) : undefined,
      output: captureIo && fullContent ? { content: fullContent.slice(0, 1000) } : undefined,
    })
  }
}

// ─── Anthropic patch ──────────────────────────────────────────────────────────

function patchAnthropic(): void {
  try {
    let anthropicModule: unknown
    try { anthropicModule = require('anthropic') } catch { /* try alternate name */ }
    if (!anthropicModule) try { anthropicModule = require('@anthropic-ai/sdk') } catch { return }
    const mod = anthropicModule as Record<string, { Messages?: { prototype: Record<string, unknown> } }>
    const Messages = mod?.Anthropic?.Messages ?? mod?.default?.Messages
    if (!Messages) return
    const original = Messages.prototype.create as ((...a: unknown[]) => unknown) & { __vantra?: boolean }
    if (!original || original.__vantra) return

    const patched = async function (this: unknown, ...args: unknown[]) {
      const kwargs = args[0] as Record<string, unknown>
      const start = Date.now()
      const model = (kwargs?.model as string) ?? 'unknown'
      const messages = (kwargs?.messages as unknown[]) ?? []
      const captureIo = config.captureIo

      if (kwargs?.stream) {
        let rawStream: AsyncIterable<Record<string, unknown>> & object
        try {
          rawStream = await original.apply(this, args) as AsyncIterable<Record<string, unknown>> & object
        } catch (e) {
          queueSpan(_anthropicErrorSpan(model, messages, captureIo, start, e instanceof Error ? e.message : String(e)))
          throw e
        }
        const gen = _trackAnthropicStream(rawStream, start, model, messages, captureIo)
        return _proxyStream(rawStream, gen)
      }

      // Non-streaming
      let status = 'ok'
      let errorMsg: string | null = null
      let response: Record<string, unknown> | null = null

      try {
        response = await original.apply(this, args) as Record<string, unknown>
        return response
      } catch (e) {
        status = 'error'
        errorMsg = e instanceof Error ? e.message : String(e)
        throw e
      } finally {
        const end = Date.now()
        let inputTokens = 0, outputTokens = 0, cost = 0
        const usage = response && (response.usage as Record<string, number>)
        if (usage) {
          inputTokens = usage.input_tokens ?? 0
          outputTokens = usage.output_tokens ?? 0
          cost = calculateCost(model, inputTokens, outputTokens)
        }
        const content = response && (response.content as Array<{ text?: string }>)
        const outputText = content?.[0]?.text
        queueSpan({
          span_id: randomId(),
          trace_id: asyncLocalStorage?.getStore()?.traceId ?? null,
          name: `anthropic.messages (${model})`,
          kind: 'llm',
          provider: 'anthropic',
          model,
          project: config.project,
          start_time: start / 1000,
          end_time: end / 1000,
          duration_ms: end - start,
          status,
          error_message: errorMsg,
          input_tokens: inputTokens,
          output_tokens: outputTokens,
          cost_usd: cost,
          input: captureIo ? truncate({ messages: messages.slice(-1) }) : undefined,
          output: captureIo && outputText ? { text: outputText.slice(0, 1000) } : undefined,
        })
      }
    };
    (patched as { __vantra?: boolean }).__vantra = true
    Messages.prototype.create = patched as unknown as Record<string, unknown>
  } catch { /* anthropic not installed */ }
}

function _anthropicErrorSpan(
  model: string, messages: unknown[], captureIo: boolean,
  start: number, errorMsg: string
): SpanData {
  const end = Date.now()
  return {
    span_id: randomId(),
    trace_id: asyncLocalStorage?.getStore()?.traceId ?? null,
    name: `anthropic.messages (${model})`,
    kind: 'llm',
    provider: 'anthropic',
    model,
    project: config.project,
    start_time: start / 1000,
    end_time: end / 1000,
    duration_ms: end - start,
    status: 'error',
    error_message: errorMsg,
    input_tokens: 0,
    output_tokens: 0,
    cost_usd: 0,
    input: captureIo ? truncate({ messages: messages.slice(-1) }) : undefined,
    output: undefined,
  }
}

async function* _trackAnthropicStream(
  stream: AsyncIterable<Record<string, unknown>>,
  start: number,
  model: string,
  messages: unknown[],
  captureIo: boolean,
): AsyncGenerator<Record<string, unknown>> {
  const contentChunks: string[] = []
  let inputTokens = 0, outputTokens = 0
  let status = 'ok'
  let errorMsg: string | null = null

  try {
    for await (const event of stream) {
      const eventType = event.type as string | undefined

      if (eventType === 'message_start') {
        const msg = event.message as Record<string, unknown> | undefined
        const usage = msg?.usage as Record<string, number> | undefined
        if (usage) inputTokens = usage.input_tokens ?? 0
      } else if (eventType === 'content_block_delta') {
        const delta = event.delta as Record<string, unknown> | undefined
        const text = delta?.text
        if (typeof text === 'string' && text) contentChunks.push(text)
      } else if (eventType === 'message_delta') {
        const usage = event.usage as Record<string, number> | undefined
        if (usage) outputTokens = usage.output_tokens ?? 0
      }

      yield event
    }
  } catch (e) {
    status = 'error'
    errorMsg = e instanceof Error ? e.message : String(e)
    throw e
  } finally {
    const end = Date.now()
    const cost = calculateCost(model, inputTokens, outputTokens)
    const fullText = contentChunks.join('')
    queueSpan({
      span_id: randomId(),
      trace_id: asyncLocalStorage?.getStore()?.traceId ?? null,
      name: `anthropic.messages (${model})`,
      kind: 'llm',
      provider: 'anthropic',
      model,
      project: config.project,
      start_time: start / 1000,
      end_time: end / 1000,
      duration_ms: end - start,
      status,
      error_message: errorMsg,
      input_tokens: inputTokens,
      output_tokens: outputTokens,
      cost_usd: cost,
      input: captureIo ? truncate({ messages: messages.slice(-1) }) : undefined,
      output: captureIo && fullText ? { text: fullText.slice(0, 1000) } : undefined,
    })
  }
}

function randomId(): string {
  return Math.random().toString(36).slice(2) + Math.random().toString(36).slice(2)
}
