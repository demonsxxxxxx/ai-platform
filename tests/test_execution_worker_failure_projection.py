from dataclasses import dataclass, field

from app.control_plane_contracts import sanitize_public_text
from app.execution.api import normalize_sandbox_reported_failure, public_executor_failure_message


@dataclass(frozen=True)
class _Result:
    status: str
    result: dict[str, object]
    executor_payload: dict[str, object] = field(default_factory=dict)


def test_sandbox_failure_normalization_drops_private_payload_and_preserves_safe_code():
    result = _Result(
        status="failed",
        result={
            "error_code": "tool_invocation_evidence_mismatch",
            "message": "/workspace/private --token secret",
            "storage_key": "private",
        },
        executor_payload={"sandbox_runtime_used": True, "sdk_error": "private"},
    )

    normalized = normalize_sandbox_reported_failure(result)

    assert normalized.result == {
        "error_code": "tool_invocation_evidence_mismatch",
        "message": "Tool invocation evidence was incomplete",
    }
    assert normalized.executor_payload["sdk_error"] == "tool_invocation_evidence_mismatch"
    assert public_executor_failure_message(normalized, sanitize_text=sanitize_public_text) == "Tool invocation evidence was incomplete"


def test_non_sandbox_failure_redacts_request_identity():
    result = _Result(status="failed", result={"message": "request id: secret-token"})

    assert public_executor_failure_message(result, sanitize_text=sanitize_public_text) == "request id: [redacted-id]"
    assert normalize_sandbox_reported_failure(result) is result
