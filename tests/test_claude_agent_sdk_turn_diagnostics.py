import asyncio
import json
from pathlib import Path
import sys
import types
from typing import Any

import pytest

from tests.support.claude_sdk import native_client_factory

from app.executors.claude_agent_sdk_runner import (
    project_sdk_turn_diagnostics,
    run_claude_agent_sdk,
)
from app.runs.domain.diagnostics import sanitize_runtime_diagnostics
from app.sandbox.domain.runtime_diagnostics import (
    SDK_RUNTIME_DIAGNOSTICS_MAX_BYTES,
    SDK_RUNTIME_DIAGNOSTICS_SCHEMA_VERSION,
    exception_chain_from_error,
    normalize_sdk_runtime_diagnostics,
)


def test_runtime_diagnostics_fit_keeps_latest_evidence_within_result_budget():
    payload = {
        "schema_version": SDK_RUNTIME_DIAGNOSTICS_SCHEMA_VERSION,
        "error_code": "claude_agent_sdk_runtime_error",
        "failure_source": "sdk_exception",
        "failure_stage": "model_wait",
        "sdk": {
            "errors": "s" * 4000,
            "exception_message": "m" * 8192,
            "exception_traceback": "t" * 8192,
        },
        "tool_lifecycles": [
            {
                "tool_name": "t" * 128,
                "invocation_id": f"lifecycle-{index}-" + "i" * 110,
                "state": "failed",
            }
            for index in range(128)
        ],
        "tool_calls": [
            {
                "tool_name": "Bash",
                "invocation_id": f"call-{index}",
                "tool_input": "i" * 4000,
                "failure": "f" * 4000,
            }
            for index in range(8)
        ],
        "tool_policy_denials": [
            {
                "tool_name": "Bash",
                "invocation_id": f"denial-{index}",
                "reason": "r" * 1024,
                "tool_input": "i" * 4000,
            }
            for index in range(8)
        ],
    }

    fitted = normalize_sdk_runtime_diagnostics(payload)

    assert len(json.dumps(fitted, separators=(",", ":")).encode()) <= (
        SDK_RUNTIME_DIAGNOSTICS_MAX_BYTES
    )
    assert fitted["tool_lifecycles"][-1]["invocation_id"].startswith(
        "lifecycle-127-"
    )
    assert fitted["tool_calls"][-1]["invocation_id"] == "call-7"
    assert fitted["tool_policy_denials"][-1]["invocation_id"] == "denial-7"
    assert "truncated" not in fitted
    assert any(
        loss["reason"] == "truncated"
        for loss in fitted["normalization_losses"]
    )


def test_runtime_diagnostics_fit_handles_json_escaping_and_invalid_unicode():
    fitted = normalize_sdk_runtime_diagnostics(
        {
            "schema_version": SDK_RUNTIME_DIAGNOSTICS_SCHEMA_VERSION,
            "error_code": "claude_agent_sdk_runtime_error",
            "failure_source": "sdk_exception",
            "failure_stage": "model_wait",
            "sdk": {
                "errors": "\0" * 4000,
                "exception_message": "\ud800" + "\0" * 8192,
                "exception_traceback": "\0" * 8192,
            },
            "tool_lifecycles": [
                {
                    "tool_name": "Bash",
                    "invocation_id": "call-1",
                    "state": "failed",
                }
            ],
            "tool_calls": [
                {
                    "tool_name": "Bash",
                    "invocation_id": "call-1",
                    "tool_input": {"command": "\ud800"},
                    "failure": "\0" * 4000,
                }
            ],
            "tool_policy_denials": [
                {
                    "tool_name": "Bash",
                    "invocation_id": "call-1",
                    "reason": "\0" * 1024,
                    "tool_input": "\0" * 4000,
                }
            ],
        }
    )

    encoded = json.dumps(fitted, ensure_ascii=False, separators=(",", ":")).encode(
        "utf-8"
    )
    assert len(encoded) <= SDK_RUNTIME_DIAGNOSTICS_MAX_BYTES
    assert fitted["tool_calls"][0]["tool_input"] == {"command": "?"}


