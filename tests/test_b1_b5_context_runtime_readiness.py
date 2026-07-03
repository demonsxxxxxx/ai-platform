import json
import subprocess
import sys

from app.b1_b5_context_runtime_readiness import (
    REQUIRED_CHECKS,
    build_b1_b5_context_runtime_readiness,
)


def test_b1_b5_context_runtime_readiness_verifies_bounded_context_runtime_contract():
    readiness = build_b1_b5_context_runtime_readiness()

    assert readiness["schema_version"] == "ai-platform.b1-b5-context-runtime-readiness.v1"
    assert readiness["status"] == "local_runtime_verifier_ready"
    assert readiness["status_label"] == "local partial"
    assert readiness["ok"] is True
    assert readiness["target"] == "local_b1_b5_context_runtime"
    assert set(readiness["checks"]) == set(REQUIRED_CHECKS)
    assert all(item["passed"] is True for item in readiness["checks"].values())
    assert readiness["non_expansion_invariants"] == {
        "does_not_touch_211": True,
        "does_not_close_b1_or_b5_gate": True,
        "long_term_cross_session_memory_enabled": False,
        "public_projection_only_for_ordinary_users": True,
    }


def test_b1_b5_context_runtime_readiness_detects_prompt_private_material_leak():
    readiness = build_b1_b5_context_runtime_readiness(
        prompt_probe_private_payload={"storage_key": "tenants/tenant-a/private/source.txt"},
    )

    assert readiness["ok"] is False
    assert readiness["status"] == "blocked_runtime_contract"
    assert readiness["checks"]["public_projection_redacts_private_context_material"]["passed"] is False


def test_b1_b5_context_runtime_readiness_cli_outputs_redacted_json():
    result = subprocess.run(
        [sys.executable, "tools/verify_b1_b5_context_runtime.py", "--format", "json"],
        check=True,
        capture_output=True,
        text=True,
    )

    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["schema_version"] == "ai-platform.b1-b5-context-runtime-readiness.v1"
    serialized = result.stdout.lower()
    assert "storage_key" not in serialized
    assert "private/source" not in serialized
    assert "c:\\users\\" not in serialized
    assert "/home/" not in serialized
