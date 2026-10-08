from app.execution.api import event_observability_kwargs, executor_observability


def test_sdk_usage_fills_missing_executor_metrics_and_public_event_counters():
    payload = {"sdk_usage": {"input_tokens": 3, "cache_read_input_tokens": 2, "output_tokens": 4, "cost_usd": "0.015"}}

    observability = executor_observability(payload, latency_ms=12)

    assert observability == {
        "latency_ms": 12,
        "token_counts": {"input": 5, "output": 4, "total": 9},
        "cost": {"estimated_cost_minor": 2},
    }
    assert event_observability_kwargs(observability, payload) == {
        "latency_ms": 12,
        "input_token_count": 5,
        "output_token_count": 4,
        "total_token_count": 9,
        "estimated_cost_minor": 2,
    }


def test_no_executor_metrics_omits_event_counters():
    payload = {}
    assert event_observability_kwargs(executor_observability(payload, latency_ms=0), payload) == {}
