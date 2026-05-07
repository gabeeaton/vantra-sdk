"""
Vantra SDK integration tests — real API calls, no mocks.

Covers:
  - Anthropic non-streaming (real tokens, real cost)
  - Anthropic streaming (real events, token capture)
  - OpenAI non-streaming
  - OpenAI streaming (stream_options injection, usage in final chunk)
  - capture_io=False suppression
  - Error handling (bad model name)
  - trace() context propagation with streaming

Run:
  cd sdk/python
  pip3 install anthropic openai
  python3 tests/test_integration.py
"""

import os
import sys
import time
import traceback
from pathlib import Path

# Load .env.local from repo root (4 levels up: tests/python/sdk/vantra)
env_file = Path(__file__).parent.parent.parent.parent / '.env.local'
if env_file.exists():
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith('#') and '=' in line:
            k, v = line.split('=', 1)
            os.environ.setdefault(k.strip(), v.strip())

ANTHROPIC_KEY = os.environ.get('ANTHROPIC_API_KEY', '')
OPENAI_KEY = os.environ.get('OPENAI_API_KEY', '')
VANTRA_KEY = os.environ.get('VANTRA_API_KEY', 'test')

if not ANTHROPIC_KEY:
    print('ERROR: ANTHROPIC_API_KEY not set')
    sys.exit(1)

# ─── Vantra SDK setup ─────────────────────────────────────────────────────────

# Add sdk/python to sys.path so `import vantra` resolves correctly
sys.path.insert(0, str(Path(__file__).parent.parent))
import vantra

# Intercept at _post so we capture what the flush worker would send
# (_post is called from _flush which is in the same module, so this patch works)
_captured_batches: list[list[dict]] = []
_original_post = vantra._post

def _mock_post(path: str, data):
    if path == '/spans':
        _captured_batches.append(list(data))
    # Don't actually send anything

vantra._post = _mock_post


def _get_spans() -> list[dict]:
    """Drain the queue manually and return all captured spans."""
    # Force a flush so the queue is processed
    vantra._flush()
    return [span for batch in _captured_batches for span in batch]

def _reset():
    _captured_batches.clear()
    # Also drain anything still in the queue from previous tests
    with vantra._queue_lock:
        vantra._queue.clear()
    vantra._config['capture_io'] = True

vantra.init(api_key=VANTRA_KEY, project='integration-test')

# ─── Test helpers ─────────────────────────────────────────────────────────────

passed = 0
failed = 0

def check(label: str, condition: bool, detail: str = ''):
    global passed, failed
    if condition:
        print(f'  PASS  {label}')
        passed += 1
    else:
        print(f'  FAIL  {label}' + (f': {detail}' if detail else ''))
        failed += 1

def section(name: str):
    print(f'\n{name}')
    print('─' * 60)

# ─── Anthropic non-streaming ──────────────────────────────────────────────────

section('Anthropic — non-streaming')
_reset()

try:
    import anthropic as anthropic_sdk
    client = anthropic_sdk.Anthropic(api_key=ANTHROPIC_KEY)

    response = client.messages.create(
        model='claude-haiku-4-5',
        max_tokens=32,
        messages=[{'role': 'user', 'content': 'Reply with exactly the word: pong'}],
    )

    spans = _get_spans()

    check('response has content', bool(response.content))
    check('response text non-empty', bool(response.content[0].text.strip()))
    check('span was queued', len(spans) == 1, str(len(spans)))

    if spans:
        span = spans[0]
        check('span kind is llm', span['kind'] == 'llm')
        check('span provider is anthropic', span['provider'] == 'anthropic')
        check('span model correct', 'claude-haiku' in span['model'])
        check('span status ok', span['status'] == 'ok')
        check('input_tokens > 0', span['input_tokens'] > 0, str(span['input_tokens']))
        check('output_tokens > 0', span['output_tokens'] > 0, str(span['output_tokens']))
        check('cost_usd > 0', span['cost_usd'] > 0, str(span['cost_usd']))
        check('duration_ms reasonable', 100 < span['duration_ms'] < 30000, str(span['duration_ms']))
        check('input captured', span['input'] is not None)
        check('output captured', span['output'] is not None)
        check('output has text key', 'text' in (span['output'] or {}))