def test_runtime_diagnostics_explains_rejected_schema_without_echoing_it():
    rejected = normalize_sdk_runtime_diagnostics(
        {
            "schema_version": "private-future-schema",
            "error_code": "private_error",
            "sdk": {"exception_message": "token=private-token"},
        }
    )

    assert rejected["error_code"] == "runtime_diagnostics_rejected"
    assert rejected["normalization_losses"] == [
        {"field": "schema_version", "reason": "unsupported_schema"}
    ]
    assert "private-future-schema" not in str(rejected)
    assert "private-token" not in str(rejected)

    invalid_field = normalize_sdk_runtime_diagnostics(
        {
            "schema_version": SDK_RUNTIME_DIAGNOSTICS_SCHEMA_VERSION,
            "error_code": "executor_failed",
            "failure_source": {"token": "private-token"},
        }
    )
    assert invalid_field["failure_source"] == ""
    assert "private-token" not in str(invalid_field)
    assert {
        "field": "failure_source",
        "reason": "invalid_field",
    } in invalid_field["normalization_losses"]


def test_runtime_diagnostics_preserves_traceback_tail_and_loss_metadata_idempotently():
    terminal_cause = "SYNTHETIC_TERMINAL_CAUSE"
    fitted = normalize_sdk_runtime_diagnostics(
        {
            "schema_version": SDK_RUNTIME_DIAGNOSTICS_SCHEMA_VERSION,
            "error_code": "claude_agent_sdk_runtime_error",
            "failure_source": "sdk_exception",
            "failure_stage": "model_wait",
            "sdk": {
                "exception_type": "TimeoutError",
                "exception_traceback": "synthetic frame\n" * 1_200
                + terminal_cause,
            },
        }
    )

    assert fitted["sdk"]["exception_traceback"].endswith(terminal_cause)
    assert "... [truncated] ..." in fitted["sdk"]["exception_traceback"]
    assert any(
        loss["field"] == "sdk.exception_traceback"
        and loss["reason"] == "truncated"
        for loss in fitted["normalization_losses"]
    )
    assert normalize_sdk_runtime_diagnostics(fitted) == fitted


def test_runtime_diagnostics_upgrades_retired_runner_slots_to_failure_observations():
    fitted = normalize_sdk_runtime_diagnostics(
        {
            "schema_version": SDK_RUNTIME_DIAGNOSTICS_SCHEMA_VERSION,
            "error_code": "executor_failed",
            "failure_source": "sandbox_terminal_normalization",
            "failure_stage": "sandbox_submission",
            "runner_error_code": "claude_agent_sdk_timeout",
            "runner_failure_source": "sdk_exception",
            "sdk": {"exception_type": "TimeoutError"},
        }
    )

    assert fitted["error_code"] == "claude_agent_sdk_timeout"
    assert fitted["failure_source"] == "sdk_exception"
    assert fitted["failure_observations"] == [
        {
            "error_code": "claude_agent_sdk_timeout",
            "failure_source": "sdk_exception",
            "failure_stage": "",
        },
        {
            "error_code": "executor_failed",
            "failure_source": "sandbox_terminal_normalization",
            "failure_stage": "sandbox_submission",
        },
    ]
    assert "runner_error_code" not in fitted
    assert "runner_failure_source" not in fitted


def test_runtime_diagnostics_preserves_bounded_exception_cause_chain():
    cause = RuntimeError("root cause")
    wrapper = ValueError("wrapper failure")
    wrapper.__cause__ = cause

    normalized = normalize_sdk_runtime_diagnostics(
        {
            "schema_version": SDK_RUNTIME_DIAGNOSTICS_SCHEMA_VERSION,
            "error_code": "executor_failure",
            "failure_source": "worker_executor",
            "failure_stage": "executor",
            "sdk": {"exception_chain": exception_chain_from_error(wrapper)},
        }
    )

    chain = normalized["sdk"]["exception_chain"]
    assert [item["type"] for item in chain] == ["ValueError", "RuntimeError"]
    assert chain[0]["relation"] == "cause"
    assert chain[-1]["message"] == "root cause"
    assert sanitize_runtime_diagnostics(normalized)["sdk"]["exception_chain"] == chain
    assert normalize_sdk_runtime_diagnostics(normalized) == normalized


