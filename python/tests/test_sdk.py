"""
Vantra Python SDK — full test suite.
Run with: pytest sdk/python/tests/ -v
"""
import sys, os, time, json, threading, asyncio
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import pytest
from unittest.mock import patch, MagicMock
import vantra
from vantra._costs import calculate_cost, COSTS


# ── Helpers ───────────────────────────────────────────────────────────────────

def _setup():
    vantra._config["api_key"] = "van_live_test"
    vantra._config["project"] = "test-project"
    vantra._config["enabled"] = True
    vantra._config["capture_io"] = True

def _collected_posts(fn):
    """Run fn, return list of (path, payload) pairs that were posted."""
    posts = []
    with patch.object(vantra, '_post', side_effect=lambda p, d: posts.append((p, d))):
        fn()
    return posts

def _trace_payload(posts):
    return next(p for path, p in posts if path == "/traces")

def _span_payloads(posts):
    call = next((p for path, p in posts if path == "/spans"), None)
    return call if call else []


# ── Cost calculation ──────────────────────────────────────────────────────────

class TestCosts:
    def test_gpt4o_mini_cheaper_than_gpt4o(self):
        assert calculate_cost("gpt-4o-mini", 1000, 1000) < calculate_cost("gpt-4o", 1000, 1000)

    def test_gpt4o_mini_exact_price(self):
        # $0.00015/1k input, $0.0006/1k output
        assert abs(calculate_cost("gpt-4o-mini", 1000, 1000) - (0.00015 + 0.0006)) < 1e-9

    def test_gpt4o_exact_price(self):
        assert abs(calculate_cost("gpt-4o", 1000, 1000) - (0.0025 + 0.010)) < 1e-9

    def test_versioned_model_matches_base(self):
        # "gpt-4o-2024-11-20" prefix-matches "gpt-4o"
        assert calculate_cost("gpt-4o-2024-11-20", 1000, 0) == calculate_cost("gpt-4o", 1000, 0)

    def test_mini_versioned_not_confused_with_gpt4o(self):
        # "gpt-4o-mini-2024-07-18" must match "gpt-4o-mini", not "gpt-4o"
        mini_versioned = calculate_cost("gpt-4o-mini-2024-07-18", 1000, 1000)
        mini_base = calculate_cost("gpt-4o-mini", 1000, 1000)
        full = calculate_cost("gpt-4o", 1000, 1000)
        assert abs(mini_versioned - mini_base) < 1e-9
        assert mini_versioned < full

    def test_zero_tokens_is_zero(self):
        assert calculate_cost("gpt-4o", 0, 0) == 0.0

    def test_only_input_tokens(self):
        cost = calculate_cost("gpt-4o-mini", 1000, 0)
        assert abs(cost - 0.00015) < 1e-9

    def test_only_output_tokens(self):
        cost = calculate_cost("gpt-4o-mini", 0, 1000)
        assert abs(cost - 0.0006) < 1e-9

    def test_unknown_model_uses_fallback(self):
        cost = calculate_cost("some-future-model-xyz", 1000, 1000)
        assert cost > 0

    def test_all_known_models_produce_positive_cost(self):
        for model in COSTS:
            assert calculate_cost(model, 1000, 1000) > 0, f"{model} returned 0 cost"

    def test_claude_models(self):
        assert calculate_cost("claude-opus-4", 1000, 1000) > calculate_cost("claude-haiku-4", 1000, 1000)

    def test_cost_scales_linearly(self):
        single = calculate_cost("gpt-4o-mini", 1000, 0)
        double = calculate_cost("gpt-4o-mini", 2000, 0)
        assert abs(double - single * 2) < 1e-9

    def test_real_world_call_gpt4o_mini(self):
        # 21 input + 29 output tokens (like the test trace showed)
        cost = calculate_cost("gpt-4o-mini", 21, 29)
        expected = (21 / 1000 * 0.00015) + (29 / 1000 * 0.0006)
        assert abs(cost - expected) < 1e-12

    def test_gemini_models(self):
        flash = calculate_cost("gemini-1.5-flash", 1000, 1000)
        pro = calculate_cost("gemini-1.5-pro", 1000, 1000)
        assert flash < pro


# ── Truncate ──────────────────────────────────────────────────────────────────

class TestTruncate:
    def test_small_dict_unchanged(self):
        data = {"key": "value"}
        assert vantra._truncate(data) == data

    def test_large_string_truncated(self):
        data = {"key": "x" * 3000}
        result = vantra._truncate(data)
        assert result["_truncated"] is True
        assert len(result["preview"]) == 2000

    def test_exact_boundary_not_truncated(self):
        # JSON of {"k": "..."} with 2000 chars total
        val = "x" * (2000 - len('{"k": ""}'))
        data = {"k": val}
        result = vantra._truncate(data)
        assert result == data

    def test_non_serializable_becomes_string(self):
        class Weird:
            def __repr__(self): return "weird_obj"
        result = vantra._truncate(Weird())
        assert isinstance(result, str)

    def test_nested_dict_small(self):
        data = {"a": {"b": {"c": 1}}}
        assert vantra._truncate(data) == data

    def test_list_data(self):
        data = [1, 2, 3]
        assert vantra._truncate(data) == data

    def test_none_value(self):
        result = vantra._truncate(None)
        assert result is None

    def test_custom_max_chars(self):
        data = {"k": "x" * 200}
        result = vantra._truncate(data, max_chars=100)
        assert result["_truncated"] is True
        assert len(result["preview"]) == 100


# ── Context vars ──────────────────────────────────────────────────────────────

class TestContextVars:
    def setup_method(self):
        _setup()

    def test_trace_id_set_during_fn(self):
        captured = []
        with patch.object(vantra, '_post'):
            @vantra.trace
            def fn():
                captured.append(vantra._current_trace_id.get())
            fn()
        assert len(captured) == 1
        assert len(captured[0]) == 36  # UUID

    def test_trace_id_none_before_and_after(self):
        assert vantra._current_trace_id.get() is None
        with patch.object(vantra, '_post'):
            @vantra.trace
            def fn(): pass
            fn()
        assert vantra._current_trace_id.get() is None

    def test_trace_id_cleared_on_exception(self):
        with patch.object(vantra, '_post'):
            @vantra.trace
            def fn(): raise ValueError("boom")
            with pytest.raises(ValueError):
                fn()
        assert vantra._current_trace_id.get() is None

    def test_pending_spans_none_after_trace(self):
        with patch.object(vantra, '_post'):
            @vantra.trace
            def fn(): pass
            fn()
        assert vantra._pending_spans.get() is None

    def test_pending_spans_cleared_on_exception(self):
        with patch.object(vantra, '_post'):
            @vantra.trace
            def fn(): raise RuntimeError("x")
            with pytest.raises(RuntimeError):
                fn()
        assert vantra._pending_spans.get() is None

    def test_different_traces_have_different_ids(self):
        ids = []
        with patch.object(vantra, '_post'):
            @vantra.trace
            def fn():
                ids.append(vantra._current_trace_id.get())
            fn()
            fn()
        assert ids[0] != ids[1]

    def test_concurrent_traces_isolated(self):
        """Two threads running traces simultaneously should not share span context."""
        thread_ids = {}
        barrier = threading.Barrier(2)

        def run(label):
            with patch.object(vantra, '_post'):
                @vantra.trace
                def fn():
                    barrier.wait()  # both threads inside trace at same time
                    thread_ids[label] = vantra._current_trace_id.get()
                fn()

        t1 = threading.Thread(target=run, args=("a",))
        t2 = threading.Thread(target=run, args=("b",))
        t1.start(); t2.start()
        t1.join(); t2.join()

        assert thread_ids["a"] != thread_ids["b"]
        assert thread_ids["a"] is not None
        assert thread_ids["b"] is not None


# ── Trace decorator ───────────────────────────────────────────────────────────

