"use strict";
var _a;
Object.defineProperty(exports, "__esModule", { value: true });
exports.init = init;
exports.trace = trace;
exports.span = span;
const costs_1 = require("./costs");
const VANTRA_ENDPOINT = (_a = process.env.VANTRA_ENDPOINT) !== null && _a !== void 0 ? _a : 'https://vantra.dev/api/v1/ingest';
const config = {
    apiKey: null,
    project: null,
    enabled: true,
    captureIo: true,
};
let asyncLocalStorage = null;
try {
    const { AsyncLocalStorage } = require('async_hooks');
    asyncLocalStorage = new AsyncLocalStorage();
}
catch (_b) {
    // not available
}
const queue = [];
let flushTimer = null;
function post(path, data) {
    if (!config.apiKey)
        return;
    const url = `${VANTRA_ENDPOINT}${path}`;
    const body = JSON.stringify(data);
    const headers = {
        'Authorization': `Bearer ${config.apiKey}`,
        'Content-Type': 'application/json',
    };
    if (typeof fetch !== 'undefined') {
        fetch(url, { method: 'POST', headers, body }).catch(() => { });
    }
    else {
        try {
            const https = require('https');
            const http = require('http');
            const u = new URL(url);
            const mod = u.protocol === 'https:' ? https : http;
            const req = mod.request({ hostname: u.hostname, path: u.pathname, method: 'POST', headers: { ...headers, 'Content-Length': Buffer.byteLength(body) } }, () => { });
            req.on('error', () => { });
            req.write(body);
            req.end();
        }
        catch ( /* silent */_a) { /* silent */ }
    }
}
function flush() {
    if (!queue.length)
        return;
    const items = queue.splice(0);
    post('/spans', items);
}
function queueSpan(span) {
    const store = asyncLocalStorage === null || asyncLocalStorage === void 0 ? void 0 : asyncLocalStorage.getStore();
    if (store) {
        store.spans.push(span);
    }
    else {
        queue.push(span);
    }
}
function truncate(data, maxChars = 2000) {
    try {
        const s = JSON.stringify(data);
        if (s.length <= maxChars)
            return data;
        return { _truncated: true, preview: s.slice(0, maxChars) };
    }
    catch (_a) {
        return String(data).slice(0, maxChars);
    }
}
// Proxy the original stream so all SDK methods (.controller, .toReadableStream(), etc.)
// remain accessible while iteration flows through our tracking generator.
function _proxyStream(original, gen) {
    return new Proxy(original, {
        get(target, prop) {
            if (prop === Symbol.asyncIterator) {
                return () => gen[Symbol.asyncIterator]();
            }
            const val = target[prop];
            return typeof val === 'function' ? val.bind(target) : val;
        },
    });
}
function init(options) {
    var _a, _b;
    config.apiKey = options.apiKey;
    config.project = options.project;
    config.enabled = (_a = options.enabled) !== null && _a !== void 0 ? _a : true;
    config.captureIo = (_b = options.captureIo) !== null && _b !== void 0 ? _b : true;
    if (!flushTimer) {
        flushTimer = setInterval(flush, 500);
        if (flushTimer.unref)
            flushTimer.unref();
    }
    if (options.patchOpenAI !== false)
        patchOpenAI();
    if (options.patchAnthropic !== false)
        patchAnthropic();
}
function trace(fn, options) {
    var _a, _b;
    const traceName = (_b = (_a = options === null || options === void 0 ? void 0 : options.name) !== null && _a !== void 0 ? _a : fn.name) !== null && _b !== void 0 ? _b : 'trace';
    const promptVersion = (options === null || options === void 0 ? void 0 : options.promptVersion) ? String(options.promptVersion).slice(0, 100) : undefined;
    const wrapped = function (...args) {
        if (!config.enabled)
            return fn.apply(this, args);
        const traceId = randomId();
        const start = Date.now();
        let status = 'ok';
        let errorMsg = null;
        const store = { traceId, spans: [] };
        const finish = () => {
            const end = Date.now();
            const collected = store.spans;
            const totalTokens = collected.reduce((s, sp) => { var _a, _b; return s + ((_a = sp.input_tokens) !== null && _a !== void 0 ? _a : 0) + ((_b = sp.output_tokens) !== null && _b !== void 0 ? _b : 0); }, 0);
            const totalCost = collected.reduce((s, sp) => { var _a; return s + ((_a = sp.cost_usd) !== null && _a !== void 0 ? _a : 0); }, 0);
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
            });
            if (collected.length)
                post('/spans', collected);
        };
        const run = () => {
            let result;
            let syncErr = undefined;
            try {
                result = fn.apply(this, args);
            }
            catch (e) {
                syncErr = e;
                status = 'error';
                errorMsg = e instanceof Error ? e.message : String(e);
            }
            if (syncErr !== undefined) {
                finish();
                throw syncErr;
            }
            if (result !== null && result !== undefined && typeof result.then === 'function') {
                return result
                    .catch((e) => {
                    status = 'error';
                    errorMsg = e instanceof Error ? e.message : String(e);
                    throw e;
                })
                    .finally(finish);
            }
            finish();
            return result;
        };
        if (asyncLocalStorage) {
            return asyncLocalStorage.run(store, run);
        }
        return run();
    };
    return wrapped;
}
async function span(name, fn, options) {
    var _a, _b;
    const spanId = randomId();
    const store = asyncLocalStorage === null || asyncLocalStorage === void 0 ? void 0 : asyncLocalStorage.getStore();
    const start = Date.now();
    let status = 'ok';
    let errorMsg = null;
    let output;
    let inputTokens;
    let outputTokens;
    const ctx = {
        setOutput(data) { output = truncate(data); },
        setTokens(i, o) { inputTokens = i; outputTokens = o; },
    };
    try {
        const result = await fn(ctx);
        return result;
    }
    catch (e) {
        status = 'error';
        errorMsg = e instanceof Error ? e.message : String(e);
        throw e;
    }
    finally {
        const end = Date.now();
        queueSpan({
            span_id: spanId,
            trace_id: (_a = store === null || store === void 0 ? void 0 : store.traceId) !== null && _a !== void 0 ? _a : null,
            name,
            kind: (_b = options === null || options === void 0 ? void 0 : options.kind) !== null && _b !== void 0 ? _b : 'chain',
            model: options === null || options === void 0 ? void 0 : options.model,
            project: config.project,
            start_time: start / 1000,
            end_time: end / 1000,
            duration_ms: end - start,
            status,
            error_message: errorMsg,
            input_tokens: inputTokens,
            output_tokens: outputTokens,
            output,
            metadata: options === null || options === void 0 ? void 0 : options.metadata,
        });
    }
}
// ─── OpenAI patch ─────────────────────────────────────────────────────────────
function patchOpenAI() {
    var _a, _b, _c, _d, _e;
    try {
        const openaiModule = require('openai');
        const Completions = (_c = (_b = (_a = openaiModule === null || openaiModule === void 0 ? void 0 : openaiModule.OpenAI) === null || _a === void 0 ? void 0 : _a.Chat) === null || _b === void 0 ? void 0 : _b.Completions) !== null && _c !== void 0 ? _c : (_e = (_d = openaiModule === null || openaiModule === void 0 ? void 0 : openaiModule.default) === null || _d === void 0 ? void 0 : _d.Chat) === null || _e === void 0 ? void 0 : _e.Completions;
        if (!Completions)
            return;
        const original = Completions.prototype.create;
        if (!original || original.__vantra)
            return;
        const patched = async function (...args) {
            var _a, _b, _c, _d, _e, _f, _g, _h;
            let kwargs = args[0];
            const start = Date.now();
            const model = (_a = kwargs === null || kwargs === void 0 ? void 0 : kwargs.model) !== null && _a !== void 0 ? _a : 'unknown';
            const messages = (_b = kwargs === null || kwargs === void 0 ? void 0 : kwargs.messages) !== null && _b !== void 0 ? _b : [];
            const captureIo = config.captureIo;
            if (kwargs === null || kwargs === void 0 ? void 0 : kwargs.stream) {
                if (!kwargs.stream_options) {
                    kwargs = { ...kwargs, stream_options: { include_usage: true } };
                    args = [kwargs, ...args.slice(1)];
                }
                let rawStream;
                try {
                    rawStream = await original.apply(this, args);
                }
                catch (e) {
                    queueSpan(_openaiErrorSpan(model, messages, captureIo, start, e instanceof Error ? e.message : String(e)));
                    throw e;
                }
                const gen = _trackOpenAIStream(rawStream, start, model, messages, captureIo);
                return _proxyStream(rawStream, gen);
            }
            // Non-streaming
            let status = 'ok';
            let errorMsg = null;
            let response = null;
            try {
                response = await original.apply(this, args);
                return response;
            }
            catch (e) {
                status = 'error';
                errorMsg = e instanceof Error ? e.message : String(e);
                throw e;
            }
            finally {
                const end = Date.now();
                let inputTokens = 0, outputTokens = 0, cost = 0;
                const usage = response && response.usage;
                if (usage) {
                    inputTokens = (_c = usage.prompt_tokens) !== null && _c !== void 0 ? _c : 0;
                    outputTokens = (_d = usage.completion_tokens) !== null && _d !== void 0 ? _d : 0;
                    cost = (0, costs_1.calculateCost)(model, inputTokens, outputTokens);
                }
                const choices = response && response.choices;
                const outputContent = (_f = (_e = choices === null || choices === void 0 ? void 0 : choices[0]) === null || _e === void 0 ? void 0 : _e.message) === null || _f === void 0 ? void 0 : _f.content;
                queueSpan({
                    span_id: randomId(),
                    trace_id: (_h = (_g = asyncLocalStorage === null || asyncLocalStorage === void 0 ? void 0 : asyncLocalStorage.getStore()) === null || _g === void 0 ? void 0 : _g.traceId) !== null && _h !== void 0 ? _h : null,
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
                });
            }
        };
        patched.__vantra = true;
        Completions.prototype.create = patched;
    }
    catch ( /* openai not installed */_f) { /* openai not installed */ }
}
function _openaiErrorSpan(model, messages, captureIo, start, errorMsg) {
    var _a, _b;
    const end = Date.now();
    return {
        span_id: randomId(),
        trace_id: (_b = (_a = asyncLocalStorage === null || asyncLocalStorage === void 0 ? void 0 : asyncLocalStorage.getStore()) === null || _a === void 0 ? void 0 : _a.traceId) !== null && _b !== void 0 ? _b : null,
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
    };
}
async function* _trackOpenAIStream(stream, start, model, messages, captureIo) {
    var _a, _b, _c, _d, _e, _f;
    const contentChunks = [];
    let inputTokens = 0, outputTokens = 0;
    let status = 'ok';
    let errorMsg = null;
    try {
        for await (const chunk of stream) {
            const usage = chunk.usage;
            if (usage) {
                inputTokens = (_a = usage.prompt_tokens) !== null && _a !== void 0 ? _a : 0;
                outputTokens = (_b = usage.completion_tokens) !== null && _b !== void 0 ? _b : 0;
            }
            const choices = chunk.choices;
            const content = (_d = (_c = choices === null || choices === void 0 ? void 0 : choices[0]) === null || _c === void 0 ? void 0 : _c.delta) === null || _d === void 0 ? void 0 : _d.content;
            if (content)
                contentChunks.push(content);
            yield chunk;
        }
    }
    catch (e) {
        status = 'error';
        errorMsg = e instanceof Error ? e.message : String(e);
        throw e;
    }
    finally {
        const end = Date.now();
        const cost = (0, costs_1.calculateCost)(model, inputTokens, outputTokens);
        const fullContent = contentChunks.join('');
        queueSpan({
            span_id: randomId(),
            trace_id: (_f = (_e = asyncLocalStorage === null || asyncLocalStorage === void 0 ? void 0 : asyncLocalStorage.getStore()) === null || _e === void 0 ? void 0 : _e.traceId) !== null && _f !== void 0 ? _f : null,
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
        });
    }
}
// ─── Anthropic patch ──────────────────────────────────────────────────────────
function patchAnthropic() {
    var _a, _b, _c;
    try {
        let anthropicModule;
        try {
            anthropicModule = require('anthropic');
        }
        catch ( /* try alternate name */_d) { /* try alternate name */ }
        if (!anthropicModule)
            try {
                anthropicModule = require('@anthropic-ai/sdk');
            }
            catch (_e) {
                return;
            }
        const mod = anthropicModule;
        const Messages = (_b = (_a = mod === null || mod === void 0 ? void 0 : mod.Anthropic) === null || _a === void 0 ? void 0 : _a.Messages) !== null && _b !== void 0 ? _b : (_c = mod === null || mod === void 0 ? void 0 : mod.default) === null || _c === void 0 ? void 0 : _c.Messages;
        if (!Messages)
            return;
        const original = Messages.prototype.create;
        if (!original || original.__vantra)
            return;
        const patched = async function (...args) {
            var _a, _b, _c, _d, _e, _f, _g;
            const kwargs = args[0];
            const start = Date.now();
            const model = (_a = kwargs === null || kwargs === void 0 ? void 0 : kwargs.model) !== null && _a !== void 0 ? _a : 'unknown';
            const messages = (_b = kwargs === null || kwargs === void 0 ? void 0 : kwargs.messages) !== null && _b !== void 0 ? _b : [];
            const captureIo = config.captureIo;
            if (kwargs === null || kwargs === void 0 ? void 0 : kwargs.stream) {
                let rawStream;
                try {
                    rawStream = await original.apply(this, args);
                }
                catch (e) {
                    queueSpan(_anthropicErrorSpan(model, messages, captureIo, start, e instanceof Error ? e.message : String(e)));
                    throw e;
                }
                const gen = _trackAnthropicStream(rawStream, start, model, messages, captureIo);
                return _proxyStream(rawStream, gen);
            }
            // Non-streaming
            let status = 'ok';
            let errorMsg = null;
            let response = null;
            try {
                response = await original.apply(this, args);
                return response;
            }
            catch (e) {
                status = 'error';
                errorMsg = e instanceof Error ? e.message : String(e);
                throw e;
            }
            finally {
                const end = Date.now();
                let inputTokens = 0, outputTokens = 0, cost = 0;
                const usage = response && response.usage;
                if (usage) {
                    inputTokens = (_c = usage.input_tokens) !== null && _c !== void 0 ? _c : 0;
                    outputTokens = (_d = usage.output_tokens) !== null && _d !== void 0 ? _d : 0;
                    cost = (0, costs_1.calculateCost)(model, inputTokens, outputTokens);
                }
                const content = response && response.content;
                const outputText = (_e = content === null || content === void 0 ? void 0 : content[0]) === null || _e === void 0 ? void 0 : _e.text;
                queueSpan({
                    span_id: randomId(),
                    trace_id: (_g = (_f = asyncLocalStorage === null || asyncLocalStorage === void 0 ? void 0 : asyncLocalStorage.getStore()) === null || _f === void 0 ? void 0 : _f.traceId) !== null && _g !== void 0 ? _g : null,
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
                });
            }
        };
        patched.__vantra = true;
        Messages.prototype.create = patched;
    }
    catch ( /* anthropic not installed */_f) { /* anthropic not installed */ }
}
function _anthropicErrorSpan(model, messages, captureIo, start, errorMsg) {
    var _a, _b;
    const end = Date.now();
    return {
        span_id: randomId(),
        trace_id: (_b = (_a = asyncLocalStorage === null || asyncLocalStorage === void 0 ? void 0 : asyncLocalStorage.getStore()) === null || _a === void 0 ? void 0 : _a.traceId) !== null && _b !== void 0 ? _b : null,
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
    };
}
async function* _trackAnthropicStream(stream, start, model, messages, captureIo) {
    var _a, _b, _c, _d;
    const contentChunks = [];
    let inputTokens = 0, outputTokens = 0;
    let status = 'ok';
    let errorMsg = null;
    try {
        for await (const event of stream) {
            const eventType = event.type;
            if (eventType === 'message_start') {
                const msg = event.message;
                const usage = msg === null || msg === void 0 ? void 0 : msg.usage;
                if (usage)
                    inputTokens = (_a = usage.input_tokens) !== null && _a !== void 0 ? _a : 0;
            }
            else if (eventType === 'content_block_delta') {
                const delta = event.delta;
                const text = delta === null || delta === void 0 ? void 0 : delta.text;
                if (typeof text === 'string' && text)
                    contentChunks.push(text);
            }
            else if (eventType === 'message_delta') {
                const usage = event.usage;
                if (usage)
                    outputTokens = (_b = usage.output_tokens) !== null && _b !== void 0 ? _b : 0;
            }
            yield event;
        }
    }
    catch (e) {
        status = 'error';
        errorMsg = e instanceof Error ? e.message : String(e);
        throw e;
    }
    finally {
        const end = Date.now();
        const cost = (0, costs_1.calculateCost)(model, inputTokens, outputTokens);
        const fullText = contentChunks.join('');
        queueSpan({
            span_id: randomId(),
            trace_id: (_d = (_c = asyncLocalStorage === null || asyncLocalStorage === void 0 ? void 0 : asyncLocalStorage.getStore()) === null || _c === void 0 ? void 0 : _c.traceId) !== null && _d !== void 0 ? _d : null,
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
        });
    }
}
function randomId() {
    return Math.random().toString(36).slice(2) + Math.random().toString(36).slice(2);
}