def test_runtime_diagnostics_names_exception_chain_cycle_and_depth_losses():
    first = RuntimeError("first")
    second = ValueError("second")
    first.__cause__ = second
    second.__cause__ = first
    cycle_losses: list[dict[str, object]] = []

    cycle = normalize_sdk_runtime_diagnostics(
        {
            "schema_version": SDK_RUNTIME_DIAGNOSTICS_SCHEMA_VERSION,
            "error_code": "executor_failure",
            "failure_source": "sdk_exception",
            "failure_stage": "model_wait",
            "sdk": {
                "exception_chain": exception_chain_from_error(
                    first,
                    losses=cycle_losses,
                )
            },
            "normalization_losses": cycle_losses,
        }
    )

    assert {loss["reason"] for loss in cycle["normalization_losses"]} == {"cycle"}

    current: BaseException = RuntimeError("root")
    for index in range(10):
        wrapper = RuntimeError(f"wrapper-{index}")
        wrapper.__cause__ = current
        current = wrapper
    deep = normalize_sdk_runtime_diagnostics(
        {
            "schema_version": SDK_RUNTIME_DIAGNOSTICS_SCHEMA_VERSION,
            "error_code": "executor_failure",
            "failure_source": "sdk_exception",
            "failure_stage": "model_wait",
            "sdk": {"exception_chain": exception_chain_from_error(current)},
        }
    )

    assert len(deep["sdk"]["exception_chain"]) == 8
    assert deep["sdk"]["exception_chain"][0]["message"] == "wrapper-9"
    assert deep["sdk"]["exception_chain"][-1]["message"] == "root"
    assert any(
        loss["field"] == "sdk.exception_chain" and loss["reason"] == "truncated"
        for loss in deep["normalization_losses"]
    )


def test_runtime_diagnostics_truncation_keeps_root_earliest_and_latest_failures():
    fitted = normalize_sdk_runtime_diagnostics(
        {
            "schema_version": SDK_RUNTIME_DIAGNOSTICS_SCHEMA_VERSION,
            "error_code": "wrapper_failure",
            "failure_source": "executor_wrapper",
            "failure_stage": "terminalization",
            "failure_observations": [
                {
                    "error_code": f"cause_{index}",
                    "failure_source": "sdk_exception",
                    "failure_stage": "model_wait",
                }
                for index in range(10)
            ],
        }
    )

    assert [item["error_code"] for item in fitted["failure_observations"]] == [
        "wrapper_failure",
        "cause_0",
        "cause_4",
        "cause_5",
        "cause_6",
        "cause_7",
        "cause_8",
        "cause_9",
    ]
    assert any(
        item["field"] == "failure_observations" and item["reason"] == "truncated"
        for item in fitted["normalization_losses"]
    )


def _settings(*, timeout_seconds: float = 5.0):
    return types.SimpleNamespace(
        claude_agent_sdk_enabled=True,
        claude_agent_sdk_skills="",
        claude_agent_permission_mode="dontAsk",
        claude_agent_allowed_tools="Read,Glob,LS",
        claude_agent_disallowed_tools="",
        claude_agent_model="model-a",
        anthropic_model="",
        claude_agent_sdk_timeout_seconds=timeout_seconds,
        claude_agent_sdk_max_turns=8,
        claude_agent_sdk_max_thinking_tokens=1024,
        claude_agent_sdk_effort="high",
        anthropic_api_key=None,
        anthropic_base_url=None,
        anthropic_auth_token=None,
        openai_api_key=None,
    )


