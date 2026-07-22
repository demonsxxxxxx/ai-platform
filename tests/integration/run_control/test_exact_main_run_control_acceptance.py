"""Pure-offline coverage for the R1 Run Control evidence packet validator."""

from __future__ import annotations

from copy import deepcopy

import pytest

from tools.acceptance.run_control.validate_run_control_evidence_packet import (
    PACKET_SCHEMA_VERSION,
    REQUIRED_CASE_IDS,
    validate_run_control_evidence_packet,
)


MAIN_SHA = "a" * 40


def _evidence_refs() -> list[dict[str, str]]:
    return [
        {"type": "source", "ref": "evidence/source-main.json"},
        {"type": "runtime", "ref": "evidence/runtime-run-control.json"},
        {"type": "browser", "ref": "evidence/browser-ordinary-user.json"},
    ]


@pytest.fixture
def valid_packet() -> dict[str, object]:
    """Provide a redacted packet with distinct source, runtime, and browser references."""

    return {
        "schema_version": PACKET_SCHEMA_VERSION,
        "source": {
            "branch": "main",
            "commit_sha": MAIN_SHA,
            "runtime_subject_commit_sha": MAIN_SHA,
        },
        "cases": [
            {
                "case_id": case_id,
                "status": "evidence_recorded",
                "evidence_refs": _evidence_refs(),
            }
            for case_id in REQUIRED_CASE_IDS
        ],
    }


def test_validator_accepts_only_a_complete_redacted_exact_main_packet(valid_packet):
    assert validate_run_control_evidence_packet(valid_packet, expected_main_sha=MAIN_SHA) == []


def test_validator_rejects_exact_main_subject_mismatch(valid_packet):
    packet = deepcopy(valid_packet)
    packet["source"]["runtime_subject_commit_sha"] = "b" * 40

    assert "exact_main_subject_mismatch" in validate_run_control_evidence_packet(packet, expected_main_sha=MAIN_SHA)


@pytest.mark.parametrize(
    ("mutation", "expected_error"),
    [
        (lambda packet: packet["cases"].pop(), "case_count_invalid"),
        (lambda packet: packet["cases"].append(deepcopy(packet["cases"][0])), "duplicate_case_id"),
    ],
)
def test_validator_rejects_missing_or_duplicate_required_cases(valid_packet, mutation, expected_error):
    packet = deepcopy(valid_packet)
    mutation(packet)

    assert expected_error in validate_run_control_evidence_packet(packet, expected_main_sha=MAIN_SHA)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda packet: packet.update({"gateway_secret": "not-allowed"}),
        lambda packet: packet["cases"][0]["evidence_refs"][0].update({"ref": "Bearer abcdefghijk"}),
    ],
)
def test_validator_rejects_secret_like_keys_or_values(valid_packet, mutation):
    packet = deepcopy(valid_packet)
    mutation(packet)

    assert "secret_like_material_present" in validate_run_control_evidence_packet(packet, expected_main_sha=MAIN_SHA)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda packet: packet["cases"][0].update({"status": "passed"}),
        lambda packet: packet["cases"][0]["evidence_refs"][0].update({"ref": "../unsafe-ref"}),
        lambda packet: packet["cases"][0].update({"case_id": "unknown_case"}),
    ],
)
def test_validator_rejects_malformed_case_status_or_evidence_reference(valid_packet, mutation):
    packet = deepcopy(valid_packet)
    mutation(packet)

    errors = validate_run_control_evidence_packet(packet, expected_main_sha=MAIN_SHA)
    assert errors
    assert any(error in errors for error in {"case_status_invalid", "evidence_refs_invalid", "case_id_invalid"})


def test_validator_rejects_local_only_evidence_for_runtime_and_browser_claims(valid_packet):
    packet = deepcopy(valid_packet)
    for case in packet["cases"]:
        case["evidence_refs"] = [{"type": "source", "ref": "evidence/local-only.json"}]

    errors = validate_run_control_evidence_packet(packet, expected_main_sha=MAIN_SHA)

    assert "evidence_refs_invalid" in errors
