import time
import uuid
from vantra._costs import calculate_cost


def patch():
    try:
        from anthropic.resources.messages.messages import Messages
        _patch_messages_class(Messages)
    except ImportError:
        pass
    except Exception:
        pass


def _patch_messages_class(Messages):
    original = Messages.create
    if getattr(original, "__vantra__", False):
        return

    def patched(self, *args, **kwargs):
        from vantra import _truncate, _config, _current_trace_id, _queue_span

        start = time.time()
        model = kwargs.get("model", "unknown")
        messages = kwargs.get("messages", [])
        capture_io = _config.get("capture_io", True)

        if kwargs.get("stream"):
            try:
                stream = original(self, *args, **kwargs)
            except Exception as e:
                _queue_span(_error_span(model, messages, capture_io, start, str(e),
                                        _current_trace_id.get(), _config["project"], _truncate))
                raise
            return _wrap_anthropic_stream(
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
            if response and hasattr(response, "usage"):
                input_tokens = response.usage.input_tokens or 0
                output_tokens = response.usage.output_tokens or 0
                cost = calculate_cost(model, input_tokens, output_tokens)
            output_text = None
            if response and response.content:
                block = response.content[0]
                output_text = {"text": getattr(block, "text", "")[:1000]}
            _queue_span({
                "span_id": str(uuid.uuid4()),
                "trace_id": _current_trace_id.get(),
                "name": f"anthropic.messages ({model})",
                "kind": "llm",
                "provider": "anthropic",
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
                "output": output_text if capture_io else None,
            })

    patched.__vantra__ = True
    Messages.create = patched


def _error_span(model, messages, capture_io, start, error_msg, trace_id, project, truncate):
    end = time.time()
    return {
        "span_id": str(uuid.uuid4()),
        "trace_id": trace_id,
        "name": f"anthropic.messages ({model})",
        "kind": "llm",
        "provider": "anthropic",
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


def _wrap_anthropic_stream(stream, start, model, messages, capture_io, trace_id, project, queue_span, truncate):
    # Anthropic stream events carry usage natively — no extra options needed
    content_chunks = []
    input_tokens = 0
    output_tokens = 0
    status = "ok"
    error_msg = None

    try:
        for event in stream:
            event_type = getattr(event, "type", None)

            # message_start carries input token count
            if event_type == "message_start":
                msg = getattr(event, "message", None)
                if msg and hasattr(msg, "usage"):
                    input_tokens = msg.usage.input_tokens or 0

            # content_block_delta carries text chunks (text_delta type only; skip input_json_delta for tool use)
            elif event_type == "content_block_delta":
                delta = getattr(event, "delta", None)
                text = getattr(delta, "text", None) if delta else None
                if isinstance(text, str) and text:
                    content_chunks.append(text)

            # message_delta carries output token count
            elif event_type == "message_delta":
                usage = getattr(event, "usage", None)
                if usage:
                    output_tokens = usage.output_tokens or 0

            yield event
    except Exception as e:
        status = "error"
        error_msg = str(e)
        raise
    finally:
        end = time.time()
        cost = calculate_cost(model, input_tokens, output_tokens)
        full_text = "".join(content_chunks)
        queue_span({
            "span_id": str(uuid.uuid4()),
            "trace_id": trace_id,
            "name": f"anthropic.messages ({model})",
            "kind": "llm",
            "provider": "anthropic",
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
            "output": {"text": full_text[:1000]} if capture_io and full_text else None,
        })
