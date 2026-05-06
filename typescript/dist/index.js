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
};
// AsyncLocalStorage for trace context (Node.js 12.17+)
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
function init(options) {
    var _a;
    config.apiKey = options.apiKey;
    config.project = options.project;
    config.enabled = (_a = options.enabled) !== null && _a !== void 0 ? _a : true;
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
            // Async function — wait for the promise before posting
            if (result !== null && result !== undefined && typeof result.then === 'function') {
                return result
                    .catch((e) => {
                    status = 'error';
                    errorMsg = e instanceof Error ? e.message : String(e);
                    throw e;
                })
                    .finally(finish);
            }
            // Synchronous function
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
            const kwargs = args[0];
            const start = Date.now();
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
                const model = (_a = kwargs === null || kwargs === void 0 ? void 0 : kwargs.model) !== null && _a !== void 0 ? _a : 'unknown';
                let inputTokens = 0, outputTokens = 0, cost = 0;
                const usage = response && response.usage;
                if (usage) {
                    inputTokens = (_b = usage.prompt_tokens) !== null && _b !== void 0 ? _b : 0;
                    outputTokens = (_c = usage.completion_tokens) !== null && _c !== void 0 ? _c : 0;
                    cost = (0, costs_1.calculateCost)(model, inputTokens, outputTokens);
                }
                const choices = response && response.choices;
                const outputContent = (_e = (_d = choices === null || choices === void 0 ? void 0 : choices[0]) === null || _d === void 0 ? void 0 : _d.message) === null || _e === void 0 ? void 0 : _e.content;
                queueSpan({
                    span_id: randomId(),
                    trace_id: (_g = (_f = asyncLocalStorage === null || asyncLocalStorage === void 0 ? void 0 : asyncLocalStorage.getStore()) === null || _f === void 0 ? void 0 : _f.traceId) !== null && _g !== void 0 ? _g : null,
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
                    input: truncate({ messages: (_h = kwargs === null || kwargs === void 0 ? void 0 : kwargs.messages) === null || _h === void 0 ? void 0 : _h.slice(-1) }),
                    output: outputContent ? { content: outputContent.slice(0, 1000) } : undefined,
                });
            }
        };
        patched.__vantra = true;
        Completions.prototype.create = patched;
    }
    catch ( /* openai not installed */_f) { /* openai not installed */ }
}
function patchAnthropic() {
    var _a, _b, _c;
    try {
        const anthropicModule = require('anthropic');
        const Messages = (_b = (_a = anthropicModule === null || anthropicModule === void 0 ? void 0 : anthropicModule.Anthropic) === null || _a === void 0 ? void 0 : _a.Messages) !== null && _b !== void 0 ? _b : (_c = anthropicModule === null || anthropicModule === void 0 ? void 0 : anthropicModule.default) === null || _c === void 0 ? void 0 : _c.Messages;
        if (!Messages)
            return;
        const original = Messages.prototype.create;
        if (!original || original.__vantra)
            return;
        const patched = async function (...args) {
            var _a, _b, _c, _d, _e, _f, _g;
            const kwargs = args[0];
            const start = Date.now();
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
                const model = (_a = kwargs === null || kwargs === void 0 ? void 0 : kwargs.model) !== null && _a !== void 0 ? _a : 'unknown';
                let inputTokens = 0, outputTokens = 0, cost = 0;
                const usage = response && response.usage;
                if (usage) {
                    inputTokens = (_b = usage.input_tokens) !== null && _b !== void 0 ? _b : 0;
                    outputTokens = (_c = usage.output_tokens) !== null && _c !== void 0 ? _c : 0;
                    cost = (0, costs_1.calculateCost)(model, inputTokens, outputTokens);
                }
                const content = response && response.content;
                const outputText = (_d = content === null || content === void 0 ? void 0 : content[0]) === null || _d === void 0 ? void 0 : _d.text;
                queueSpan({
                    span_id: randomId(),
                    trace_id: (_f = (_e = asyncLocalStorage === null || asyncLocalStorage === void 0 ? void 0 : asyncLocalStorage.getStore()) === null || _e === void 0 ? void 0 : _e.traceId) !== null && _f !== void 0 ? _f : null,
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
                    input: truncate({ messages: (_g = kwargs === null || kwargs === void 0 ? void 0 : kwargs.messages) === null || _g === void 0 ? void 0 : _g.slice(-1) }),
                    output: outputText ? { text: outputText.slice(0, 1000) } : undefined,
                });
            }
        };
        patched.__vantra = true;
        Messages.prototype.create = patched;
    }
    catch ( /* anthropic not installed */_d) { /* anthropic not installed */ }
}
function randomId() {
    return Math.random().toString(36).slice(2) + Math.random().toString(36).slice(2);
}