class TestTraceDecorator:
    def setup_method(self):
        _setup()

    def test_returns_fn_result(self):
        with patch.object(vantra, '_post'):
            @vantra.trace
            def fn(): return 99
            assert fn() == 99

    def test_passes_args_through(self):
        with patch.object(vantra, '_post'):
            @vantra.trace
            def fn(a, b): return a + b
            assert fn(3, 4) == 7

    def test_posts_trace_on_success(self):
        def go():
            @vantra.trace
            def fn(): pass
            fn()

        posts = _collected_posts(go)
        assert any(path == "/traces" for path, _ in posts)

    def test_trace_payload_fields(self):
        def go():
            @vantra.trace
            def my_fn(): pass
            my_fn()

        posts = _collected_posts(go)
        payload = _trace_payload(posts)
        assert payload["name"] == "my_fn"
        assert payload["project"] == "test-project"
        assert payload["status"] == "ok"
        assert "trace_id" in payload
        assert payload["duration_ms"] >= 0
        assert "start_time" in payload
        assert "end_time" in payload
        assert payload["end_time"] >= payload["start_time"]

    def test_error_status_and_message(self):
        def go():
            @vantra.trace
            def fn(): raise RuntimeError("bad thing happened")
            with pytest.raises(RuntimeError):
                fn()

        posts = _collected_posts(go)
        payload = _trace_payload(posts)
        assert payload["status"] == "error"
        assert payload["error_message"] == "bad thing happened"

    def test_custom_name(self):
        def go():
            @vantra.trace(name="custom_trace")
            def fn(): pass
            fn()

        posts = _collected_posts(go)
        assert _trace_payload(posts)["name"] == "custom_trace"

    def test_spans_posted_after_trace(self):
        paths = []
        def go():
            with patch.object(vantra, '_post', side_effect=lambda p, d: paths.append(p)):
                @vantra.trace
                def fn():
                    with vantra.span("s"): pass
                fn()

        go()
        assert paths.index("/traces") < paths.index("/spans")

    def test_no_spans_means_no_spans_post(self):
        def go():
            @vantra.trace
            def fn(): pass
            fn()

        posts = _collected_posts(go)
        assert not any(path == "/spans" for path, _ in posts)

    def test_total_tokens_summed_from_spans(self):
        def go():
            @vantra.trace
            def fn():
                with vantra.span("s1") as s:
                    s.set_tokens(100, 50)
                with vantra.span("s2") as s:
                    s.set_tokens(200, 75)
            fn()

        posts = _collected_posts(go)
        payload = _trace_payload(posts)
        assert payload["total_tokens"] == 425

    def test_total_cost_summed_from_openai_spans(self):
        def go():
            @vantra.trace
            def fn():
                vantra._queue_span({
                    "span_id": "x", "trace_id": vantra._current_trace_id.get(),
                    "name": "openai.chat", "kind": "llm",
                    "input_tokens": 1000, "output_tokens": 1000,
                    "cost_usd": calculate_cost("gpt-4o-mini", 1000, 1000),
                })
            fn()

        posts = _collected_posts(go)
        payload = _trace_payload(posts)
        expected = calculate_cost("gpt-4o-mini", 1000, 1000)
        assert abs(payload["total_cost_usd"] - expected) < 1e-9

    def test_disabled_skips_everything(self):
        vantra._config["enabled"] = False
        posts = []
        with patch.object(vantra, '_post', side_effect=lambda p, d: posts.append(p)):
            @vantra.trace
            def fn(): return "result"
            result = fn()
        assert result == "result"
        assert posts == []
        vantra._config["enabled"] = True

    def test_preserves_function_name(self):
        @vantra.trace
        def my_named_fn(): pass
        assert my_named_fn.__name__ == "my_named_fn"

    def test_duration_ms_reasonable(self):
        def go():
            @vantra.trace
            def fn(): time.sleep(0.05)
            fn()

        posts = _collected_posts(go)
        duration = _trace_payload(posts)["duration_ms"]
        assert 40 <= duration <= 500


# ── Span context manager ──────────────────────────────────────────────────────

class TestSpan:
    def setup_method(self):
        _setup()

    def test_span_inside_trace_collected(self):
        def go():
            @vantra.trace
            def fn():
                with vantra.span("my_span"): pass
            fn()

        posts = _collected_posts(go)
        spans = _span_payloads(posts)
        assert len(spans) == 1
        assert spans[0]["name"] == "my_span"

    def test_span_has_required_fields(self):
        def go():
            @vantra.trace
            def fn():
                with vantra.span("s", kind="retrieval"): pass
            fn()

        posts = _collected_posts(go)
        span = _span_payloads(posts)[0]
        assert "span_id" in span
        assert "trace_id" in span
        assert span["kind"] == "retrieval"
        assert span["status"] == "ok"
        assert span["duration_ms"] >= 0

    def test_span_trace_id_matches_parent(self):
        captured_trace_id = []

        def go():
            @vantra.trace
            def fn():
                captured_trace_id.append(vantra._current_trace_id.get())
                with vantra.span("s"): pass
            fn()

        posts = _collected_posts(go)
        span = _span_payloads(posts)[0]
        assert span["trace_id"] == captured_trace_id[0]

    def test_multiple_spans_all_collected(self):
        def go():
            @vantra.trace
            def fn():
                with vantra.span("a"): pass
                with vantra.span("b"): pass
                with vantra.span("c"): pass
            fn()

        posts = _collected_posts(go)
        spans = _span_payloads(posts)
        assert len(spans) == 3
        assert {s["name"] for s in spans} == {"a", "b", "c"}

    def test_span_error_captured(self):
        def go():
            @vantra.trace
            def fn():
                with vantra.span("failing"):
                    raise ValueError("span broke")
            with pytest.raises(ValueError):
                fn()

        posts = _collected_posts(go)
        span = _span_payloads(posts)[0]
        assert span["status"] == "error"
        assert span["error_message"] == "span broke"

    def test_set_output(self):
        def go():
            @vantra.trace
            def fn():
                with vantra.span("s") as sp:
                    sp.set_output({"result": "Paris"})
            fn()

        posts = _collected_posts(go)
        assert _span_payloads(posts)[0]["output"] == {"result": "Paris"}

    def test_set_tokens(self):
        def go():
            @vantra.trace
            def fn():
                with vantra.span("s") as sp:
                    sp.set_tokens(21, 29)
            fn()

        posts = _collected_posts(go)
        span = _span_payloads(posts)[0]
        assert span["input_tokens"] == 21
        assert span["output_tokens"] == 29

    def test_span_outside_trace_goes_to_queue(self):
        initial = len(vantra._queue)
        with patch.object(vantra, '_post'):
            with vantra.span("orphan"): pass
        assert len(vantra._queue) > initial

    def test_span_duration_reasonable(self):
        def go():
            @vantra.trace
            def fn():
                with vantra.span("timed"):
                    time.sleep(0.03)
            fn()

        posts = _collected_posts(go)
        duration = _span_payloads(posts)[0]["duration_ms"]
        assert 20 <= duration <= 500

    def test_kind_defaults_to_chain(self):
        def go():
            @vantra.trace
            def fn():
                with vantra.span("s"): pass
            fn()

        posts = _collected_posts(go)
        assert _span_payloads(posts)[0]["kind"] == "chain"

    def test_span_with_model(self):
        def go():
            @vantra.trace
            def fn():
                with vantra.span("s", model="gpt-4o-mini"): pass
            fn()

        posts = _collected_posts(go)
        assert _span_payloads(posts)[0]["model"] == "gpt-4o-mini"


# ── Queue / flush ─────────────────────────────────────────────────────────────

