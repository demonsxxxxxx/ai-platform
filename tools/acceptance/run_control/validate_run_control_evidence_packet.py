#!/usr/bin/env python3
"""Validate one redacted local Run Control packet without opening evidence references."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any


PACKET_SCHEMA_VERSION = "ai-platform.run-control-r1-evidence.v1"
REQUIRED_CASE_IDS = (
    "runtime_run_control",
    "browser_ordinary_user_run_control",
)
RECORDED_STATUS = "evidence_recorded"
NON_CLAIM_STATUSES = {"not_run", "blocked"}
EVIDENCE_TYPES = {"source", "runtime", "browser"}
MAX_PACKET_BYTES = 64 * 1024
MAX_JSON_DEPTH = 12
FULL_GIT_SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$", re.IGNORECASE)
EVIDENCE_REF_PATTERNS = {
    "source": re.compile(r"^evidence/source-[a-z0-9][a-z0-9-]{0,63}\.json$"),
    "runtime": re.compile(r"^evidence/runtime-[a-z0-9][a-z0-9-]{0,63}\.json$"),
    "browser": re.compile(r"^evidence/browser-[a-z0-9][a-z0-9-]{0,63}\.json$"),
}
SECRET_KEY_MARKERS = (
    "api_key",
    "authorization",
    "cookie",
    "credential",
    "password",
    "secret",
    "token",
)
SECRET_VALUE_PATTERNS = (
    re.compile(r"\bbearer\s+[A-Za-z0-9._~+/=-]{8,}\b", re.IGNORECASE),
    re.compile(r"\bghp_[A-Za-z0-9]{20,}\b", re.IGNORECASE),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b", re.IGNORECASE),
    re.compile(r"\beyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b"),
    re.compile(r"\bsk-[A-Za-z0-9][A-Za-z0-9_-]{8,}\b", re.IGNORECASE),
    re.compile(r"\b(?:api[_ -]?key|client[_ -]?secret|password|token)\s*[:=]\s*\S+", re.IGNORECASE),
    re.compile(r"-----BEGIN (?:[A-Z ]+ )?PRIVATE KEY-----", re.IGNORECASE),
)


class PacketDecodeError(ValueError):
    """Carry one fixed, redacted packet-decoding diagnostic code."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _as_dict(value: Any) -> dict[str, Any]:
    """Return a mapping only when a JSON value has object shape."""

    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    """Return a list only when a JSON value has array shape."""

    return value if isinstance(value, list) else []


