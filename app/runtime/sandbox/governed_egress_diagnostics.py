from __future__ import annotations

import logging
from enum import Enum
from typing import Any


_logger = logging.getLogger(__name__)

_HTTP_STATUS_EXIT_CODE = 70
_PAYLOAD_INVALID_EXIT_CODE = 71
_RUNTIME_COMMIT_MISMATCH_EXIT_CODE = 72
_CALLBACK_FAILURE_STAGE_BY_EXIT_CODE = {
    _HTTP_STATUS_EXIT_CODE: "callback_probe_http_status",
    _PAYLOAD_INVALID_EXIT_CODE: "callback_probe_payload_invalid",
    _RUNTIME_COMMIT_MISMATCH_EXIT_CODE: "callback_probe_runtime_commit_mismatch",
}


class AdmissionGate(Enum):
    """Allowlisted top-level governed-egress admission gates."""

    PREFLIGHT_TOPOLOGY = "preflight_topology_admission_failed"
    POST_CREATE_PROOF_SEAL = "post_create_proof_seal_failed"
    CALLBACK_REACHABILITY = "callback_reachability_failed"


def _emit(stage: str, *, failed: bool) -> None:
    log = _logger.warning if failed else _logger.debug
    log(
        "governed_egress_private_diagnostic stage=%s outcome=%s",
        stage,
        "failed" if failed else "passed",
    )


def record_admission_failure(gate: AdmissionGate) -> None:
    """Log one fixed admission gate without accepting runtime subject data."""
    if isinstance(gate, AdmissionGate):
        _emit(gate.value, failed=True)


def callback_probe_command(callback_base_url: str, expected_runtime_commit: str) -> list[str]:
    """Build the bounded callback probe command for prevalidated inputs."""
    script = f"""
import json
import sys
import urllib.error
import urllib.request

url = sys.argv[1].rstrip('/') + '/api/ai/health'
try:
    with urllib.request.urlopen(url, timeout=3) as response:
        if getattr(response, 'status', None) != 200:
            raise SystemExit({_HTTP_STATUS_EXIT_CODE})
        try:
            body = json.load(response)
        except Exception:
            raise SystemExit({_PAYLOAD_INVALID_EXIT_CODE}) from None
except urllib.error.HTTPError:
    raise SystemExit({_HTTP_STATUS_EXIT_CODE}) from None
except SystemExit:
    raise
except Exception:
    raise SystemExit(1) from None

if (
    not isinstance(body, dict)
    or set(body) != {{'status', 'runtime_commit'}}
    or body.get('status') != 'ok'
    or not isinstance(body.get('runtime_commit'), str)
):
    raise SystemExit({_PAYLOAD_INVALID_EXIT_CODE})
if body['runtime_commit'] != sys.argv[2]:
    raise SystemExit({_RUNTIME_COMMIT_MISMATCH_EXIT_CODE})
""".strip()
    return ["python", "-c", script, callback_base_url, expected_runtime_commit]


def record_callback_exec_exception() -> bool:
    """Record a Docker exec exception and return the fail-closed probe result."""
    _emit("callback_probe_docker_exec_exception", failed=True)
    return False


def record_callback_exec_result(result: Any) -> bool:
    """Interpret and record only the fixed callback probe result classification."""
    exit_code = getattr(result, "exit_code", None)
    if exit_code is None and isinstance(result, tuple) and result:
        exit_code = result[0]
    if type(exit_code) is int and exit_code == 0:
        _emit("callback_probe_success", failed=False)
        return True
    stage = (
        _CALLBACK_FAILURE_STAGE_BY_EXIT_CODE.get(exit_code, "callback_probe_docker_exec_nonzero")
        if type(exit_code) is int
        else "callback_probe_docker_exec_nonzero"
    )
    _emit(stage, failed=True)
    return False
