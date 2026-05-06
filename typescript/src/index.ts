import { calculateCost } from './costs'

const VANTRA_ENDPOINT = process.env.VANTRA_ENDPOINT ?? 'https://vantra.dev/api/v1/ingest'

const config = {
  apiKey: null as string | null,
  project: null as string | null,
  enabled: true,
}

// AsyncLocalStorage for trace context (Node.js 12.17+)
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

export interface InitOptions {
  apiKey: string
  project: string
  enabled?: boolean
  patchOpenAI?: boolean
  patchAnthropic?: boolean
}

export function init(options: InitOptions): void {
  config.apiKey = options.apiKey
  config.project = options.project
  config.enabled = options.enabled ?? true

  if (!flushTimer) {
    flushTimer = setInterval(flush, 500)
    if (flushTimer.unref) flushTimer.unref()
  }

  if (options.patchOpenAI !== false) patchOpenAI()
  if (options.patchAnthropic !== false) patchAnthropic()
}

export function trace<T extends (...args: unknown[]) => unknown>(
  fn: T,
  options?: { name?: string }
): T {
  const traceName = options?.name ?? fn.name ?? 'trace'

  const wrapped = function (this: unknown, ...args: unknown[]) {
    if (!config.enabled) return fn.apply(this, args)

    const traceId = randomId()
    const start = Date.now()
    let status = 'ok'
    let errorMsg: string | null = null

    const store = { traceId, spans: [] as SpanData[] }

    const run = () => {
      let result: unknown
      try {
        result = fn.apply(this, args)
      } catch (e) {
        status = 'error'
        errorMsg = e instanceof Error ? e.message : String(e)
        throw e
      } finally {
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
        })
        if (collected.length) post('/spans', collected)
      }
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

function patchOpenAI(): void {
  try {
    const openaiModule = require('openai')
    const Completions = openaiModule?.OpenAI?.Chat?.Completions ?? openaiModule?.default?.Chat?.Completions
    if (!Completions) return
    const original = Completions.prototype.create
    if (!original || (original as { __vantra?: boolean }).__vantra) return

    const patched = async function (this: unknown, ...args: unknown[]) {
      const kwargs = args[0] as Record<string, unknown>
      const start = Date.now()
      let status = 'ok'
      let errorMsg: string | null = null
      let response: Record<string, unknown> | null = null

      try {
        response = await original.apply(this, args)
        return response
      } catch (e) {
        status = 'error'
        errorMsg = e instanceof Error ? e.message : String(e)
        throw e
      } finally {
        const end = Date.now()
        const model = (kwargs?.model as string) ?? 'unknown'
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
          input: truncate({ messages: (kwargs?.messages as unknown[])?.slice(-1) }),
          output: outputContent ? { content: outputContent.slice(0, 1000) } : undefined,
        })
      }
    };
    (patched as { __vantra?: boolean }).__vantra = true
    Completions.prototype.create = patched
  } catch { /* openai not installed */ }
}

function patchAnthropic(): void {
  try {
    const anthropicModule = require('anthropic')
    const Messages = anthropicModule?.Anthropic?.Messages ?? anthropicModule?.default?.Messages
    if (!Messages) return
    const original = Messages.prototype.create
    if (!original || (original as { __vantra?: boolean }).__vantra) return

    const patched = async function (this: unknown, ...args: unknown[]) {
      const kwargs = args[0] as Record<string, unknown>
      const start = Date.now()
      let status = 'ok'
      let errorMsg: string | null = null
      let response: Record<string, unknown> | null = null

      try {
        response = await original.apply(this, args)
        return response
      } catch (e) {
        status = 'error'
        errorMsg = e instanceof Error ? e.message : String(e)
        throw e
      } finally {
        const end = Date.now()
        const model = (kwargs?.model as string) ?? 'unknown'
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
          input: truncate({ messages: (kwargs?.messages as unknown[])?.slice(-1) }),
          output: outputText ? { text: outputText.slice(0, 1000) } : undefined,
        })
      }
    };
    (patched as { __vantra?: boolean }).__vantra = true
    Messages.prototype.create = patched
  } catch { /* anthropic not installed */ }
}

function randomId(): string {
  return Math.random().toString(36).slice(2) + Math.random().toString(36).slice(2)
}