except Exception as e:
    print(f'  ERROR  {e}')
    traceback.print_exc()
    failed += 1

# ─── Anthropic streaming ──────────────────────────────────────────────────────

section('Anthropic — streaming')
_reset()

try:
    chunks_received = []
    text_received = []

    stream = client.messages.create(
        model='claude-haiku-4-5',
        max_tokens=32,
        stream=True,
        messages=[{'role': 'user', 'content': 'Reply with exactly the word: pong'}],
    )

    for event in stream:
        chunks_received.append(event)
        if hasattr(event, 'type') and event.type == 'content_block_delta':
            delta = getattr(event, 'delta', None)
            if delta and hasattr(delta, 'text'):
                text_received.append(delta.text)

    spans = _get_spans()

    check('received multiple events', len(chunks_received) > 3, str(len(chunks_received)))
    check('received text content', len(text_received) > 0)
    check('assembled text non-empty', bool(''.join(text_received).strip()))
    check('span was queued after stream', len(spans) == 1, str(len(spans)))

    if spans:
        span = spans[0]
        check('stream span kind is llm', span['kind'] == 'llm')
        check('stream span provider is anthropic', span['provider'] == 'anthropic')
        check('stream span status ok', span['status'] == 'ok')
        check('stream input_tokens > 0', span['input_tokens'] > 0, str(span['input_tokens']))
        check('stream output_tokens > 0', span['output_tokens'] > 0, str(span['output_tokens']))
        check('stream cost_usd > 0', span['cost_usd'] > 0, str(span['cost_usd']))
        check('stream output text assembled', span['output'] is not None and bool(span['output'].get('text')))

except Exception as e:
    print(f'  ERROR  {e}')
    traceback.print_exc()
    failed += 1

# ─── Anthropic streaming — error handling ────────────────────────────────────

section('Anthropic — streaming error handling')
_reset()

try:
    try:
        stream = client.messages.create(
            model='not-a-real-model-xyz',
            max_tokens=16,
            stream=True,
            messages=[{'role': 'user', 'content': 'hi'}],
        )
        for _ in stream:
            pass
    except Exception:
        pass

    spans = _get_spans()
    check('error span queued', len(spans) == 1, str(len(spans)))
    if spans:
        span = spans[0]
        check('error span status is error', span['status'] == 'error')
        check('error_message set', bool(span.get('error_message')))

except Exception as e:
    print(f'  ERROR  {e}')
    traceback.print_exc()
    failed += 1

# ─── Anthropic — capture_io=False ────────────────────────────────────────────

section('Anthropic — capture_io=False')
_reset()
vantra._config['capture_io'] = False

try:
    client.messages.create(
        model='claude-haiku-4-5',
        max_tokens=16,
        messages=[{'role': 'user', 'content': 'hi'}],
    )
    spans = _get_spans()
    check('span queued', len(spans) == 1, str(len(spans)))
    if spans:
        span = spans[0]
        check('input is None', span['input'] is None)
        check('output is None', span['output'] is None)
        check('tokens still captured', span['input_tokens'] > 0)

except Exception as e:
    print(f'  ERROR  {e}')
    traceback.print_exc()
    failed += 1

vantra._config['capture_io'] = True

# ─── Anthropic — capture_io=False streaming ───────────────────────────────────

section('Anthropic — capture_io=False streaming')
_reset()
vantra._config['capture_io'] = False

try:
    stream = client.messages.create(
        model='claude-haiku-4-5',
        max_tokens=16,
        stream=True,
        messages=[{'role': 'user', 'content': 'hi'}],
    )
    for _ in stream:
        pass
    spans = _get_spans()
    check('span queued', len(spans) == 1, str(len(spans)))
    if spans:
        span = spans[0]
        check('input is None', span['input'] is None)
        check('output is None', span['output'] is None)
        check('tokens still captured', span['input_tokens'] > 0)

