import time
import uuid
from vantra._costs import calculate_cost

def patch():
    try:
        import anthropic
        _patch_client(anthropic)
    except ImportError:
        pass


def _patch_client(anthropic_module):
    original = anthropic_module.Anthropic.messages.create

    def patched(self, *args, **kwargs):
        from vantra import _queue, _queue_lock, _truncate, _config

        start = time.time()
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
            model = kwargs.get("model", "unknown")
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

            from vantra import _current_trace_id
            with _queue_lock:
                _queue.append({
                    "_type": "span",
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
                    "input": _truncate({"messages": kwargs.get("messages", [])[-1:]}),
                    "output": output_text,
                })

    anthropic_module.Anthropic.messages.create = patched