def _install_sdk(monkeypatch, query):
    class TextBlock:
        def __init__(self, text: str):
            self.text = text

    class AssistantMessage:
        def __init__(self, content: list[Any]):
            self.content = content

    class ResultMessage:
        def __init__(
            self,
            *,
            is_error: bool = False,
            subtype: str = "success",
            errors: list[str] | None = None,
            stop_reason: str | None = "end_turn",
            num_turns: int = 1,
        ):
            self.session_id = "sdk-session"
            self.usage = {"input_tokens": 1}
            self.model_usage = {}
            self.result = "done"
            self.is_error = is_error
            self.subtype = subtype
            self.errors = list(errors or [])
            self.stop_reason = stop_reason
            self.num_turns = num_turns
            self.permission_denials = []

    class HookMatcher:
        def __init__(self, matcher=None, hooks=None, timeout=None):
            self.matcher = matcher
            self.hooks = hooks or []
            self.timeout = timeout

    class ClaudeAgentOptions:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    fake_sdk = types.SimpleNamespace(
        AssistantMessage=AssistantMessage,
        ClaudeAgentOptions=ClaudeAgentOptions,
        HookMatcher=HookMatcher,
        ResultMessage=ResultMessage,
        TextBlock=TextBlock,
        query=query,
        ClaudeSDKClient=native_client_factory(query),
    )
    monkeypatch.setitem(sys.modules, "claude_agent_sdk", fake_sdk)
    return fake_sdk


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("subtype", "errors", "stop_reason"),
    [
        ("error_max_turns", [], None),
        ("error_during_execution", ["Reached maximum number of turns (8)"], None),
        ("error_during_execution", [], "max_turns"),
    ],
)
async def test_sdk_turn_limit_variants_share_one_actionable_public_diagnostic(
    monkeypatch,
    tmp_path: Path,
    subtype,
    errors,
    stop_reason,
):
    sdk_types: dict[str, Any] = {}

    async def query(prompt, options):
        yield sdk_types["ResultMessage"](
            is_error=True,
            subtype=subtype,
            errors=errors,
            stop_reason=stop_reason,
            num_turns=8,
        )

    sdk = _install_sdk(monkeypatch, query)
    sdk_types["ResultMessage"] = sdk.ResultMessage
    monkeypatch.setattr(
        "app.executors.claude_agent_sdk_runner.get_settings",
        _settings,
    )

    result = await run_claude_agent_sdk(
        prompt="bounded request",
        cwd=tmp_path,
        skill_id="general-chat",
    )

    assert result.error == "claude_agent_sdk_turn_limit_exceeded"
    assert result.turn_diagnostics == {
        "schema_version": "ai-platform.sdk-turn-diagnostics.v1",
        "terminal_class": "max_turn_exhausted",
        "error_code": "claude_agent_sdk_turn_limit_exceeded",
        "action": "continue_or_narrow_request",
        "retryable": True,
        "counters": {
            "max_turns": 8,
            "turns_observed": 8,
            "assistant_messages": 0,
            "text_blocks": 0,
            "result_messages": 1,
            "tool_admission_denials": 0,
            "tool_policy_denials": 0,
            "tool_lifecycle_denials": 0,
            "skill_invocations": 0,
            "public_projection_omissions": 0,
        },
        "last_public_stage": "runtime",
        "selected_skill": None,
        "used_skills": [],
        "tool_policy_denials_detail": [],
    }


@pytest.mark.asyncio
async def test_sdk_timeout_and_missing_terminal_are_distinct(monkeypatch, tmp_path: Path):
    async def timeout_query(prompt, options):
        await asyncio.sleep(1)
        if False:
            yield None

    _install_sdk(monkeypatch, timeout_query)
    monkeypatch.setattr(
        "app.executors.claude_agent_sdk_runner.get_settings",
        lambda: _settings(timeout_seconds=0.01),
    )
    timed_out = await run_claude_agent_sdk(
        prompt="bounded request",
        cwd=tmp_path,
        skill_id="general-chat",
    )

    async def empty_query(prompt, options):
        if False:
            yield None

    _install_sdk(monkeypatch, empty_query)
    monkeypatch.setattr(
        "app.executors.claude_agent_sdk_runner.get_settings",
        _settings,
    )
    missing = await run_claude_agent_sdk(
        prompt="bounded request",
        cwd=tmp_path,
        skill_id="general-chat",
    )

    assert timed_out.error == "claude_agent_sdk_timeout"
    assert timed_out.turn_diagnostics["terminal_class"] == "timeout"
    assert missing.error == "claude_agent_sdk_missing_structured_terminal"
    assert missing.turn_diagnostics["terminal_class"] == "missing_terminal"


@pytest.mark.asyncio
async def test_authorized_skill_is_optional_and_policy_admission_remains_distinct(
    monkeypatch,
    tmp_path: Path,
):
    sdk_types: dict[str, Any] = {}
    captured: dict[str, Any] = {}

    async def success_query(prompt, options):
        captured["prompt_messages"] = [message async for message in prompt]
        captured["options"] = options.kwargs
        yield sdk_types["ResultMessage"]()

    sdk = _install_sdk(monkeypatch, success_query)
    sdk_types["ResultMessage"] = sdk.ResultMessage
    monkeypatch.setattr(
        "app.executors.claude_agent_sdk_runner.get_settings",
        _settings,
    )
    metadata = {
        "review-skill": {
            "name": "Document review",
            "version": "version-a",
            "availability": "available",
        }
    }
    not_invoked = await run_claude_agent_sdk(
        prompt="review",
        cwd=tmp_path,
        skill_id="review-skill",
        skills=["review-skill"],
        public_skill_metadata=metadata,
    )
    not_admitted = await run_claude_agent_sdk(
        prompt="review",
        cwd=tmp_path,
        skill_id="review-skill",
        skills=["review-skill"],
        tool_policy_subjects=[
            {
                "identity": "Skill",
                "declared_identities": ["Skill"],
                "allowed_skill_names": [],
            }
        ],
        execution_policy="sandbox_brokered",
        public_skill_metadata=metadata,
    )

    assert not_invoked.error is None
    assert not_invoked.turn_diagnostics["terminal_class"] == "completed"
    assert not_invoked.turn_diagnostics["selected_skill"] == metadata["review-skill"]
    assert not_invoked.used_skills == []
    assert captured["options"]["skills"] == ["review-skill"]
    assert "Skill" in captured["options"]["tools"]
    assert "Skill(review-skill)" in captured["options"]["allowed_tools"]
    assert captured["prompt_messages"][0]["message"]["content"] == "review"
    assert "Authoritative platform Skill requirement" not in (
        captured["prompt_messages"][0]["message"]["content"]
    )
    assert not_admitted.error == "claude_agent_sdk_selected_skill_not_authorized"
    assert not_admitted.turn_diagnostics["terminal_class"] == "tool_policy_or_admission_failure"