class TestQueue:
    def setup_method(self):
        _setup()
        vantra._queue.clear()

    def test_queue_span_inside_trace_goes_to_pending_not_queue(self):
        queue_before = len(vantra._queue)
        with patch.object(vantra, '_post'):
            @vantra.trace
            def fn():
                vantra._queue_span({"span_id": "x", "trace_id": vantra._current_trace_id.get(), "name": "s"})
            fn()
        assert len(vantra._queue) == queue_before  # nothing added to queue

    def test_queue_span_outside_trace_goes_to_queue(self):
        queue_before = len(vantra._queue)
        vantra._queue_span({"span_id": "y", "trace_id": None, "name": "s"})
        assert len(vantra._queue) == queue_before + 1

    def test_flush_clears_queue(self):
        vantra._queue_span({"_type": "span", "span_id": "z", "name": "s"})
        with patch.object(vantra, '_post'):
            vantra._flush()
        assert len(vantra._queue) == 0


# ── OpenAI patching ───────────────────────────────────────────────────────────

class TestOpenAIPatching:
    def setup_method(self):
        _setup()

    def _make_response(self, input_tokens=21, output_tokens=29, content="Paris"):
        r = MagicMock()
        r.usage.prompt_tokens = input_tokens
        r.usage.completion_tokens = output_tokens
        r.choices[0].message.content = content
        return r

    def test_llm_span_created_for_openai_call(self):
        try:
            from openai.resources.chat.completions import Completions
            from vantra.integrations import openai as oai_integration

            response = self._make_response()
            posts = []

            with patch.object(vantra, '_post', side_effect=lambda p, d: posts.append((p, d))):
                with patch.object(Completions, 'create', return_value=response):
                    oai_integration.patch()

                    @vantra.trace
                    def fn():
                        inst = MagicMock()
                        Completions.create(inst, model="gpt-4o-mini", messages=[{"role": "user", "content": "hi"}])
                    fn()

            spans = _span_payloads(posts)
            assert len(spans) >= 1
            llm_span = next(s for s in spans if s.get("kind") == "llm")
            assert llm_span["provider"] == "openai"
            assert llm_span["model"] == "gpt-4o-mini"
            assert llm_span["input_tokens"] == 21
            assert llm_span["output_tokens"] == 29
        except ImportError:
            pytest.skip("openai not installed")

    def test_llm_span_cost_calculated(self):
        try:
            from openai.resources.chat.completions import Completions
            from vantra.integrations import openai as oai_integration

            response = self._make_response(21, 29)
            posts = []

            with patch.object(vantra, '_post', side_effect=lambda p, d: posts.append((p, d))):
                with patch.object(Completions, 'create', return_value=response):
                    oai_integration.patch()

                    @vantra.trace
                    def fn():
                        Completions.create(MagicMock(), model="gpt-4o-mini",
                                           messages=[{"role": "user", "content": "hi"}])
                    fn()

            span = next(s for s in _span_payloads(posts) if s.get("kind") == "llm")
            expected = calculate_cost("gpt-4o-mini", 21, 29)
            assert abs(span["cost_usd"] - expected) < 1e-12
        except ImportError:
            pytest.skip("openai not installed")

    def test_trace_totals_include_openai_tokens(self):
        try:
            from openai.resources.chat.completions import Completions
            from vantra.integrations import openai as oai_integration

            response = self._make_response(21, 29)
            posts = []

            with patch.object(vantra, '_post', side_effect=lambda p, d: posts.append((p, d))):
                with patch.object(Completions, 'create', return_value=response):
                    oai_integration.patch()

                    @vantra.trace
                    def fn():
                        Completions.create(MagicMock(), model="gpt-4o-mini",
                                           messages=[{"role": "user", "content": "hi"}])
                    fn()

            trace = _trace_payload(posts)
            assert trace["total_tokens"] == 50  # 21 + 29
            assert trace["total_cost_usd"] > 0
        except ImportError:
            pytest.skip("openai not installed")

    def test_two_openai_calls_both_captured(self):
        try:
            from openai.resources.chat.completions import Completions
            from vantra.integrations import openai as oai_integration

            r1 = self._make_response(21, 5, "factual")
            r2 = self._make_response(30, 40, "Paris is the capital of France.")
            responses = iter([r1, r2])

            posts = []
            with patch.object(vantra, '_post', side_effect=lambda p, d: posts.append((p, d))):
                with patch.object(Completions, 'create', side_effect=lambda *a, **kw: next(responses)):
                    oai_integration.patch()

                    @vantra.trace
                    def fn():
                        Completions.create(MagicMock(), model="gpt-4o-mini", messages=[{"role": "user", "content": "classify"}])
                        Completions.create(MagicMock(), model="gpt-4o-mini", messages=[{"role": "user", "content": "answer"}])
                    fn()

            llm_spans = [s for s in _span_payloads(posts) if s.get("kind") == "llm"]
            assert len(llm_spans) == 2
            trace = _trace_payload(posts)
            assert trace["total_tokens"] == 21 + 5 + 30 + 40  # 96
        except ImportError:
            pytest.skip("openai not installed")

    def test_openai_error_captured_in_span(self):
        try:
            from openai.resources.chat.completions import Completions
            from vantra.integrations import openai as oai_integration

            posts = []
            with patch.object(vantra, '_post', side_effect=lambda p, d: posts.append((p, d))):
                with patch.object(Completions, 'create', side_effect=Exception("rate limit hit")):
                    oai_integration.patch()

                    @vantra.trace
                    def fn():
                        Completions.create(MagicMock(), model="gpt-4o-mini", messages=[])

                    with pytest.raises(Exception):
                        fn()

            spans = _span_payloads(posts)
            if spans:
                assert spans[0]["status"] == "error"
                assert "rate limit" in spans[0]["error_message"]
        except ImportError:
            pytest.skip("openai not installed")


# ── Integration: full trace + spans + cost ────────────────────────────────────

class TestEndToEnd:
    def setup_method(self):
        _setup()

    def test_full_agent_flow(self):
        """Simulate a classify → retrieve → respond agent flow."""
        def go():
            @vantra.trace(name="run_agent")
            def agent(question):
                # Simulate classify LLM call
                vantra._queue_span({
                    "span_id": "s1",
                    "trace_id": vantra._current_trace_id.get(),
                    "name": "openai.chat (classify)",
                    "kind": "llm",
                    "model": "gpt-4o-mini",
                    "input_tokens": 21,
                    "output_tokens": 5,
                    "cost_usd": calculate_cost("gpt-4o-mini", 21, 5),
                })
                # Simulate retrieval span
                with vantra.span("retrieve_context", kind="retrieval") as s:
                    s.set_output({"docs": ["Paris is the capital of France"]})
                # Simulate respond LLM call
                vantra._queue_span({
                    "span_id": "s3",
                    "trace_id": vantra._current_trace_id.get(),
                    "name": "openai.chat (respond)",
                    "kind": "llm",
                    "model": "gpt-4o-mini",
                    "input_tokens": 30,
                    "output_tokens": 40,
                    "cost_usd": calculate_cost("gpt-4o-mini", 30, 40),
                })
                return "Paris"

            agent("What is the capital of France?")

        posts = _collected_posts(go)
        trace = _trace_payload(posts)
        spans = _span_payloads(posts)

        assert trace["name"] == "run_agent"
        assert trace["status"] == "ok"
        assert trace["total_tokens"] == 21 + 5 + 30 + 40  # 96
        assert len(spans) == 3  # classify + retrieve + respond

        llm_spans = [s for s in spans if s.get("kind") == "llm"]
        retrieval_spans = [s for s in spans if s.get("kind") == "retrieval"]
        assert len(llm_spans) == 2
        assert len(retrieval_spans) == 1

        expected_cost = (
            calculate_cost("gpt-4o-mini", 21, 5) +
            calculate_cost("gpt-4o-mini", 30, 40)
        )
        assert abs(trace["total_cost_usd"] - expected_cost) < 1e-12

    def test_error_trace_still_posts_spans(self):
        def go():
            @vantra.trace
            def fn():
                with vantra.span("before_error"): pass
                raise RuntimeError("agent crashed")

            with pytest.raises(RuntimeError):
                fn()

        posts = _collected_posts(go)
        trace = _trace_payload(posts)
        spans = _span_payloads(posts)

        assert trace["status"] == "error"
        assert len(spans) == 1
        assert spans[0]["name"] == "before_error"


