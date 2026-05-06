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

                if response and hasattr(response, "usage") and response.usage:
                    input_tokens = response.usage.prompt_tokens or 0
                    output_tokens = response.usage.completion_tokens or 0
                    cost = calculate_cost(model, input_tokens, output_tokens)

                messages = kwargs.get("messages", [])
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
                    "input": _truncate({"messages": messages[-1:]}),
                    "output": output_content,
                })

        Completions.create = patched
    except ImportError:
        pass
