from app.sandbox_hardening_contract import (
    bounded_error_projection_error,
    bounded_error_projection_is_safe,
    safe_bounded_error_projection,
)


def safe_projection(**overrides):
    projection = {
        "source": "admin_runtime_projection",
        "run_id": "run-a",
        "status": "failed",
        "error_code": "executor_health_timeout",
        "host_paths_redacted": True,
        "raw_docker_payload_absent": True,
        "callback_token_absent": True,
    }
    projection.update(overrides)
    return projection


def test_bounded_error_projection_accepts_only_safe_projection_shape():
    projection = safe_projection()

    assert bounded_error_projection_error(projection, run_id="run-a") is None
    assert bounded_error_projection_is_safe(projection, run_id="run-a") is True
    assert safe_bounded_error_projection(projection, run_id="run-a") == projection


def test_bounded_error_projection_rejects_self_asserted_or_leaky_shapes():
    assert bounded_error_projection_error(None, run_id="run-a") == "resource_limits.bounded_error_projection"
    assert (
        bounded_error_projection_error(
            safe_projection(run_id="run-b"),
            run_id="run-a",
        )
        == "resource_limits.bounded_error_projection.run_id"
    )
    assert (
        bounded_error_projection_error(
            safe_projection(source="raw_docker_error"),
            run_id="run-a",
        )
        == "resource_limits.bounded_error_projection.source"
    )
    assert (
        bounded_error_projection_error(
            safe_projection(status="running"),
            run_id="run-a",
        )
        == "resource_limits.bounded_error_projection.status"
    )
    assert (
        bounded_error_projection_error(
            safe_projection(error_code="unredacted_exception"),
            run_id="run-a",
        )
        == "resource_limits.bounded_error_projection.error_code"
    )
    assert (
        bounded_error_projection_error(
            safe_projection(raw_docker_payload_absent=False),
            run_id="run-a",
        )
        == "resource_limits.bounded_error_projection.raw_docker_payload_absent"
    )
    assert (
        bounded_error_projection_error(
            safe_projection(raw_error="Container failed with /home/user/private"),
            run_id="run-a",
        )
        == "resource_limits.bounded_error_projection.unknown_fields"
    )
    assert safe_bounded_error_projection(safe_projection(callback_token_absent=False), run_id="run-a") is None