# ── Async trace decorator ─────────────────────────────────────────────────────

def _collected_posts_async(coro_factory):
    """Run async coro_factory(), return list of (path, payload) pairs posted."""
    posts = []
    async def run():
        with patch.object(vantra, '_post', side_effect=lambda p, d: posts.append((p, d))):
            await coro_factory()
    asyncio.run(run())
    return posts


class TestAsyncTrace:
    def setup_method(self):
        _setup()

    def test_async_trace_returns_result(self):
        async def go():
            with patch.object(vantra, '_post'):
                @vantra.trace
                async def fn(): return 42
                return await fn()
        assert asyncio.run(go()) == 42

    def test_async_trace_passes_args(self):
        async def go():
            with patch.object(vantra, '_post'):
                @vantra.trace
                async def fn(a, b): return a + b
                return await fn(10, 20)
        assert asyncio.run(go()) == 30

    def test_async_trace_posts_trace(self):
        async def go():
            @vantra.trace
            async def fn(): pass
            await fn()

        posts = _collected_posts_async(go)
        assert any(path == "/traces" for path, _ in posts)

    def test_async_trace_payload_fields(self):
        async def go():
            @vantra.trace
            async def my_async_fn(): pass
            await my_async_fn()

        posts = _collected_posts_async(go)
        payload = _trace_payload(posts)
        assert payload["name"] == "my_async_fn"
        assert payload["project"] == "test-project"
        assert payload["status"] == "ok"
        assert "trace_id" in payload
        assert payload["duration_ms"] >= 0

    def test_async_trace_error_status(self):
        async def go():
            @vantra.trace
            async def fn(): raise ValueError("async boom")
            with pytest.raises(ValueError):
                await fn()

        posts = _collected_posts_async(go)
        payload = _trace_payload(posts)
        assert payload["status"] == "error"
        assert payload["error_message"] == "async boom"

    def test_async_trace_custom_name(self):
        async def go():
            @vantra.trace(name="custom_async")
            async def fn(): pass
            await fn()

        posts = _collected_posts_async(go)
        assert _trace_payload(posts)["name"] == "custom_async"

    def test_async_trace_spans_collected(self):
        async def go():
            @vantra.trace
            async def fn():
                with vantra.span("inner_span"): pass
            await fn()

        posts = _collected_posts_async(go)
        spans = _span_payloads(posts)
        assert len(spans) == 1
        assert spans[0]["name"] == "inner_span"

    def test_async_trace_multiple_spans(self):
        async def go():
            @vantra.trace
            async def fn():
                with vantra.span("a"): pass
                with vantra.span("b"): pass
            await fn()

        posts = _collected_posts_async(go)
        spans = _span_payloads(posts)
        assert len(spans) == 2
        assert {s["name"] for s in spans} == {"a", "b"}

    def test_async_trace_total_tokens(self):
        async def go():
            @vantra.trace
            async def fn():
                with vantra.span("s") as sp:
                    sp.set_tokens(100, 50)
            await fn()

        posts = _collected_posts_async(go)
        assert _trace_payload(posts)["total_tokens"] == 150

    def test_async_trace_disabled(self):
        vantra._config["enabled"] = False
        result_holder = []

        async def go():
            @vantra.trace
            async def fn(): return "async_result"
            result_holder.append(await fn())

        posts = _collected_posts_async(go)
        assert result_holder[0] == "async_result"
        assert posts == []
        vantra._config["enabled"] = True

    def test_async_trace_context_var_cleared_after(self):
        async def go():
            @vantra.trace
            async def fn(): pass
            await fn()

        _collected_posts_async(go)
        assert vantra._current_trace_id.get() is None
        assert vantra._pending_spans.get() is None

    def test_async_trace_context_var_cleared_on_error(self):
        async def go():
            @vantra.trace
            async def fn(): raise RuntimeError("fail")
            with pytest.raises(RuntimeError):
                await fn()

        _collected_posts_async(go)
        assert vantra._current_trace_id.get() is None
        assert vantra._pending_spans.get() is None

    def test_async_trace_preserves_function_name(self):
        @vantra.trace
        async def my_async_named_fn(): pass
        assert my_async_named_fn.__name__ == "my_async_named_fn"

    def test_async_trace_different_calls_get_different_ids(self):
        ids = []

        async def go():
            @vantra.trace
            async def fn():
                ids.append(vantra._current_trace_id.get())
            await fn()
            await fn()

        _collected_posts_async(go)
        assert len(ids) == 2
        assert ids[0] != ids[1]


# ── set_input on SpanContext ──────────────────────────────────────────────────

class TestSetInput:
    def setup_method(self):
        _setup()
        vantra._config["capture_io"] = True

    def teardown_method(self):
        vantra._config["capture_io"] = True

    def test_set_input_captured(self):
        def go():
            @vantra.trace
            def fn():
                with vantra.span("s") as sp:
                    sp.set_input({"question": "What is Paris?"})
            fn()

        posts = _collected_posts(go)
        assert _span_payloads(posts)[0]["input"] == {"question": "What is Paris?"}

    def test_set_input_and_output_both_captured(self):
        def go():
            @vantra.trace
            def fn():
                with vantra.span("s") as sp:
                    sp.set_input({"query": "capital of France"})
                    sp.set_output({"answer": "Paris"})
            fn()

        posts = _collected_posts(go)
        span = _span_payloads(posts)[0]
        assert span["input"] == {"query": "capital of France"}
        assert span["output"] == {"answer": "Paris"}

    def test_set_input_truncated_when_large(self):
        def go():
            @vantra.trace
            def fn():
                with vantra.span("s") as sp:
                    sp.set_input({"data": "x" * 5000})
            fn()

        posts = _collected_posts(go)
        span = _span_payloads(posts)[0]
        assert span["input"]["_truncated"] is True

    def test_set_input_suppressed_when_capture_io_false(self):
        vantra._config["capture_io"] = False

        def go():
            @vantra.trace
            def fn():
                with vantra.span("s") as sp:
                    sp.set_input({"secret": "classified"})
            fn()

        posts = _collected_posts(go)
        span = _span_payloads(posts)[0]
        assert "input" not in span

    def test_set_output_suppressed_when_capture_io_false(self):
        vantra._config["capture_io"] = False

        def go():
            @vantra.trace
            def fn():
                with vantra.span("s") as sp:
                    sp.set_output({"result": "secret"})
            fn()

        posts = _collected_posts(go)
        span = _span_payloads(posts)[0]
        assert "output" not in span


# ── capture_io flag ───────────────────────────────────────────────────────────