@pytest.mark.asyncio
async def test_sdk_error_terminal_preserves_sdk_error_without_skill_invocation(
    monkeypatch,
    tmp_path: Path,
):
    sdk_types: dict[str, Any] = {}

    async def error_query(prompt, options):
        yield sdk_types["ResultMessage"](
            is_error=True,
            subtype="error_during_execution",
            errors=["private upstream detail"],
        )

    sdk = _install_sdk(monkeypatch, error_query)
    sdk_types["ResultMessage"] = sdk.ResultMessage
    monkeypatch.setattr(
        "app.executors.claude_agent_sdk_runner.get_settings",
        _settings,
    )

    result = await run_claude_agent_sdk(
        prompt="review",
        cwd=tmp_path,
        skill_id="review-skill",
        skills=["review-skill"],
    )

    assert result.error == "claude_agent_sdk_upstream_error"
    assert result.turn_diagnostics["terminal_class"] == "upstream_error"
    assert result.used_skills == []
    assert "private upstream detail" not in str(result.turn_diagnostics)
    assert result.runtime_diagnostics["error_code"] == result.error
    assert result.runtime_diagnostics["failure_source"] == "sdk_result_error"
    assert result.runtime_diagnostics["sdk"]["errors"] == [
        "private upstream detail"
    ]


@pytest.mark.asyncio
async def test_dependency_hook_failure_after_selected_success_is_safe_upstream_error(
    monkeypatch,
    tmp_path: Path,
):
    async def query(prompt, options):
        pre_hook = options.kwargs["hooks"]["PreToolUse"][0].hooks[0]
        success_hook = options.kwargs["hooks"]["PostToolUse"][0].hooks[0]
        failure_hook = options.kwargs["hooks"]["PostToolUseFailure"][0].hooks[0]
        selected = {
            "hook_event_name": "PostToolUse",
            "tool_name": "Skill",
            "tool_input": {"skill": "review-skill"},
            "tool_use_id": "selected-tool-id",
        }
        dependency = {
            "hook_event_name": "PostToolUseFailure",
            "tool_name": "Skill",
            "tool_input": {"skill": "minimax-docx"},
            "tool_use_id": "dependency-tool-id",
        }
        await pre_hook(selected)
        await success_hook(selected)
        await pre_hook(dependency)
        await failure_hook(dependency)
        raise RuntimeError("private dependency command failed")
        if False:
            yield None

    async def acknowledge(_fact):
        return True

    _install_sdk(monkeypatch, query)
    monkeypatch.setattr(
        "app.executors.claude_agent_sdk_runner.get_settings",
        _settings,
    )
    metadata = {
        "review-skill": {
            "name": "Document review",
            "version": "version-a",
            "availability": "available",
        }
    }

    result = await run_claude_agent_sdk(
        prompt="review",
        cwd=tmp_path,
        skill_id="review-skill",
        skills=["review-skill", "minimax-docx"],
        public_skill_metadata=metadata,
        on_capability_evidence=acknowledge,
    )

    assert result.error == "claude_agent_sdk_upstream_error"
    assert result.used_skills == ["review-skill"]
    assert result.used_skills_source == "executor_hook"
    assert result.turn_diagnostics["terminal_class"] == "upstream_error"
    assert result.turn_diagnostics["selected_skill"] == metadata["review-skill"]
    assert result.turn_diagnostics["used_skills"] == [metadata["review-skill"]]
    assert "minimax-docx" not in str(result.turn_diagnostics)
    assert "private dependency command failed" not in str(result.turn_diagnostics)


