import time
import uuid
from vantra._costs import calculate_cost


def patch():
    try:
        import openai
        from openai.resources.chat.completions import Completions
        original = Completions.create

        def patched(self, *args, **kwargs):
            from vantra import _truncate, _config, _current_trace_id, _queue_span

            start = time.time()
            model = kwargs.get("model", "unknown")
            messages = kwargs.get("messages", [])
            capture_io = _config.get("capture_io", True)

            if kwargs.get("stream"):
                # Inject stream_options so OpenAI includes usage in the final chunk
                if "stream_options" not in kwargs:
                    kwargs = {**kwargs, "stream_options": {"include_usage": True}}
                try:
                    stream = original(self, *args, **kwargs)
                except Exception as e:
                    _queue_span(_error_span(model, messages, capture_io, start, str(e),
                                            _current_trace_id.get(), _config["project"], _truncate))
                    raise
                return _wrap_openai_stream(
                    stream, start, model, messages, capture_io,
                    _current_trace_id.get(), _config["project"], _queue_span, _truncate,
                )

            # Non-streaming path
            status = "ok"
            error_msg = None
            response = None
            try:
                response = original(self, *args, **kwargs)
                return response
            except Exception as e:
                status = "error"
                error_msg = str(e)
                raise
            finally:
                end = time.time()
                input_tokens = output_tokens = 0
                cost = 0.0
                if response and hasattr(response, "usage") and response.usage:
                    input_tokens = response.usage.prompt_tokens or 0
                    output_tokens = response.usage.completion_tokens or 0
                    cost = calculate_cost(model, input_tokens, output_tokens)
                output_content = None
                if response and response.choices:
                    msg = response.choices[0].message
                    output_content = {"content": (msg.content or "")[:1000]}
                _queue_span({
                    "span_id": str(uuid.uuid4()),
                    "trace_id": _current_trace_id.get(),
                    "name": f"openai.chat ({model})",
                    "kind": "llm",
                    "provider": "openai",
                    "model": model,
                    "project": _config["project"],
                    "start_time": start,
                    "end_time": end,
                    "duration_ms": int((end - start) * 1000),
                    "status": status,
                    "error_message": error_msg,
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "cost_usd": cost,
                    "input": _truncate({"messages": messages}) if capture_io else None,
                    "output": output_content if capture_io else None,
                })

        Completions.create = patched
    except ImportError:
        pass


def _error_span(model, messages, capture_io, start, error_msg, trace_id, project, truncate):
    end = time.time()
    return {
        "span_id": str(uuid.uuid4()),
        "trace_id": trace_id,
        "name": f"openai.chat ({model})",
        "kind": "llm",
        "provider": "openai",
        "model": model,
        "project": project,
        "start_time": start,
        "end_time": end,
        "duration_ms": int((end - start) * 1000),
        "status": "error",
        "error_message": error_msg,
        "input_tokens": 0,
        "output_tokens": 0,
        "cost_usd": 0.0,
        "input": truncate({"messages": messages}) if capture_io else None,
        "output": None,
    }


def _wrap_openai_stream(stream, start, model, messages, capture_io, trace_id, project, queue_span, truncate):
    content_chunks = []
    input_tokens = 0
    output_tokens = 0
    status = "ok"
    error_msg = None

    try:
        for chunk in stream:
            # Final chunk carries usage when stream_options.include_usage=True
            if hasattr(chunk, "usage") and chunk.usage:
                input_tokens = chunk.usage.prompt_tokens or 0
                output_tokens = chunk.usage.completion_tokens or 0
            if chunk.choices:
                delta = chunk.choices[0].delta
                if delta and delta.content:
                    content_chunks.append(delta.content)
            yield chunk
    except Exception as e:
        status = "error"
        error_msg = str(e)
        raise
    finally:
        end = time.time()
        cost = calculate_cost(model, input_tokens, output_tokens)
        full_content = "".join(content_chunks)
        queue_span({
            "span_id": str(uuid.uuid4()),
            "trace_id": trace_id,
            "name": f"openai.chat ({model})",
            "kind": "llm",
            "provider": "openai",
            "model": model,
            "project": project,
            "start_time": start,
            "end_time": end,
            "duration_ms": int((end - start) * 1000),
            "status": status,
            "error_message": error_msg,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cost_usd": cost,
            "input": truncate({"messages": messages}) if capture_io else None,
            "output": {"content": full_content[:1000]} if capture_io and full_content else None,
        })
