from app.execution.api import event_observability_kwargs, executor_observability
from claude_agent_sdk import ResultMessage
import pytest

from app.execution.infrastructure.harness.claude.result_usage import ClaudeResultUsage


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


def _sdk_result(*, cost=None, usage=None, models=None, session_id="session", subtype="success"):
    return ResultMessage(
        subtype=subtype, duration_ms=1, duration_api_ms=1,
        is_error=subtype != "success", num_turns=1, session_id=session_id,
        total_cost_usd=cost, usage=usage, model_usage=models,
    )


def _model_usage(*, multiplier=1):
    return {"private-model": {
        "inputTokens": 11 * multiplier, "outputTokens": 13 * multiplier,
        "cacheReadInputTokens": 3 * multiplier, "cacheCreationInputTokens": 5 * multiplier,
        "costUSD": 0.125 * multiplier, "contextWindow": 100000,
        "maxOutputTokens": 4096, "private_extra": "must not copy",
    }}


def _metrics(usage):
    return executor_observability({"sdk_usage": usage}, latency_ms=1)


def test_real_sdk_cumulative_cost_replaces_snapshot_without_adding_turns():
    observation = ClaudeResultUsage(session_id="session", prompt="start")
    observation.observe_result(_sdk_result(cost=0.125, usage={"input_tokens": 5}, models=_model_usage()))
    observation.observe_result(_sdk_result(cost=0.25, usage={"input_tokens": 7}, models=_model_usage(multiplier=2)))
    value = observation.project({"input_tokens": 12}, terminal_result=True)
    assert value["total_cost_usd"] == 0.25
    assert value["model_usage"]["private-model"]["inputTokens"] == 22
    assert "private_extra" not in value["model_usage"]["private-model"]
    assert value["cost_coverage"] == "complete"
    assert _metrics(value)["cost"] == {"estimated_cost_minor": 25}
    assert _metrics(value)["token_counts"] == {"input": 12, "output": 0, "total": 12}
    # An identical latest cumulative snapshot does not turn 25 cents into 50.
    observation.observe_result(_sdk_result(cost=0.25, usage={"input_tokens": 7}, models=_model_usage(multiplier=2)))
    assert observation.project({"input_tokens": 12}, terminal_result=True) == value


def test_real_sdk_model_only_fallback_projects_latest_call_snapshot_once():
    observation = ClaudeResultUsage(session_id="session", prompt="start")
    observation.observe_result(_sdk_result(cost=0.125, models=_model_usage()))
    observation.observe_result(_sdk_result(cost=0.25, models=_model_usage(multiplier=2)))
    value = observation.project({}, terminal_result=True)
    assert _metrics(value)["token_counts"] == {"input": 38, "output": 26, "total": 64}
    assert _metrics(value)["cost"] == {"estimated_cost_minor": 25}


@pytest.mark.parametrize("cost", [None, True, False, float("nan"), float("inf"), -1, "0.25", 21474836.48])
def test_missing_or_invalid_latest_cost_keeps_partial_prior_observation(cost):
    observation = ClaudeResultUsage(session_id="session", prompt="start")
    observation.observe_result(_sdk_result(cost=0.25, usage={"input_tokens": 4}))
    observation.observe_result(_sdk_result(cost=cost, usage={"input_tokens": 6}))
    value = observation.project({"input_tokens": 10}, terminal_result=True)
    assert value["total_cost_usd"] == 0.25
    assert value["cost_coverage"] == "partial"
    assert _metrics(value)["cost"] == {"estimated_cost_minor": None}
    assert _metrics(value)["token_counts"]["input"] == 10


def test_real_zero_unknown_and_no_terminal_remain_distinct_in_private_carrier():
    unknown = ClaudeResultUsage(session_id="session", prompt="start")
    unknown.observe_result(_sdk_result())
    assert unknown.project({}, terminal_result=True) == {"cost_coverage": "unavailable"}
    known = ClaudeResultUsage(session_id="session", prompt="start")
    known.observe_result(_sdk_result(cost=0.0))
    assert known.project({}, terminal_result=True) == {"total_cost_usd": 0.0, "cost_coverage": "complete"}
    known.observe_result(_sdk_result(cost=0.125))
    assert known.project({}, terminal_result=False) == {"total_cost_usd": 0.125, "cost_coverage": "partial"}


def test_crash_zero_preserves_prior_cumulative_evidence_without_complete_cost():
    observation = ClaudeResultUsage(session_id="session", prompt="start")
    observation.observe_result(_sdk_result(cost=0.25, models=_model_usage()))
    observation.observe_result(_sdk_result(cost=0.0, models=_model_usage(multiplier=0), subtype="error_during_execution"))
    value = observation.project({"input_tokens": 4}, terminal_result=True)
    assert value["total_cost_usd"] == 0.25
    assert value["model_usage"]["private-model"]["inputTokens"] == 11
    assert value["cost_coverage"] == "partial"
    assert _metrics(value)["cost"] == {"estimated_cost_minor": None}