except Exception as e:
    print(f'  ERROR  {e}')
    traceback.print_exc()
    failed += 1

vantra._config['capture_io'] = True

# ─── Anthropic — trace() + streaming ─────────────────────────────────────────

section('Anthropic — trace() + streaming')
_reset()

try:
    @vantra.trace
    def run_agent():
        stream = client.messages.create(
            model='claude-haiku-4-5',
            max_tokens=16,
            stream=True,
            messages=[{'role': 'user', 'content': 'say hi'}],
        )
        for _ in stream:
            pass

    run_agent()
    time.sleep(0.1)  # let trace finalize
    spans = _get_spans()

    check('span queued inside trace', len(spans) >= 1, str(len(spans)))
    if spans:
        span = spans[0]
        check('span has trace_id', bool(span.get('trace_id')))
        check('span status ok', span['status'] == 'ok')

except Exception as e:
    print(f'  ERROR  {e}')
    traceback.print_exc()
    failed += 1

# ─── Anthropic — two sequential streams, separate spans ───────────────────────

section('Anthropic — sequential streams get separate spans')
_reset()

try:
    for i in range(2):
        stream = client.messages.create(
            model='claude-haiku-4-5',
            max_tokens=16,
            stream=True,
            messages=[{'role': 'user', 'content': f'say {i}'}],
        )
        for _ in stream:
            pass

    spans = _get_spans()
    check('two spans queued', len(spans) == 2, str(len(spans)))
    if len(spans) == 2:
        check('span_ids are unique', spans[0]['span_id'] != spans[1]['span_id'])
        check('both spans ok', spans[0]['status'] == 'ok' and spans[1]['status'] == 'ok')

except Exception as e:
    print(f'  ERROR  {e}')
    traceback.print_exc()
    failed += 1

# ─── OpenAI (if key available) ───────────────────────────────────────────────

if OPENAI_KEY:
    section('OpenAI — non-streaming')
    _reset()

    try:
        import openai as openai_sdk
        oai = openai_sdk.OpenAI(api_key=OPENAI_KEY)

        response = oai.chat.completions.create(
            model='gpt-4o-mini',
            max_tokens=16,
            messages=[{'role': 'user', 'content': 'Reply with exactly the word: pong'}],
        )

        spans = _get_spans()
        check('response has choices', bool(response.choices))
        check('span queued', len(spans) == 1, str(len(spans)))
        if spans:
            span = spans[0]
            check('provider is openai', span['provider'] == 'openai')
            check('input_tokens > 0', span['input_tokens'] > 0)
            check('output_tokens > 0', span['output_tokens'] > 0)
            check('cost_usd > 0', span['cost_usd'] > 0)

    except Exception as e:
        print(f'  ERROR  {e}')
        traceback.print_exc()
        failed += 1

    section('OpenAI — streaming')
    _reset()

    try:
        chunks = []
        stream = oai.chat.completions.create(
            model='gpt-4o-mini',
            max_tokens=16,
            stream=True,
            messages=[{'role': 'user', 'content': 'say hi'}],
        )
        for chunk in stream:
            chunks.append(chunk)

        spans = _get_spans()
        check('received chunks', len(chunks) > 1, str(len(chunks)))
        check('span queued after stream', len(spans) == 1, str(len(spans)))
        if spans:
            span = spans[0]
            check('provider is openai', span['provider'] == 'openai')
            check('stream input_tokens > 0', span['input_tokens'] > 0, str(span['input_tokens']))
            check('stream output_tokens > 0', span['output_tokens'] > 0, str(span['output_tokens']))

    except Exception as e:
        print(f'  ERROR  {e}')
        traceback.print_exc()
        failed += 1
else:
    print('\nSkipping OpenAI tests (no OPENAI_API_KEY)')

# ─── Summary ──────────────────────────────────────────────────────────────────

total = passed + failed
print(f'\n{"═" * 60}')
print(f'Results: {passed}/{total} passed', '✓' if failed == 0 else f'— {failed} FAILED')
print('═' * 60)
sys.exit(0 if failed == 0 else 1)