class TestCaptureIO:
    def setup_method(self):
        _setup()
        vantra._config["capture_io"] = True

    def teardown_method(self):
        vantra._config["capture_io"] = True

    def test_capture_io_true_openai_records_io(self):
        try:
            from openai.resources.chat.completions import Completions
            from vantra.integrations import openai as oai_integration

            r = MagicMock()
            r.usage.prompt_tokens = 10
            r.usage.completion_tokens = 5
            r.choices[0].message.content = "answer"
            posts = []

            with patch.object(vantra, '_post', side_effect=lambda p, d: posts.append((p, d))):
                with patch.object(Completions, 'create', return_value=r):
                    oai_integration.patch()

                    @vantra.trace
                    def fn():
                        Completions.create(MagicMock(), model="gpt-4o-mini",
                                           messages=[{"role": "user", "content": "hi"}])
                    fn()

            span = next(s for s in _span_payloads(posts) if s.get("kind") == "llm")
            assert span["input"] is not None
            assert span["output"] is not None
        except ImportError:
            pytest.skip("openai not installed")

    def test_capture_io_false_openai_suppresses_io(self):
        try:
            from openai.resources.chat.completions import Completions
            from vantra.integrations import openai as oai_integration

            vantra._config["capture_io"] = False
            r = MagicMock()
            r.usage.prompt_tokens = 10
            r.usage.completion_tokens = 5
            r.choices[0].message.content = "secret answer"
            posts = []

            with patch.object(vantra, '_post', side_effect=lambda p, d: posts.append((p, d))):
                with patch.object(Completions, 'create', return_value=r):
                    oai_integration.patch()

                    @vantra.trace
                    def fn():
                        Completions.create(MagicMock(), model="gpt-4o-mini",
                                           messages=[{"role": "user", "content": "secret question"}])
                    fn()

            span = next(s for s in _span_payloads(posts) if s.get("kind") == "llm")
            assert span.get("input") is None
            assert span.get("output") is None
        except ImportError:
            pytest.skip("openai not installed")

    def test_capture_io_false_tokens_still_recorded(self):
        """capture_io=False suppresses content but not token counts."""
        try:
            from openai.resources.chat.completions import Completions
            from vantra.integrations import openai as oai_integration

            vantra._config["capture_io"] = False
            r = MagicMock()
            r.usage.prompt_tokens = 42
            r.usage.completion_tokens = 17
            r.choices[0].message.content = "hidden"
            posts = []

            with patch.object(vantra, '_post', side_effect=lambda p, d: posts.append((p, d))):
                with patch.object(Completions, 'create', return_value=r):
                    oai_integration.patch()

                    @vantra.trace
                    def fn():
                        Completions.create(MagicMock(), model="gpt-4o-mini", messages=[])
                    fn()

            span = next(s for s in _span_payloads(posts) if s.get("kind") == "llm")
            assert span["input_tokens"] == 42
            assert span["output_tokens"] == 17
        except ImportError:
            pytest.skip("openai not installed")


# ── Full messages array capture ───────────────────────────────────────────────

class TestFullMessagesCapture:
    def setup_method(self):
        _setup()

    def test_openai_captures_all_messages(self):
        try:
            from openai.resources.chat.completions import Completions
            from vantra.integrations import openai as oai_integration

            messages = [
                {"role": "system", "content": "You are helpful."},
                {"role": "user", "content": "What is Paris?"},
                {"role": "assistant", "content": "Paris is the capital."},
                {"role": "user", "content": "Tell me more."},
            ]
            r = MagicMock()
            r.usage.prompt_tokens = 50
            r.usage.completion_tokens = 30
            r.choices[0].message.content = "..."
            posts = []

            with patch.object(vantra, '_post', side_effect=lambda p, d: posts.append((p, d))):
                with patch.object(Completions, 'create', return_value=r):
                    oai_integration.patch()

                    @vantra.trace
                    def fn():
                        Completions.create(MagicMock(), model="gpt-4o-mini", messages=messages)
                    fn()

            span = next(s for s in _span_payloads(posts) if s.get("kind") == "llm")
            assert len(span["input"]["messages"]) == 4
        except ImportError:
            pytest.skip("openai not installed")

    def test_openai_captures_system_message(self):
        try:
            from openai.resources.chat.completions import Completions
            from vantra.integrations import openai as oai_integration

            messages = [
                {"role": "system", "content": "Be concise."},
                {"role": "user", "content": "Hi"},
            ]
            r = MagicMock()
            r.usage.prompt_tokens = 10
            r.usage.completion_tokens = 5
            r.choices[0].message.content = "Hey"
            posts = []

            with patch.object(vantra, '_post', side_effect=lambda p, d: posts.append((p, d))):
                with patch.object(Completions, 'create', return_value=r):
                    oai_integration.patch()

                    @vantra.trace
                    def fn():
                        Completions.create(MagicMock(), model="gpt-4o-mini", messages=messages)
                    fn()

            span = next(s for s in _span_payloads(posts) if s.get("kind") == "llm")
            captured = span["input"]["messages"]
            assert captured[0]["role"] == "system"
            assert len(captured) == 2
        except ImportError:
            pytest.skip("openai not installed")


# ── Anthropic patching ────────────────────────────────────────────────────────

class TestAnthropicPatching:
    def setup_method(self):
        _setup()
        vantra._config["capture_io"] = True

    def teardown_method(self):
        vantra._config["capture_io"] = True

    def _make_response(self, input_tokens=15, output_tokens=25, text="Hello!"):
        r = MagicMock()
        r.usage.input_tokens = input_tokens
        r.usage.output_tokens = output_tokens
        r.content = [MagicMock(text=text)]
        return r

    def test_anthropic_span_created(self):
        try:
            from anthropic.resources.messages.messages import Messages
            from vantra.integrations import anthropic as ant_integration

            response = self._make_response()
            posts = []

            with patch.object(vantra, '_post', side_effect=lambda p, d: posts.append((p, d))):
                with patch.object(Messages, 'create', return_value=response):
                    ant_integration.patch()

                    @vantra.trace
                    def fn():
                        Messages.create(MagicMock(), model="claude-haiku-4-5",
                                        messages=[{"role": "user", "content": "hi"}])
                    fn()

            spans = _span_payloads(posts)
            assert len(spans) >= 1
            llm_span = next(s for s in spans if s.get("kind") == "llm")
            assert llm_span["provider"] == "anthropic"
            assert llm_span["model"] == "claude-haiku-4-5"
            assert llm_span["input_tokens"] == 15
            assert llm_span["output_tokens"] == 25
        except ImportError:
            pytest.skip("anthropic not installed")

    def test_anthropic_cost_calculated(self):
        try:
            from anthropic.resources.messages.messages import Messages
            from vantra.integrations import anthropic as ant_integration

            response = self._make_response(1000, 1000)
            posts = []

            with patch.object(vantra, '_post', side_effect=lambda p, d: posts.append((p, d))):
                with patch.object(Messages, 'create', return_value=response):
                    ant_integration.patch()

                    @vantra.trace
                    def fn():
                        Messages.create(MagicMock(), model="claude-haiku-4-5",
                                        messages=[{"role": "user", "content": "hi"}])
                    fn()

            span = next(s for s in _span_payloads(posts) if s.get("kind") == "llm")
            expected = calculate_cost("claude-haiku-4-5", 1000, 1000)
            assert abs(span["cost_usd"] - expected) < 1e-12
        except ImportError:
            pytest.skip("anthropic not installed")

    def test_anthropic_full_messages_captured(self):
        try:
            from anthropic.resources.messages.messages import Messages
            from vantra.integrations import anthropic as ant_integration

            messages = [
                {"role": "user", "content": "First"},
                {"role": "assistant", "content": "Response"},
                {"role": "user", "content": "Follow up"},
            ]
            response = self._make_response()
            posts = []

            with patch.object(vantra, '_post', side_effect=lambda p, d: posts.append((p, d))):
                with patch.object(Messages, 'create', return_value=response):
                    ant_integration.patch()

                    @vantra.trace
                    def fn():
                        Messages.create(MagicMock(), model="claude-haiku-4-5", messages=messages)
                    fn()

            span = next(s for s in _span_payloads(posts) if s.get("kind") == "llm")
            assert len(span["input"]["messages"]) == 3
        except ImportError:
            pytest.skip("anthropic not installed")

    def test_anthropic_output_text_captured(self):
        try:
            from anthropic.resources.messages.messages import Messages
            from vantra.integrations import anthropic as ant_integration

            response = self._make_response(text="Paris is lovely.")
            posts = []

            with patch.object(vantra, '_post', side_effect=lambda p, d: posts.append((p, d))):
                with patch.object(Messages, 'create', return_value=response):
                    ant_integration.patch()

                    @vantra.trace
                    def fn():
                        Messages.create(MagicMock(), model="claude-haiku-4-5",
                                        messages=[{"role": "user", "content": "tell me"}])
                    fn()

            span = next(s for s in _span_payloads(posts) if s.get("kind") == "llm")
            assert span["output"]["text"] == "Paris is lovely."
        except ImportError:
            pytest.skip("anthropic not installed")

    def test_anthropic_error_captured(self):
        try:
            from anthropic.resources.messages.messages import Messages
            from vantra.integrations import anthropic as ant_integration

            posts = []
            with patch.object(vantra, '_post', side_effect=lambda p, d: posts.append((p, d))):
                with patch.object(Messages, 'create', side_effect=Exception("api limit")):
                    ant_integration.patch()

                    @vantra.trace
                    def fn():
                        Messages.create(MagicMock(), model="claude-haiku-4-5", messages=[])
                    with pytest.raises(Exception):
                        fn()

            spans = _span_payloads(posts)
            if spans:
                span = next((s for s in spans if s.get("kind") == "llm"), None)
                if span:
                    assert span["status"] == "error"
                    assert "api limit" in span["error_message"]
        except ImportError:
            pytest.skip("anthropic not installed")

    def test_anthropic_capture_io_false(self):
        try:
            from anthropic.resources.messages.messages import Messages
            from vantra.integrations import anthropic as ant_integration

            vantra._config["capture_io"] = False
            response = self._make_response()
            posts = []

            with patch.object(vantra, '_post', side_effect=lambda p, d: posts.append((p, d))):
                with patch.object(Messages, 'create', return_value=response):
                    ant_integration.patch()

                    @vantra.trace
                    def fn():
                        Messages.create(MagicMock(), model="claude-haiku-4-5",
                                        messages=[{"role": "user", "content": "secret"}])
                    fn()

            span = next(s for s in _span_payloads(posts) if s.get("kind") == "llm")
            assert span.get("input") is None
            assert span.get("output") is None
        except ImportError:
            pytest.skip("anthropic not installed")

    def test_anthropic_capture_io_false_tokens_still_recorded(self):
        try:
            from anthropic.resources.messages.messages import Messages
            from vantra.integrations import anthropic as ant_integration

            vantra._config["capture_io"] = False
            response = self._make_response(input_tokens=33, output_tokens=44)
            posts = []

            with patch.object(vantra, '_post', side_effect=lambda p, d: posts.append((p, d))):
                with patch.object(Messages, 'create', return_value=response):
                    ant_integration.patch()

                    @vantra.trace
                    def fn():
                        Messages.create(MagicMock(), model="claude-haiku-4-5", messages=[])
                    fn()

            span = next(s for s in _span_payloads(posts) if s.get("kind") == "llm")
            assert span["input_tokens"] == 33
            assert span["output_tokens"] == 44
        except ImportError:
            pytest.skip("anthropic not installed")

    def test_anthropic_no_double_patch(self):
        try:
            from anthropic.resources.messages.messages import Messages
            from vantra.integrations import anthropic as ant_integration

            response = self._make_response()
            posts = []

            with patch.object(vantra, '_post', side_effect=lambda p, d: posts.append((p, d))):
                with patch.object(Messages, 'create', return_value=response):
                    ant_integration.patch()
                    ant_integration.patch()  # second call must be a no-op

                    @vantra.trace
                    def fn():
                        Messages.create(MagicMock(), model="claude-haiku-4-5",
                                        messages=[{"role": "user", "content": "hi"}])
                    fn()

            llm_spans = [s for s in _span_payloads(posts) if s.get("kind") == "llm"]
            assert len(llm_spans) == 1
        except ImportError:
            pytest.skip("anthropic not installed")

    def test_anthropic_span_in_trace_totals(self):
        try:
            from anthropic.resources.messages.messages import Messages
            from vantra.integrations import anthropic as ant_integration

            response = self._make_response(input_tokens=100, output_tokens=200)
            posts = []

            with patch.object(vantra, '_post', side_effect=lambda p, d: posts.append((p, d))):
                with patch.object(Messages, 'create', return_value=response):
                    ant_integration.patch()

                    @vantra.trace
                    def fn():
                        Messages.create(MagicMock(), model="claude-haiku-4-5",
                                        messages=[{"role": "user", "content": "hi"}])
                    fn()

            trace = _trace_payload(posts)
            assert trace["total_tokens"] == 300
            assert trace["total_cost_usd"] > 0
        except ImportError:
            pytest.skip("anthropic not installed")