@pytest.mark.parametrize(
    "error_code",
    [
        "required_tool_completion_evidence_missing",
        "required_tool_completion_evidence_mismatch",
    ],
)
def test_required_tool_completion_errors_are_not_classified_as_upstream(error_code):
    diagnostics = project_sdk_turn_diagnostics({}, error_code=error_code)

    assert diagnostics["terminal_class"] == "tool_policy_or_admission_failure"
    assert diagnostics["error_code"] == "claude_agent_sdk_tool_admission_failed"
    assert diagnostics["action"] == "review_skill_or_tool_admission"
    assert diagnostics["retryable"] is False


def test_mcp_execution_receipt_errors_require_reconciliation_before_retry():
    for error_code, terminal_class in (
        (
            "mcp_execution_succeeded_receipt_incomplete",
            "execution_receipt_incomplete",
        ),
        ("mcp_execution_outcome_unknown", "execution_outcome_unknown"),
    ):
        diagnostics = project_sdk_turn_diagnostics({}, error_code=error_code)

        assert diagnostics["terminal_class"] == terminal_class
        assert (
            diagnostics["error_code"]
            == "claude_agent_sdk_execution_receipt_incomplete"
        )
        assert diagnostics["action"] == "reconcile_before_retry"
        assert diagnostics["retryable"] is False


@pytest.mark.asyncio
async def test_generic_upstream_error_never_exposes_private_exception_text(
    monkeypatch,
    tmp_path: Path,
):
    async def query(prompt, options):
        raise RuntimeError("private-token=secret command=do-not-expose")
        if False:
            yield None

    _install_sdk(monkeypatch, query)
    monkeypatch.setattr(
        "app.executors.claude_agent_sdk_runner.get_settings",
        _settings,
    )

    result = await run_claude_agent_sdk(
        prompt="bounded request",
        cwd=tmp_path,
        skill_id="general-chat",
    )

    assert result.error == "claude_agent_sdk_upstream_error"
    assert result.turn_diagnostics["terminal_class"] == "upstream_error"
    assert "private-token" not in str(result.turn_diagnostics)
    assert "do-not-expose" not in str(result.turn_diagnostics)


@pytest.mark.asyncio
async def test_success_diagnostics_include_only_public_skill_metadata_and_bounded_counters(
    monkeypatch,
    tmp_path: Path,
):
    sdk_types: dict[str, Any] = {}

    async def query(prompt, options):
        yield sdk_types["AssistantMessage"]([sdk_types["TextBlock"]("working")])
        hook_input = {
            "hook_event_name": "PostToolUse",
            "tool_name": "Skill",
            "tool_input": {"skill": "internal-review-id"},
            "tool_use_id": "tool-secret-id",
        }
        pre_hook = options.kwargs["hooks"]["PreToolUse"][0].hooks[0]
        post_hook = options.kwargs["hooks"]["PostToolUse"][0].hooks[0]
        await pre_hook(hook_input)
        await post_hook(hook_input)
        yield sdk_types["ResultMessage"](num_turns=3)

    async def acknowledge(_fact):
        return True

    sdk = _install_sdk(monkeypatch, query)
    sdk_types.update(
        {
            "AssistantMessage": sdk.AssistantMessage,
            "ResultMessage": sdk.ResultMessage,
            "TextBlock": sdk.TextBlock,
        }
    )
    monkeypatch.setattr(
        "app.executors.claude_agent_sdk_runner.get_settings",
        _settings,
    )
    metadata = {
        "internal-review-id": {
            "name": "Document review",
            "version": "version-a",
            "availability": "available",
        }
    }

    result = await run_claude_agent_sdk(
        prompt="review",
        cwd=tmp_path,
        skill_id="internal-review-id",
        skills=["internal-review-id"],
        public_skill_metadata=metadata,
        on_capability_evidence=acknowledge,
    )

    diagnostics = result.turn_diagnostics
    assert result.error is None
    assert diagnostics["terminal_class"] == "completed"
    assert diagnostics["last_public_stage"] == "skills"
    assert diagnostics["selected_skill"] == metadata["internal-review-id"]
    assert diagnostics["used_skills"] == [metadata["internal-review-id"]]
    assert diagnostics["counters"] == {
        "max_turns": 8,
        "turns_observed": 3,
        "assistant_messages": 1,
        "text_blocks": 1,
        "result_messages": 1,
        "tool_admission_denials": 0,
        "tool_policy_denials": 0,
        "tool_lifecycle_denials": 0,
        "skill_invocations": 1,
        "public_projection_omissions": 0,
    }
    assert "internal-review-id" not in str(diagnostics)
    assert "tool-secret-id" not in str(diagnostics)
