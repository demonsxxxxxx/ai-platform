"""Pure-offline coverage for the R1 Run Control evidence packet validator."""

from __future__ import annotations

import json
from copy import deepcopy

import pytest

from tools.acceptance.run_control.validate_run_control_evidence_packet import (
    MAX_JSON_DEPTH,
    MAX_PACKET_BYTES,
    PACKET_SCHEMA_VERSION,
    PacketDecodeError,
    REQUIRED_CASE_IDS,
    _result,
    decode_evidence_packet,
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
    """Provide a complete redacted packet with canonical evidence names."""

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


def _source_ref(packet: dict[str, object]) -> dict[str, str]:
    return packet["cases"][0]["evidence_refs"][0]


def _nested_json(depth: int, payload: object) -> str:
    return "[" * depth + json.dumps(payload) + "]" * depth


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
    "reference",
    [
        "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJydW4ifQ.signature",
        "ghp_abcdefghijklmnopqrstuvwxyz123456",
        "github_pat_abcdefghijklmnopqrstuvwxyz_123456",
        "C:\\evidence\\source-main.json",
        "/evidence/source-main.json",
        "evidence/../source-main.json",
        "https://example.test/evidence/source-main.json",
        "evidence\\source-main.json",
        "evidence/archive/source-main.json",
        "evidence/source_main.json",
        "evidence/source-main.extra.json",
    ],
)
def test_validator_rejects_noncanonical_or_secret_shaped_evidence_refs(valid_packet, reference):
    packet = deepcopy(valid_packet)
    _source_ref(packet)["ref"] = reference

    assert "evidence_refs_invalid" in validate_run_control_evidence_packet(packet, expected_main_sha=MAIN_SHA)


def test_validator_rejects_type_prefix_mismatch(valid_packet):
    packet = deepcopy(valid_packet)
    _source_ref(packet)["type"] = "runtime"

    assert "evidence_refs_invalid" in validate_run_control_evidence_packet(packet, expected_main_sha=MAIN_SHA)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda packet: packet.update({"gateway_secret": "not-allowed"}),
        lambda packet: _source_ref(packet).update({"ref": "Bearer abcdefghijk"}),
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
        lambda packet: packet["cases"][0].update({"case_id": "unknown_case"}),
    ],
)
def test_validator_rejects_malformed_case_status_or_identifier(valid_packet, mutation):
    packet = deepcopy(valid_packet)
    mutation(packet)

    errors = validate_run_control_evidence_packet(packet, expected_main_sha=MAIN_SHA)
    assert any(error in errors for error in {"case_status_invalid", "case_id_invalid"})


@pytest.mark.parametrize(
    "raw_packet",
    [
        '{"schema_version":"v","source":{},"source":{},"cases":[]}',
        '{"gateway_secret":"Bearer abcdefghijk","gateway_secret":"legal"}',
        '{"cases":[{"case_id":"runtime_run_control","case_id":"browser_ordinary_user_run_control"}]}',
        '{"evidence_refs":[{"type":"source","type":"runtime","ref":"evidence/source-main.json"}]}',
        '{"evidence_refs":[{"type":"source","ref":"evidence/source-main.json","ref":"evidence/source-other.json"}]}',
    ],
)
def test_strict_decoder_rejects_duplicate_members_before_schema_or_secret_validation(raw_packet):
    with pytest.raises(PacketDecodeError) as exc_info:
        decode_evidence_packet(raw_packet)

    assert exc_info.value.code == "packet_duplicate_member"
    assert _result([exc_info.value.code]) == {
        "status": "schema_invalid",
        "schema_validity_is_not_runtime_proof": True,
        "errors": ["packet_duplicate_member"],
    }


def test_strict_decoder_rejects_bounded_size_and_depth():
    with pytest.raises(PacketDecodeError, match="packet_size_exceeded"):
        decode_evidence_packet(" " * (MAX_PACKET_BYTES + 1))

    with pytest.raises(PacketDecodeError, match="packet_depth_exceeded"):
        decode_evidence_packet(_nested_json(MAX_JSON_DEPTH + 1, {}))


def test_predecode_depth_guard_rejects_near_limit_valid_json_before_semantic_decoding():
    depth = MAX_JSON_DEPTH + 1
    payload = "x" * (MAX_PACKET_BYTES - (2 * depth) - 2)
    raw_packet = _nested_json(depth, payload)

    assert len(raw_packet.encode("utf-8")) == MAX_PACKET_BYTES
    with pytest.raises(PacketDecodeError) as exc_info:
        decode_evidence_packet(raw_packet)

    assert exc_info.value.code == "packet_depth_exceeded"
    assert _result([exc_info.value.code]) == {
        "status": "schema_invalid",
        "schema_validity_is_not_runtime_proof": True,
        "errors": ["packet_depth_exceeded"],
    }


def test_predecode_depth_guard_takes_priority_over_outer_duplicate_member():
    raw_packet = '{"outer":' + _nested_json(MAX_JSON_DEPTH + 1, 0) + ',"outer":0}'

    with pytest.raises(PacketDecodeError) as exc_info:
        decode_evidence_packet(raw_packet)

    result_text = json.dumps(_result([exc_info.value.code]))
    assert exc_info.value.code == "packet_depth_exceeded"
    assert "outer" not in result_text
    assert "Traceback" not in result_text


def test_predecode_nesting_guard_ignores_delimiters_and_escapes_inside_strings():
    expected_text = '{}[] with "quotes" and a \\ backslash'
    raw_packet = _nested_json(MAX_JSON_DEPTH, expected_text)

    decoded = decode_evidence_packet(raw_packet)
    for _ in range(MAX_JSON_DEPTH):
        decoded = decoded[0]

    assert decoded == expected_text


def test_bounded_nested_duplicate_still_uses_fixed_duplicate_member_diagnostic():
    raw_packet = '[[{"case":{"case_id":"runtime_run_control","case_id":"browser_ordinary_user_run_control"}}]]'

    with pytest.raises(PacketDecodeError) as exc_info:
        decode_evidence_packet(raw_packet)

    assert exc_info.value.code == "packet_duplicate_member"
    assert "case_id" not in json.dumps(_result([exc_info.value.code]))


def test_validator_rejects_local_only_evidence_for_runtime_and_browser_claims(valid_packet):
    packet = deepcopy(valid_packet)
    for case in packet["cases"]:
        case["evidence_refs"] = [{"type": "source", "ref": "evidence/source-local-only.json"}]

    assert "evidence_refs_invalid" in validate_run_control_evidence_packet(packet, expected_main_sha=MAIN_SHA)
