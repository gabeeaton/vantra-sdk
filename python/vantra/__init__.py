import os
import time
import uuid
import asyncio
import threading
import functools
from contextlib import contextmanager, asynccontextmanager
from contextvars import ContextVar
from typing import Optional, Any
from collections import deque

_current_trace_id: ContextVar[Optional[str]] = ContextVar('current_trace_id', default=None)
_pending_spans: ContextVar[Optional[list]] = ContextVar('pending_spans', default=None)

try:
    import httpx
    _HTTP_CLIENT = httpx.Client(timeout=5.0)
except ImportError:
    _HTTP_CLIENT = None

VANTRA_ENDPOINT = os.environ.get("VANTRA_ENDPOINT", "https://vantra.dev/api/v1/ingest")

_config = {
    "api_key": None,
    "project": None,
    "enabled": True,
    "capture_io": True,
}

_queue: deque = deque()
_queue_lock = threading.Lock()
_flush_interval = 0.5

def _flush_worker():
    while True:
        time.sleep(_flush_interval)
        _flush()

def _flush():
    with _queue_lock:
        if not _queue:
            return
        items = list(_queue)
        _queue.clear()
    for item in items:
        item.pop("_type", None)
    if items:
        _post("/spans", items)

def _post(path: str, data):
    if not _config["api_key"] or not _HTTP_CLIENT:
        return
    try:
        _HTTP_CLIENT.post(
            f"{VANTRA_ENDPOINT}{path}",
            json=data,
            headers={"Authorization": f"Bearer {_config['api_key']}"},
        )
    except Exception:
        pass


def init(
    api_key: str,
    project: str,
    enabled: bool = True,
    capture_io: bool = True,
    patch_openai: bool = True,
    patch_anthropic: bool = True,
):
    _config["api_key"] = api_key
    _config["project"] = project
    _config["enabled"] = enabled
    _config["capture_io"] = capture_io

    t = threading.Thread(target=_flush_worker, daemon=True)
    t.start()

    if patch_openai:
        from vantra.integrations.openai import patch
        patch()

    if patch_anthropic:
        from vantra.integrations.anthropic import patch
        patch()


def _finish_trace(trace_id, trace_name, start, status, error_msg, prompt_version=None):
    collected = _pending_spans.get() or []
    end = time.time()
    total_tokens = sum((s.get("input_tokens") or 0) + (s.get("output_tokens") or 0) for s in collected)
    total_cost = sum(s.get("cost_usd") or 0 for s in collected)
    payload = {
        "trace_id": trace_id,
        "name": trace_name,
        "project": _config["project"],
        "start_time": start,
        "end_time": end,
        "duration_ms": int((end - start) * 1000),
        "status": status,
        "error_message": error_msg,
        "total_tokens": total_tokens,
        "total_cost_usd": total_cost,
    }
    if prompt_version:
        payload["prompt_version"] = str(prompt_version)[:100]
    _post("/traces", payload)
    if collected:
        _post("/spans", collected)


def trace(func=None, *, name: str = None, prompt_version: str = None):
    def decorator(fn):
        if asyncio.iscoroutinefunction(fn):
            @functools.wraps(fn)
            async def async_wrapper(*args, **kwargs):
                if not _config["enabled"]:
                    return await fn(*args, **kwargs)

                trace_id = str(uuid.uuid4())
                trace_name = name or fn.__name__
                start = time.time()
                status = "ok"
                error_msg = None

                trace_token = _current_trace_id.set(trace_id)
                spans_token = _pending_spans.set([])
                try:
                    result = await fn(*args, **kwargs)
                    return result
                except Exception as e:
                    status = "error"
                    error_msg = str(e)
                    raise
                finally:
                    _current_trace_id.reset(trace_token)
                    _finish_trace(trace_id, trace_name, start, status, error_msg, prompt_version)
                    _pending_spans.reset(spans_token)

            return async_wrapper
        else:
            @functools.wraps(fn)
            def sync_wrapper(*args, **kwargs):
                if not _config["enabled"]:
                    return fn(*args, **kwargs)

                trace_id = str(uuid.uuid4())
                trace_name = name or fn.__name__
                start = time.time()
                status = "ok"
                error_msg = None

                trace_token = _current_trace_id.set(trace_id)
                spans_token = _pending_spans.set([])
                try:
                    result = fn(*args, **kwargs)
                    return result
                except Exception as e:
                    status = "error"
                    error_msg = str(e)
                    raise
                finally:
                    _current_trace_id.reset(trace_token)
                    _finish_trace(trace_id, trace_name, start, status, error_msg, prompt_version)
                    _pending_spans.reset(spans_token)

            return sync_wrapper

    if func is not None:
        return decorator(func)
    return decorator


def _queue_span(span_data: dict):
    pending = _pending_spans.get()
    if pending is not None:
        pending.append(span_data)
    else:
        with _queue_lock:
            _queue.append({"_type": "span", **span_data})


@contextmanager
def span(name: str, kind: str = "chain", model: str = None, **metadata):
    span_id = str(uuid.uuid4())
    start = time.time()
    status = "ok"
    error_msg = None
    _input = {}
    _output = {}
    _tokens = {}

    class SpanContext:
        id = span_id

        def set_input(self, data: Any):
            if _config["capture_io"]:
                _input["input"] = _truncate(data)

        def set_output(self, data: Any):
            if _config["capture_io"]:
                _output["output"] = _truncate(data)

        def set_tokens(self, input_tokens: int, output_tokens: int):
            _tokens["input_tokens"] = input_tokens
            _tokens["output_tokens"] = output_tokens

    ctx = SpanContext()
    try:
        yield ctx
    except Exception as e:
        status = "error"
        error_msg = str(e)
        raise
    finally:
        end = time.time()
        _queue_span({
            "span_id": span_id,
            "trace_id": _current_trace_id.get(),
            "name": name,
            "kind": kind,
            "model": model,
            "project": _config["project"],
            "start_time": start,
            "end_time": end,
            "duration_ms": int((end - start) * 1000),
            "status": status,
            "error_message": error_msg,
            "metadata": metadata,
            **_input,
            **_output,
            **_tokens,
        })


def _truncate(data: Any, max_chars: int = 2000) -> Any:
    import json
    try:
        s = json.dumps(data)
        if len(s) <= max_chars:
            return data
        return {"_truncated": True, "preview": s[:max_chars]}
    except Exception:
        return str(data)[:max_chars]