def _reject_duplicate_members(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """Build one JSON object while rejecting a duplicate member at every depth."""

    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise PacketDecodeError("packet_duplicate_member")
        result[key] = value
    return result


def _guard_raw_json_nesting(raw_text: str) -> None:
    """Linearly cap container nesting while ignoring braces and brackets in strings."""

    nesting = 0
    in_string = False
    escaped = False
    for character in raw_text:
        if in_string:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                in_string = False
            continue
        if character == '"':
            in_string = True
        elif character in "{[":
            nesting += 1
            if nesting > MAX_JSON_DEPTH:
                raise PacketDecodeError("packet_depth_exceeded")
        elif character in "}]":
            # Deliberately do not validate matching structure here.  The strict
            # JSON decoder owns malformed syntax after this resource guard.
            nesting = max(nesting - 1, 0)


def _validate_json_depth(value: Any, *, depth: int = 0) -> None:
    """Keep the decoded container walk aligned with the pre-decode nesting cap."""

    if isinstance(value, dict):
        if depth >= MAX_JSON_DEPTH:
            raise PacketDecodeError("packet_depth_exceeded")
        for child in value.values():
            _validate_json_depth(child, depth=depth + 1)
    elif isinstance(value, list):
        if depth >= MAX_JSON_DEPTH:
            raise PacketDecodeError("packet_depth_exceeded")
        for child in value:
            _validate_json_depth(child, depth=depth + 1)


def decode_evidence_packet(raw_text: str) -> Any:
    """Strictly decode bounded JSON with duplicate-member and depth protection."""

    if len(raw_text.encode("utf-8")) > MAX_PACKET_BYTES:
        raise PacketDecodeError("packet_size_exceeded")
    _guard_raw_json_nesting(raw_text)
    try:
        decoded = json.loads(raw_text, object_pairs_hook=_reject_duplicate_members)
    except PacketDecodeError:
        raise
    except RecursionError as exc:
        raise PacketDecodeError("packet_depth_exceeded") from exc
    except json.JSONDecodeError as exc:
        raise PacketDecodeError("packet_not_valid_json") from exc
    _validate_json_depth(decoded)
    return decoded


def _read_packet(packet_path: str) -> Any:
    """Read only the explicitly supplied packet, bounded before JSON decoding."""

    try:
        with Path(packet_path).open("rb") as packet_file:
            raw_bytes = packet_file.read(MAX_PACKET_BYTES + 1)
    except OSError as exc:
        raise PacketDecodeError("packet_not_valid_json") from exc
    if len(raw_bytes) > MAX_PACKET_BYTES:
        raise PacketDecodeError("packet_size_exceeded")
    try:
        return decode_evidence_packet(raw_bytes.decode("utf-8"))
    except UnicodeDecodeError as exc:
        raise PacketDecodeError("packet_not_valid_json") from exc


def _contains_secret_like_material(value: Any) -> bool:
    """Reject common credential keys and credential-shaped values recursively."""

    if isinstance(value, dict):
        for key, child in value.items():
            normalized_key = str(key).strip().lower().replace("-", "_")
            if any(marker in normalized_key for marker in SECRET_KEY_MARKERS):
                return True
            if _contains_secret_like_material(child):
                return True
        return False
    if isinstance(value, list):
        return any(_contains_secret_like_material(item) for item in value)
    if isinstance(value, str):
        return any(pattern.search(value) is not None for pattern in SECRET_VALUE_PATTERNS)
    return False


def _valid_evidence_refs(value: Any, *, claimed: bool) -> bool:
    """Require one canonical, type-matching relative name for each evidence type."""

    refs = _as_list(value)
    if not claimed:
        return refs == []
    if len(refs) != len(EVIDENCE_TYPES):
        return False
    types: list[str] = []
    for item in refs:
        entry = _as_dict(item)
        if set(entry) != {"type", "ref"}:
            return False
        evidence_type = entry.get("type")
        reference = entry.get("ref")
        if not isinstance(evidence_type, str) or evidence_type not in EVIDENCE_TYPES:
            return False
        if not isinstance(reference, str) or EVIDENCE_REF_PATTERNS[evidence_type].fullmatch(reference) is None:
            return False
        types.append(evidence_type)
    return set(types) == EVIDENCE_TYPES and len(types) == len(set(types))


def validate_run_control_evidence_packet(packet: Any, *, expected_main_sha: str) -> list[str]:
    """Return fixed schema error codes; an empty list is never runtime proof."""

    errors: list[str] = []
    if FULL_GIT_SHA_PATTERN.fullmatch(expected_main_sha) is None:
        return ["expected_main_sha_invalid"]
    if _contains_secret_like_material(packet):
        errors.append("secret_like_material_present")
    body = _as_dict(packet)
    if set(body) != {"schema_version", "source", "cases"}:
        errors.append("packet_shape_invalid")
        return errors
    if body.get("schema_version") != PACKET_SCHEMA_VERSION:
        errors.append("schema_version_invalid")
    source = _as_dict(body.get("source"))
    if set(source) != {"branch", "commit_sha", "runtime_subject_commit_sha"}:
        errors.append("source_shape_invalid")
    else:
        source_commit = source.get("commit_sha")
        runtime_subject = source.get("runtime_subject_commit_sha")
        if source.get("branch") != "main":
            errors.append("source_branch_invalid")
        if not isinstance(source_commit, str) or FULL_GIT_SHA_PATTERN.fullmatch(source_commit) is None:
            errors.append("source_commit_invalid")
        if not isinstance(runtime_subject, str) or FULL_GIT_SHA_PATTERN.fullmatch(runtime_subject) is None:
            errors.append("runtime_subject_invalid")
        if source_commit != expected_main_sha or runtime_subject != expected_main_sha:
            errors.append("exact_main_subject_mismatch")
    cases = _as_list(body.get("cases"))
    if len(cases) != len(REQUIRED_CASE_IDS):
        errors.append("case_count_invalid")
    observed_case_ids: list[str] = []
    for item in cases:
        case = _as_dict(item)
        if set(case) != {"case_id", "status", "evidence_refs"}:
            errors.append("case_shape_invalid")
            continue
        case_id = case.get("case_id")
        status = case.get("status")
        if not isinstance(case_id, str) or case_id not in REQUIRED_CASE_IDS:
            errors.append("case_id_invalid")
        else:
            observed_case_ids.append(case_id)
        if not isinstance(status, str) or status not in {RECORDED_STATUS, *NON_CLAIM_STATUSES}:
            errors.append("case_status_invalid")
            continue
        if not _valid_evidence_refs(case.get("evidence_refs"), claimed=status == RECORDED_STATUS):
            errors.append("evidence_refs_invalid")
    if set(observed_case_ids) != set(REQUIRED_CASE_IDS):
        errors.append("required_cases_missing")
    if len(observed_case_ids) != len(set(observed_case_ids)):
        errors.append("duplicate_case_id")
    return list(dict.fromkeys(errors))


def _result(errors: list[str]) -> dict[str, object]:
    """Build schema-only output without claiming runtime or browser acceptance."""

    return {
        "status": "schema_valid" if not errors else "schema_invalid",
        "schema_validity_is_not_runtime_proof": True,
        "errors": errors,
    }


def main() -> int:
    """Read one local packet and emit schema-only validation output."""

    parser = argparse.ArgumentParser(description="Validate a local redacted Run Control evidence packet.")
    parser.add_argument("--packet", required=True)
    parser.add_argument("--expected-main-sha", required=True)
    args = parser.parse_args()
    try:
        packet = _read_packet(args.packet)
    except PacketDecodeError as exc:
        result = _result([exc.code])
    else:
        result = _result(validate_run_control_evidence_packet(packet, expected_main_sha=args.expected_main_sha))
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result["status"] == "schema_valid" else 1


if __name__ == "__main__":
    raise SystemExit(main())
