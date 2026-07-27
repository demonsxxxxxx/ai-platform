import json

import httpx
import pytest
from pydantic import ValidationError

from app.runtime.sandbox.readiness_evidence import (
    ExecutorReadinessEvidence,
    bounded_elapsed_ms,
    health_failure_outcome,
    normalize_docker_readiness_evidence,
    safe_readiness_evidence_payload,
)


def test_readiness_evidence_normalizes_terminal_docker_state_and_discards_private_attrs():
    prohibited_values = {
        "Logs": "stdout-private",
        "Config": {"Env": ["TOKEN=secret-private"], "Cmd": ["private-command"]},
        "Image": "registry.example/private-image",
        "NetworkSettings": {"Ports": {"18000/tcp": [{"HostPort": "43123"}]}},
        "Id": "container-private-id",
    }

    evidence = normalize_docker_readiness_evidence(
        readiness_phase="publish_wait",
        container_attrs={
            **prohibited_values,
            "State": {"Status": "exited", "ExitCode": 137, "OOMKilled": True},
        },
        container_status="private-fallback-state",
        published_port_observed=False,
        health_outcome="not_attempted",
        elapsed_ms=321,
    )

    assert safe_readiness_evidence_payload(evidence) == {
        "readiness_phase": "publish_wait",
        "container_state": "exited",
        "exit_code": 137,
        "oom_killed": True,
        "published_port_observed": False,
        "health_outcome": "not_attempted",
        "elapsed_ms": 321,
    }
    serialized = json.dumps(safe_readiness_evidence_payload(evidence), sort_keys=True)
    for prohibited in (
        "stdout-private",
        "TOKEN=secret-private",
        "private-command",
        "registry.example/private-image",
        "43123",
        "container-private-id",
        "private-fallback-state",
    ):
        assert prohibited not in serialized


@pytest.mark.parametrize(
    ("attrs", "expected_state"),
    (
        (None, "unknown"),
        ("private-raw-attrs", "unknown"),
        ({"State": "private-raw-state"}, "unknown"),
        ({"State": {"Status": ["private-state"], "ExitCode": "private-exit", "OOMKilled": "private-oom"}}, "unknown"),
        ({"State": {"Status": "exited", "ExitCode": 2**63, "OOMKilled": 1}}, "exited"),
    ),
)
def test_readiness_evidence_malformed_attrs_fail_closed(attrs, expected_state):
    evidence = normalize_docker_readiness_evidence(
        readiness_phase="health_probe",
        container_attrs=attrs,
        container_status=None,
        published_port_observed=True,
        health_outcome="unhealthy",
        elapsed_ms=-100,
    )

    assert evidence.container_state == expected_state
    assert evidence.exit_code is None
    assert evidence.oom_killed is None
    assert evidence.elapsed_ms == 0
    assert "private" not in repr(safe_readiness_evidence_payload(evidence))


@pytest.mark.parametrize(
    ("started_at", "finished_at", "expected"),
    (
        (1.0, 1.125, 125),
        (2.0, 1.0, 0),
        (0.0, float("inf"), 0),
        (0.0, (2**31 + 1) / 1000, 2**31 - 1),
        ("private-start", 1.0, 0),
    ),
)
def test_bounded_elapsed_ms_rejects_or_clamps_unbounded_values(started_at, finished_at, expected):
    assert bounded_elapsed_ms(started_at, finished_at) == expected


@pytest.mark.parametrize(
    ("exc", "expected"),
    (
        (TimeoutError("private-timeout"), "timeout"),
        (httpx.ReadTimeout("private-http-timeout"), "timeout"),
        (RuntimeError("private-transport-error"), "transport_error"),
    ),
)
def test_health_failure_outcome_is_bounded_and_drops_exception_text(exc, expected):
    outcome = health_failure_outcome(exc)
    assert outcome == expected
    assert str(exc) not in outcome


def test_typed_readiness_evidence_rejects_extra_or_unbounded_fields():
    valid = {
        "readiness_phase": "health_probe",
        "container_state": "running",
        "exit_code": None,
        "oom_killed": None,
        "published_port_observed": True,
        "health_outcome": "timeout",
        "elapsed_ms": 1,
    }

    with pytest.raises(ValidationError):
        ExecutorReadinessEvidence(**{**valid, "logs": "private-log"})
    with pytest.raises(ValidationError):
        ExecutorReadinessEvidence(**{**valid, "elapsed_ms": 2**63})
    with pytest.raises(ValidationError):
        ExecutorReadinessEvidence(**{**valid, "published_port_observed": 1})