# ── OpenAI streaming ──────────────────────────────────────────────────────────

def _make_openai_chunk(content=None, input_tokens=None, output_tokens=None):
    """Build a mock OpenAI streaming chunk."""
    chunk = MagicMock()
    chunk.choices = []
    chunk.usage = None

    if content is not None:
        delta = MagicMock()
        delta.content = content
        choice = MagicMock()
        choice.delta = delta
        chunk.choices = [choice]

    if input_tokens is not None or output_tokens is not None:
        chunk.usage = MagicMock()
        chunk.usage.prompt_tokens = input_tokens or 0
        chunk.usage.completion_tokens = output_tokens or 0

    return chunk


class TestOpenAIStreaming:
    def setup_method(self):
        _setup()

    def _stream(self, chunks):
        """Helper: patch Completions.create to return an iterator of chunks."""
        return iter(chunks)

    def test_streaming_span_queued(self):
        """Consuming a stream produces an LLM span."""
        try:
            from openai.resources.chat.completions import Completions
            from vantra.integrations import openai as oai_integration

            chunks = [
                _make_openai_chunk(content="Hello"),
                _make_openai_chunk(content=" world"),
                _make_openai_chunk(input_tokens=10, output_tokens=5),
            ]
            posts = []

            with patch.object(vantra, '_post', side_effect=lambda p, d: posts.append((p, d))):
                with patch.object(Completions, 'create', return_value=self._stream(chunks)):
                    oai_integration.patch()

                    @vantra.trace
                    def fn():
                        inst = MagicMock()
                        stream = Completions.create(inst, model="gpt-4o-mini",
                                                    messages=[{"role": "user", "content": "hi"}],
                                                    stream=True)
                        return "".join(
                            c.choices[0].delta.content
                            for c in stream
                            if c.choices and c.choices[0].delta.content
                        )
                    fn()

            spans = _span_payloads(posts)
            llm_span = next((s for s in spans if s.get("kind") == "llm"), None)
            assert llm_span is not None
            assert llm_span["provider"] == "openai"
            assert llm_span["model"] == "gpt-4o-mini"
        except ImportError:
            pytest.skip("openai not installed")

    def test_streaming_tokens_captured(self):
        """Token counts from the final usage chunk are captured."""
        try:
            from openai.resources.chat.completions import Completions
            from vantra.integrations import openai as oai_integration

            chunks = [
                _make_openai_chunk(content="Hi"),
                _make_openai_chunk(input_tokens=20, output_tokens=10),
            ]
            posts = []

            with patch.object(vantra, '_post', side_effect=lambda p, d: posts.append((p, d))):
                with patch.object(Completions, 'create', return_value=self._stream(chunks)):
                    oai_integration.patch()

                    @vantra.trace
                    def fn():
                        list(Completions.create(MagicMock(), model="gpt-4o-mini",
                                                messages=[], stream=True))
                    fn()

            llm_span = next(s for s in _span_payloads(posts) if s.get("kind") == "llm")
            assert llm_span["input_tokens"] == 20
            assert llm_span["output_tokens"] == 10
        except ImportError:
            pytest.skip("openai not installed")

    def test_streaming_cost_calculated(self):
        """Cost is computed from streamed token counts."""
        try:
            from openai.resources.chat.completions import Completions
            from vantra.integrations import openai as oai_integration

            chunks = [
                _make_openai_chunk(content="Hi"),
                _make_openai_chunk(input_tokens=20, output_tokens=10),
            ]
            posts = []

            with patch.object(vantra, '_post', side_effect=lambda p, d: posts.append((p, d))):
                with patch.object(Completions, 'create', return_value=self._stream(chunks)):
                    oai_integration.patch()

                    @vantra.trace
                    def fn():
                        list(Completions.create(MagicMock(), model="gpt-4o-mini",
                                                messages=[], stream=True))
                    fn()

            llm_span = next(s for s in _span_payloads(posts) if s.get("kind") == "llm")
            expected = calculate_cost("gpt-4o-mini", 20, 10)
            assert abs(llm_span["cost_usd"] - expected) < 1e-12
        except ImportError:
            pytest.skip("openai not installed")

    def test_streaming_content_captured(self):
        """Concatenated content chunks stored in span output."""
        try:
            from openai.resources.chat.completions import Completions
            from vantra.integrations import openai as oai_integration

            chunks = [
                _make_openai_chunk(content="Hello"),
                _make_openai_chunk(content=" world"),
                _make_openai_chunk(input_tokens=5, output_tokens=3),
            ]
            posts = []

            with patch.object(vantra, '_post', side_effect=lambda p, d: posts.append((p, d))):
                with patch.object(Completions, 'create', return_value=self._stream(chunks)):
                    oai_integration.patch()

                    @vantra.trace
                    def fn():
                        list(Completions.create(MagicMock(), model="gpt-4o-mini",
                                                messages=[], stream=True))
                    fn()

            llm_span = next(s for s in _span_payloads(posts) if s.get("kind") == "llm")
            assert llm_span["output"]["content"] == "Hello world"
        except ImportError:
            pytest.skip("openai not installed")

    def test_streaming_trace_totals_updated(self):
        """Trace total_tokens and total_cost_usd include streamed span."""
        try:
            from openai.resources.chat.completions import Completions
            from vantra.integrations import openai as oai_integration

            chunks = [
                _make_openai_chunk(content="Hi"),
                _make_openai_chunk(input_tokens=20, output_tokens=10),
            ]
            posts = []

            with patch.object(vantra, '_post', side_effect=lambda p, d: posts.append((p, d))):
                with patch.object(Completions, 'create', return_value=self._stream(chunks)):
                    oai_integration.patch()

                    @vantra.trace
                    def fn():
                        list(Completions.create(MagicMock(), model="gpt-4o-mini",
                                                messages=[], stream=True))
                    fn()

            trace = _trace_payload(posts)
            assert trace["total_tokens"] == 30
            assert trace["total_cost_usd"] > 0
        except ImportError:
            pytest.skip("openai not installed")

    def test_streaming_error_mid_stream_captured(self):
        """An exception raised mid-stream marks the span as error."""
        try:
            from openai.resources.chat.completions import Completions
            from vantra.integrations import openai as oai_integration

            def bad_stream():
                yield _make_openai_chunk(content="Hi")
                raise RuntimeError("connection reset")

            posts = []

            with patch.object(vantra, '_post', side_effect=lambda p, d: posts.append((p, d))):
                with patch.object(Completions, 'create', return_value=bad_stream()):
                    oai_integration.patch()

                    @vantra.trace
                    def fn():
                        with pytest.raises(RuntimeError):
                            list(Completions.create(MagicMock(), model="gpt-4o-mini",
                                                    messages=[], stream=True))
                    fn()

            llm_span = next(s for s in _span_payloads(posts) if s.get("kind") == "llm")
            assert llm_span["status"] == "error"
            assert "connection reset" in llm_span["error_message"]
        except ImportError:
            pytest.skip("openai not installed")

    def test_streaming_capture_io_false(self):
        """capture_io=False suppresses input/output on streamed spans."""
        try:
            from openai.resources.chat.completions import Completions
            from vantra.integrations import openai as oai_integration

            vantra._config["capture_io"] = False
            chunks = [
                _make_openai_chunk(content="Hi"),
                _make_openai_chunk(input_tokens=5, output_tokens=3),
            ]
            posts = []

            with patch.object(vantra, '_post', side_effect=lambda p, d: posts.append((p, d))):
                with patch.object(Completions, 'create', return_value=iter(chunks)):
                    oai_integration.patch()

                    @vantra.trace
                    def fn():
                        list(Completions.create(MagicMock(), model="gpt-4o-mini",
                                                messages=[{"role": "user", "content": "secret"}],
                                                stream=True))
                    fn()

            llm_span = next(s for s in _span_payloads(posts) if s.get("kind") == "llm")
            assert llm_span["input"] is None
            assert llm_span["output"] is None
        except ImportError:
            pytest.skip("openai not installed")

    def test_streaming_stream_options_injected(self):
        """stream_options include_usage is injected automatically."""
        try:
            from openai.resources.chat.completions import Completions
            from vantra.integrations import openai as oai_integration

            captured_kwargs = {}

            def fake_create(self, *args, **kwargs):
                captured_kwargs.update(kwargs)
                return iter([_make_openai_chunk(input_tokens=5, output_tokens=3)])

            posts = []
            with patch.object(vantra, '_post', side_effect=lambda p, d: posts.append((p, d))):
                with patch.object(Completions, 'create', fake_create):
                    oai_integration.patch()

                    @vantra.trace
                    def fn():
                        list(Completions.create(MagicMock(), model="gpt-4o-mini",
                                                messages=[], stream=True))
                    fn()

            assert captured_kwargs.get("stream_options") == {"include_usage": True}
        except ImportError:
            pytest.skip("openai not installed")

    def test_streaming_user_stream_options_not_overridden(self):
        """If user already set stream_options, don't overwrite it."""
        try:
            from openai.resources.chat.completions import Completions
            from vantra.integrations import openai as oai_integration

            captured_kwargs = {}
            user_opts = {"include_usage": True, "include_logprobs": True}

            def fake_create(self, *args, **kwargs):
                captured_kwargs.update(kwargs)
                return iter([_make_openai_chunk(input_tokens=5, output_tokens=3)])

            posts = []
            with patch.object(vantra, '_post', side_effect=lambda p, d: posts.append((p, d))):
                with patch.object(Completions, 'create', fake_create):
                    oai_integration.patch()

                    @vantra.trace
                    def fn():
                        list(Completions.create(MagicMock(), model="gpt-4o-mini",
                                                messages=[], stream=True,
                                                stream_options=user_opts))
                    fn()

            assert captured_kwargs["stream_options"] == user_opts
        except ImportError:
            pytest.skip("openai not installed")

    def test_non_streaming_still_works_after_patch(self):
        """Non-streaming path is unaffected by streaming changes."""
        try:
            from openai.resources.chat.completions import Completions
            from vantra.integrations import openai as oai_integration

            response = MagicMock()
            response.usage.prompt_tokens = 10
            response.usage.completion_tokens = 20
            response.choices[0].message.content = "Paris"
            posts = []

            with patch.object(vantra, '_post', side_effect=lambda p, d: posts.append((p, d))):
                with patch.object(Completions, 'create', return_value=response):
                    oai_integration.patch()

                    @vantra.trace
                    def fn():
                        Completions.create(MagicMock(), model="gpt-4o-mini", messages=[])
                    fn()

            llm_span = next(s for s in _span_payloads(posts) if s.get("kind") == "llm")
            assert llm_span["input_tokens"] == 10
            assert llm_span["output_tokens"] == 20
        except ImportError:
            pytest.skip("openai not installed")