def test_noncrash_error_result_preserves_complete_reported_estimate():
    observation = ClaudeResultUsage(session_id="session", prompt="start")
    observation.observe_result(_sdk_result(cost=0.125, subtype="error_max_turns"))
    assert _metrics(observation.project({}, terminal_result=True))["cost"] == {"estimated_cost_minor": 13}


@pytest.mark.parametrize("prompt", ["/clear", " /reset\n", "/new with instructions"])
@pytest.mark.parametrize("initial", [True, False])
def test_initial_and_supplementary_reset_commands_never_claim_complete_run_cost(prompt, initial):
    observation = ClaudeResultUsage(session_id="session", prompt=prompt if initial else "start")
    observation.observe_result(_sdk_result(cost=0.25))
    if not initial:
        observation.observe_input(prompt)
    observation.observe_result(_sdk_result(cost=0.5))
    value = observation.project({}, terminal_result=True)
    assert value["cost_coverage"] == "partial"
    assert _metrics(value)["cost"] == {"estimated_cost_minor": None}


@pytest.mark.parametrize("second", [
    _sdk_result(cost=0.1),
    _sdk_result(cost=0.5, session_id="new-session"),
    _sdk_result(cost=0.5, models=_model_usage(multiplier=0)),
])
def test_cumulative_regression_or_session_change_has_no_invented_total(second):
    observation = ClaudeResultUsage(session_id="session", prompt="start")
    observation.observe_result(_sdk_result(cost=0.25, models=_model_usage()))
    observation.observe_result(second)
    assert observation.project({}, terminal_result=True)["cost_coverage"] == "partial"


@pytest.mark.parametrize("value", [True, -1, float("nan"), float("inf"), 2**31])
def test_invalid_model_counter_cannot_erase_existing_main_tokens(value):
    observation = ClaudeResultUsage(session_id="session", prompt="start")
    models = _model_usage()
    models["private-model"]["inputTokens"] = value
    observation.observe_result(_sdk_result(cost=0.25, usage={"input_tokens": 9}, models=models))
    carrier = observation.project({"input_tokens": 9}, terminal_result=True)
    assert "model_usage" not in carrier
    assert _metrics(carrier)["token_counts"]["input"] == 9
    assert _metrics(carrier)["cost"] == {"estimated_cost_minor": 25}


@pytest.mark.parametrize("cost", ["NaN", "-NaN", "Infinity", "1e999999999", "1e-999999999", float("nan"), float("inf"), True, -1, 21474836.48])
def test_cost_projection_rejects_nonfinite_or_out_of_range_legacy_value_without_raising(cost):
    assert _metrics({"total_cost_usd": cost})["cost"] == {"estimated_cost_minor": 0}


@pytest.mark.parametrize("cost,expected", [("21474836.474", 2**31 - 1), ("21474836.475", 0)])
def test_legacy_cost_upper_bound_retains_half_up_rounding(cost, expected):
    assert _metrics({"total_cost_usd": cost})["cost"] == {"estimated_cost_minor": expected}


@pytest.mark.parametrize("coverage", ["partial", "unavailable"])
def test_incomplete_cost_is_unknown_even_with_tokens_and_stale_top_level_amount(coverage):
    payload = {"estimated_cost_minor": 99, "sdk_usage": {
        "cost_coverage": coverage, "total_cost_usd": 0.25, "input_tokens": 3,
    }}
    observed = executor_observability(payload, latency_ms=7)
    assert observed["cost"] == {"estimated_cost_minor": None}
    assert observed["token_counts"] == {"input": 3, "output": 0, "total": 3}
    assert event_observability_kwargs(observed, payload)["input_token_count"] == 3


def test_marked_complete_zero_wins_over_stale_legacy_top_level_amount():
    observed = executor_observability({"estimated_cost_minor": 97, "sdk_usage": {
        "cost_coverage": "complete", "total_cost_usd": 0.0,
    }}, latency_ms=1)
    assert observed["cost"] == {"estimated_cost_minor": 0}
    assert observed["runtime_private_payload"] == {"sdk_usage": {"cost_coverage": "complete"}}


@pytest.mark.parametrize("value", [None, True, "NaN", float("inf"), -1, 21474836.48])
def test_malformed_marked_complete_usd_never_becomes_known_zero(value):
    observed = executor_observability({"sdk_usage": {
        "cost_coverage": "complete", "total_cost_usd": value,
    }}, latency_ms=1)
    assert observed["cost"] == {"estimated_cost_minor": None}