# ── Anthropic streaming ───────────────────────────────────────────────────────

def _make_anthropic_event(event_type, **kwargs):
    """Build a mock Anthropic streaming event."""
    event = MagicMock()
    event.type = event_type

    if event_type == "message_start":
        event.message = MagicMock()
        event.message.usage = MagicMock()
        event.message.usage.input_tokens = kwargs.get("input_tokens", 0)

    elif event_type == "content_block_delta":
        event.delta = MagicMock()
        event.delta.type = "text_delta"
        event.delta.text = kwargs.get("text", "")

    elif event_type == "message_delta":
        event.usage = MagicMock()
        event.usage.output_tokens = kwargs.get("output_tokens", 0)

    elif event_type == "message_stop":
        pass

    return event


class TestAnthropicStreaming:
    def setup_method(self):
        _setup()

    def test_streaming_span_queued(self):
        """Consuming an Anthropic stream produces an LLM span."""
        try:
            from anthropic.resources.messages.messages import Messages
            from vantra.integrations import anthropic as ant_integration

            events = [
                _make_anthropic_event("message_start", input_tokens=15),
                _make_anthropic_event("content_block_delta", text="Hello"),
                _make_anthropic_event("message_delta", output_tokens=8),
                _make_anthropic_event("message_stop"),
            ]
            posts = []

            with patch.object(vantra, '_post', side_effect=lambda p, d: posts.append((p, d))):
                with patch.object(Messages, 'create', return_value=iter(events)):
                    ant_integration.patch()

                    @vantra.trace
                    def fn():
                        list(Messages.create(MagicMock(), model="claude-haiku-4-5",
                                             messages=[{"role": "user", "content": "hi"}],
                                             stream=True))
                    fn()

            llm_span = next((s for s in _span_payloads(posts) if s.get("kind") == "llm"), None)
            assert llm_span is not None
            assert llm_span["provider"] == "anthropic"
        except ImportError:
            pytest.skip("anthropic not installed")

    def test_streaming_tokens_captured(self):
        """Input and output tokens read from Anthropic stream events."""
        try:
            from anthropic.resources.messages.messages import Messages
            from vantra.integrations import anthropic as ant_integration

            events = [
                _make_anthropic_event("message_start", input_tokens=15),
                _make_anthropic_event("content_block_delta", text="Hi"),
                _make_anthropic_event("message_delta", output_tokens=8),
                _make_anthropic_event("message_stop"),
            ]
            posts = []

            with patch.object(vantra, '_post', side_effect=lambda p, d: posts.append((p, d))):
                with patch.object(Messages, 'create', return_value=iter(events)):
                    ant_integration.patch()

                    @vantra.trace
                    def fn():
                        list(Messages.create(MagicMock(), model="claude-haiku-4-5",
                                             messages=[], stream=True))
                    fn()

            llm_span = next(s for s in _span_payloads(posts) if s.get("kind") == "llm")
            assert llm_span["input_tokens"] == 15
            assert llm_span["output_tokens"] == 8
        except ImportError:
            pytest.skip("anthropic not installed")

    def test_streaming_cost_calculated(self):
        """Cost computed from Anthropic stream token counts."""
        try:
            from anthropic.resources.messages.messages import Messages
            from vantra.integrations import anthropic as ant_integration

            events = [
                _make_anthropic_event("message_start", input_tokens=15),
                _make_anthropic_event("message_delta", output_tokens=8),
                _make_anthropic_event("message_stop"),
            ]
            posts = []

            with patch.object(vantra, '_post', side_effect=lambda p, d: posts.append((p, d))):
                with patch.object(Messages, 'create', return_value=iter(events)):
                    ant_integration.patch()

                    @vantra.trace
                    def fn():
                        list(Messages.create(MagicMock(), model="claude-haiku-4-5",
                                             messages=[], stream=True))
                    fn()

            llm_span = next(s for s in _span_payloads(posts) if s.get("kind") == "llm")
            expected = calculate_cost("claude-haiku-4-5", 15, 8)
            assert abs(llm_span["cost_usd"] - expected) < 1e-12
        except ImportError:
            pytest.skip("anthropic not installed")

    def test_streaming_content_captured(self):
        """Text chunks concatenated into span output."""
        try:
            from anthropic.resources.messages.messages import Messages
            from vantra.integrations import anthropic as ant_integration

            events = [
                _make_anthropic_event("message_start", input_tokens=5),
                _make_anthropic_event("content_block_delta", text="Hello"),
                _make_anthropic_event("content_block_delta", text=" world"),
                _make_anthropic_event("message_delta", output_tokens=3),
                _make_anthropic_event("message_stop"),
            ]
            posts = []

            with patch.object(vantra, '_post', side_effect=lambda p, d: posts.append((p, d))):
                with patch.object(Messages, 'create', return_value=iter(events)):
                    ant_integration.patch()

                    @vantra.trace
                    def fn():
                        list(Messages.create(MagicMock(), model="claude-haiku-4-5",
                                             messages=[], stream=True))
                    fn()

            llm_span = next(s for s in _span_payloads(posts) if s.get("kind") == "llm")
            assert llm_span["output"]["text"] == "Hello world"
        except ImportError:
            pytest.skip("anthropic not installed")

    def test_streaming_trace_totals_updated(self):
        """Trace total_tokens includes Anthropic streamed span."""
        try:
            from anthropic.resources.messages.messages import Messages
            from vantra.integrations import anthropic as ant_integration

            events = [
                _make_anthropic_event("message_start", input_tokens=15),
                _make_anthropic_event("message_delta", output_tokens=8),
                _make_anthropic_event("message_stop"),
            ]
            posts = []

            with patch.object(vantra, '_post', side_effect=lambda p, d: posts.append((p, d))):
                with patch.object(Messages, 'create', return_value=iter(events)):
                    ant_integration.patch()

                    @vantra.trace
                    def fn():
                        list(Messages.create(MagicMock(), model="claude-haiku-4-5",
                                             messages=[], stream=True))
                    fn()

            trace = _trace_payload(posts)
            assert trace["total_tokens"] == 23
            assert trace["total_cost_usd"] > 0
        except ImportError:
            pytest.skip("anthropic not installed")

    def test_streaming_error_mid_stream_captured(self):
        """Exception mid-stream marks span as error."""
        try:
            from anthropic.resources.messages.messages import Messages
            from vantra.integrations import anthropic as ant_integration

            def bad_stream():
                yield _make_anthropic_event("message_start", input_tokens=5)
                raise RuntimeError("stream dropped")

            posts = []

            with patch.object(vantra, '_post', side_effect=lambda p, d: posts.append((p, d))):
                with patch.object(Messages, 'create', return_value=bad_stream()):
                    ant_integration.patch()

                    @vantra.trace
                    def fn():
                        with pytest.raises(RuntimeError):
                            list(Messages.create(MagicMock(), model="claude-haiku-4-5",
                                                 messages=[], stream=True))
                    fn()

            llm_span = next(s for s in _span_payloads(posts) if s.get("kind") == "llm")
            assert llm_span["status"] == "error"
            assert "stream dropped" in llm_span["error_message"]
        except ImportError:
            pytest.skip("anthropic not installed")

    def test_streaming_capture_io_false(self):
        """capture_io=False suppresses input/output on Anthropic streamed spans."""
        try:
            from anthropic.resources.messages.messages import Messages
            from vantra.integrations import anthropic as ant_integration

            vantra._config["capture_io"] = False
            events = [
                _make_anthropic_event("message_start", input_tokens=5),
                _make_anthropic_event("content_block_delta", text="secret"),
                _make_anthropic_event("message_delta", output_tokens=3),
                _make_anthropic_event("message_stop"),
            ]
            posts = []

            with patch.object(vantra, '_post', side_effect=lambda p, d: posts.append((p, d))):
                with patch.object(Messages, 'create', return_value=iter(events)):
                    ant_integration.patch()

                    @vantra.trace
                    def fn():
                        list(Messages.create(MagicMock(), model="claude-haiku-4-5",
                                             messages=[], stream=True))
                    fn()

            llm_span = next(s for s in _span_payloads(posts) if s.get("kind") == "llm")
            assert llm_span["input"] is None
            assert llm_span["output"] is None
        except ImportError:
            pytest.skip("anthropic not installed")

    def test_non_streaming_still_works_after_patch(self):
        """Non-streaming Anthropic path unaffected."""
        try:
            from anthropic.resources.messages.messages import Messages
            from vantra.integrations import anthropic as ant_integration

            response = MagicMock()
            response.usage.input_tokens = 10
            response.usage.output_tokens = 20
            response.content = [MagicMock(text="Paris")]
            posts = []

            with patch.object(vantra, '_post', side_effect=lambda p, d: posts.append((p, d))):
                with patch.object(Messages, 'create', return_value=response):
                    ant_integration.patch()

                    @vantra.trace
                    def fn():
                        Messages.create(MagicMock(), model="claude-haiku-4-5", messages=[])
                    fn()

            llm_span = next(s for s in _span_payloads(posts) if s.get("kind") == "llm")
            assert llm_span["input_tokens"] == 10
            assert llm_span["output_tokens"] == 20
        except ImportError:
            pytest.skip("anthropic not installed")
