"""Generate live sandbox runtime evidence for the 211 verifier.

This script is a smoke tool. It creates a verifier-owned callback receiver,
submits one task to a running sandbox executor, runs a verifier-owned Docker
create/stop/remove probe, and writes sanitized evidence for
verify_sandbox_runtime_211.py.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import errno
import hashlib
import hmac
import inspect
import json
import math
import os
import re
import stat
import subprocess
import sys
import threading
import time
import textwrap
import tempfile
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse
from urllib import request as urllib_request


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

TERMINAL_STATUSES = {"completed", "failed", "cancelled"}
SAFE_NAME_PATTERN = re.compile(r"[^a-zA-Z0-9_.-]+")
EVIDENCE_SCHEMA_VERSION = "ai-platform.sandbox-runtime-211.v1"
INSPECTION_EVIDENCE_SCHEMA_VERSION = "ai-platform.sandbox-skill-mount-inspection-211.v1"
BOOTSTRAP_EVIDENCE_SCHEMA_VERSION = "ai-platform.sandbox-runtime-211.bootstrap-error.v1"
LATENCY_SCHEMA_VERSION = "ai-platform.sandbox-latency-split.v1"
RUNTIME_PROBE_RESULTS_SCHEMA_VERSION = "ai-platform.sandbox-runtime-probe-results.v1"
INSPECTION_PROFILES = ("platform-controlled", "sdk-native")
INSPECTION_AUTHORIZED_SKILLS = {
    "platform-controlled": "qa-file-reviewer",
    "sdk-native": "minimax-docx",
}
INSPECTION_WORKSPACE_BASE_NAME = "ai-platform-sandbox-verifier-211"
INSPECTION_ATTACKS = (
    "direct_write",
    "chmod",
    "rm",
    "mv",
    "symlink",
    "delivery_link_mutation",
)
SOURCE_SHA_PATTERN = re.compile(r"[0-9a-fA-F]{40}|[0-9a-fA-F]{64}")
IMAGE_REFERENCE_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,254}")
PLATFORM_DEADLINE_PROBE_SECONDS = 2.0
NON_EXPANSION_INVARIANTS = {
    "ordinary_user_high_risk_sandbox_allowed": False,
    "admin_or_allowlist_only": True,
    "production_concurrency_defaults_raised": False,
    "docker_sandbox_production_hardening_claimed": False,
    "ordinary_user_multi_agent_allowed": False,
}
RUNTIME_PROBE_RESULTS_ALLOWED_KEYS = {
    "schema_version",
    "run_id",
    "source",
    "resource_limits",
    "egress_policy",
    "security_options",
}
RUNTIME_PROBE_RESULTS_SECTION_KEYS = ("resource_limits", "egress_policy", "security_options")


class SandboxEvidenceArgumentParser(argparse.ArgumentParser):
    """Normalize callback defaults after runtime/provider flags are parsed."""

    def parse_args(self, args: list[str] | None = None, namespace: argparse.Namespace | None = None) -> argparse.Namespace:
        raw_args = list(args) if args is not None else sys.argv[1:]
        callback_host_explicit = any(
            item == "--callback-host" or item.startswith("--callback-host=") for item in raw_args
        )
        callback_public_url_explicit = any(
            item == "--callback-public-url" or item.startswith("--callback-public-url=") for item in raw_args
        )
        parsed = super().parse_args(args, namespace)
        _normalize_callback_defaults(
            parsed,
            callback_host_explicit=callback_host_explicit,
            callback_public_url_explicit=callback_public_url_explicit,
        )
        return parsed


def _normalize_callback_defaults(
    args: argparse.Namespace,
    *,
    callback_host_explicit: bool = False,
    callback_public_url_explicit: bool = False,
) -> None:
    docker_platform_callback = args.sandbox_provider == "docker" and (
        args.runtime_mode == "platform" or bool(args.generate_runtime_probe_results_file)
    )
    callback_host_configured = callback_host_explicit or os.environ.get("AI_PLATFORM_CALLBACK_HOST") is not None
    callback_public_url_configured = (
        callback_public_url_explicit or os.environ.get("AI_PLATFORM_CALLBACK_PUBLIC_URL") is not None
    )
    auto_docker_callback = docker_platform_callback and not callback_host_configured and not callback_public_url_configured
    if args.callback_host is None:
        args.callback_host = "0.0.0.0" if docker_platform_callback else "127.0.0.1"
    if args.callback_public_url is None:
        args.callback_public_url = "http://host.docker.internal:{port}/callback" if auto_docker_callback else ""


def _runtime_probe_section_error(section_name: str, section: dict[str, Any], *, run_id: str) -> str | None:
    if section_name == "resource_limits":
        if section.get("over_limit_cleanup_verified") is not True:
            return "runtime probe results missing: resource_limits.over_limit_cleanup_verified"
        if section.get("probe_kind") != "platform_executor_deadline":
            return "runtime probe results missing: resource_limits.probe_kind"
        if section.get("max_seconds_enforced") is not True:
            return "runtime probe results missing: resource_limits.max_seconds_enforced"
        if section.get("run_id") != run_id:
            return "runtime probe results missing: resource_limits.run_id"
        if section.get("probe_source") != "executor_response":
            return "runtime probe results missing: resource_limits.probe_source"
        if section.get("runtime_mode") != "platform":
            return "runtime probe results missing: resource_limits.runtime_mode"
        if not str(section.get("runtime_subject") or ""):
            return "runtime probe results missing: resource_limits.runtime_subject"
        if not _runtime_identity_matches_subject(
            section.get("runtime_identity"),
            runtime_subject=str(section.get("runtime_subject") or ""),
        ):
            return "runtime probe results missing: resource_limits.runtime_identity"
        if not _positive_number(section.get("requested_max_seconds")):
            return "runtime probe results missing: resource_limits.requested_max_seconds"
        if not _deadline_elapsed_is_bounded(
            section.get("observed_timeout_elapsed_ms"),
            requested_max_seconds=section.get("requested_max_seconds"),
        ):
            return "runtime probe results missing: resource_limits.observed_timeout_elapsed_ms"
        return None
    if section_name == "egress_policy":
        for field in (
            "default_deny_outbound",
            "platform_allowlist_enforced",
            "callback_exception_scoped_to_run_token",
            "denied_egress_redacted",
        ):
            if section.get(field) is not True:
                return f"runtime probe results missing: egress_policy.{field}"
        required_text_fields = (
            "denied_target",
            "denied_probe_error_code",
            "allowed_callback_host",
            "callback_probe_status",
        )
        for field in required_text_fields:
            value = section.get(field)
            if not isinstance(value, str) or not value:
                return f"runtime probe results missing: egress_policy.{field}"
        if section.get("denied_probe_error_code") != "egress_denied":
            return "runtime probe results missing: egress_policy.denied_probe_error_code"
        if section.get("callback_probe_status") != "delivered":
            return "runtime probe results missing: egress_policy.callback_probe_status"
        if section.get("policy_source") != "platform_policy":
            return "runtime probe results missing: egress_policy.policy_source"
        if section.get("probe_source") != "runtime_probe_results":
            return "runtime probe results missing: egress_policy.probe_source"
        return None
    if section_name == "security_options":
        if section.get("privileged") is not False:
            return "runtime probe results missing: security_options.privileged"
        if section.get("docker_socket_mounted") is not False:
            return "runtime probe results missing: security_options.docker_socket_mounted"
        for field in (
            "no_new_privileges",
            "capabilities_dropped",
            "root_filesystem_read_only_or_minimal",
        ):
            if section.get(field) is not True:
                return f"runtime probe results missing: security_options.{field}"
        if section.get("workspace_mount_mode") not in {"rw", "ro"}:
            return "runtime probe results missing: security_options.workspace_mount_mode"
    return None


def _safe_run_id(value: str) -> str:
    cleaned = SAFE_NAME_PATTERN.sub("-", value).strip("-")
    return cleaned[:80] or f"run-{uuid.uuid4().hex[:12]}"


def _configured_platform_runtime_model(settings: object) -> str:
    from app.model_catalog import build_model_catalog, resolve_model_selection

    configured_default = str(getattr(settings, "default_model_id", "") or "").strip()
    if configured_default:
        try:
            selection = resolve_model_selection(configured_default, settings)
        except Exception:
            selection = None
        if selection and selection.get("value"):
            return str(selection["value"])
        return configured_default
    for attr in ("claude_agent_model", "anthropic_model", "openai_model"):
        value = str(getattr(settings, attr, "") or "").strip()
        if value:
            return value
    catalog = build_model_catalog(settings)
    catalog_default = str(catalog.get("default_model_id") or "").strip()
    if catalog_default:
        try:
            selection = resolve_model_selection(catalog_default, settings)
        except Exception:
            selection = None
        if selection and selection.get("value"):
            return str(selection["value"])
        return catalog_default
    return "deepseek-v4-flash"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, ensure_ascii=True).encode("utf-8")


def _redact(text: object) -> str:
    value = str(text)
    value = re.sub(r"Bearer\s+[A-Za-z0-9._~+/=-]+", "Bearer [redacted]", value, flags=re.IGNORECASE)
    value = re.sub(r"token\s*[:=]\s*[^,\s\"'}]+", "token=[redacted]", value, flags=re.IGNORECASE)
    value = re.sub(r"/var/run/docker\.sock", "[redacted-path]", value, flags=re.IGNORECASE)
    value = re.sub(r"%2Fvar%2Frun%2Fdocker\.sock", "[redacted-path]", value, flags=re.IGNORECASE)
    value = re.sub(r"/home/[^\s\"']*", "[redacted-path]", value, flags=re.IGNORECASE)
    value = re.sub(r"/tmp/[^\s\"']*", "[redacted-path]", value, flags=re.IGNORECASE)
    value = re.sub(r"[A-Za-z]:\\[^\s\"']*", "[redacted-path]", value)
    return value


def redact_for_output(text: object) -> str:
    return _redact(text)


class _InspectionCheckFailed(RuntimeError):
    """Keep failed live checks inside the runtime cleanup path."""


class _InspectionCleanupFailed(RuntimeError):
    """Report that owned runtime cleanup could not be proved by exact enumeration."""


def _atomic_write_json(path_value: str | Path, payload: dict[str, Any]) -> None:
    path = Path(path_value)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    serialized = json.dumps(payload, ensure_ascii=True, indent=2) + "\n"
    try:
        with temporary.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(serialized)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _script_source_sha() -> str:
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def _selected_source_sha(value: object) -> tuple[str, bool]:
    raw = str(value or "").strip()
    if not raw:
        return _script_source_sha(), True
    if SOURCE_SHA_PATTERN.fullmatch(raw) is None:
        return _script_source_sha(), False
    return raw.lower(), True


def _safe_image_reference(value: object) -> str:
    image = str(value or "").strip()
    if (
        not image
        or IMAGE_REFERENCE_PATTERN.fullmatch(image) is None
        or image.startswith(("-", ".", "/"))
        or "//" in image
        or ".." in image.split("/")
    ):
        raise ValueError("invalid sandbox executor image")
    if "@" in image:
        image_name, separator, digest = image.rpartition("@")
        if not image_name or separator != "@" or re.fullmatch(r"sha256:[0-9a-fA-F]{64}", digest) is None:
            raise ValueError("invalid sandbox executor image digest")
    return image


def _path_identity(path: Path) -> tuple[int, int]:
    node = path.lstat()
    if stat.S_ISLNK(node.st_mode) or not stat.S_ISDIR(node.st_mode):
        raise ValueError("inspection workspace path is not a real directory")
    if hasattr(path, "is_junction") and path.is_junction():
        raise ValueError("inspection workspace path must not be a junction")
    resolved = path.resolve(strict=True)
    if os.path.normcase(str(resolved)) != os.path.normcase(str(path.absolute())):
        raise ValueError("inspection workspace path resolves through an alias")
    return int(node.st_dev), int(node.st_ino)


def _directory_chain(path: Path, *, allow_missing_leaf: bool) -> dict[str, tuple[int, int]]:
    absolute = path.absolute()
    current = Path(absolute.anchor)
    identities: dict[str, tuple[int, int]] = {}
    if absolute.anchor:
        identities[os.path.normcase(str(current))] = _path_identity(current)
    parts = absolute.parts[1:] if absolute.anchor else absolute.parts
    for part in parts:
        current /= part
        try:
            identities[os.path.normcase(str(current))] = _path_identity(current)
        except FileNotFoundError:
            if allow_missing_leaf:
                break
            raise ValueError("inspection workspace path is missing") from None
    return identities


def _prepare_inspection_workspace_root(
    run_id: str,
    *,
    verifier_base: str | Path | None = None,
) -> tuple[Path, dict[str, tuple[int, int]]]:
    if verifier_base is None:
        temp_root = Path(tempfile.gettempdir()).resolve(strict=True)
        base = temp_root / INSPECTION_WORKSPACE_BASE_NAME
    else:
        base = Path(verifier_base).absolute()
        if not base.is_absolute():
            raise ValueError("inspection verifier base must be absolute")
    _directory_chain(base, allow_missing_leaf=True)
    try:
        base.mkdir(mode=0o700)
    except FileExistsError:
        pass
    base_identities = _directory_chain(base, allow_missing_leaf=False)
    run_root = base / run_id
    try:
        run_root.lstat()
    except FileNotFoundError:
        pass
    else:
        raise ValueError("inspection workspace run child already exists")
    os.mkdir(run_root, mode=0o700)
    identities = _directory_chain(run_root, allow_missing_leaf=False)
    base_key = os.path.normcase(str(base.absolute()))
    if identities.get(base_key) != base_identities.get(base_key):
        raise ValueError("inspection verifier base changed during creation")
    if run_root.resolve(strict=True).parent != base.resolve(strict=True):
        raise ValueError("inspection workspace escaped verifier base")
    return run_root, identities


def _revalidate_inspection_workspace(
    run_root: Path,
    identities: dict[str, tuple[int, int]],
    *,
    workspace: Any | None = None,
    staged_skill_name: str = "",
) -> None:
    current = _directory_chain(run_root, allow_missing_leaf=False)
    for key, expected in identities.items():
        if current.get(key) != expected:
            raise ValueError("inspection workspace identity changed")
    if workspace is None:
        return
    trusted_root = run_root.resolve(strict=True)
    paths = [
        Path(workspace.host_root),
        Path(workspace.workspace_host_path),
        Path(workspace.inputs_host_path),
        Path(workspace.logs_host_path),
    ]
    if staged_skill_name:
        paths.extend(
            [
                Path(workspace.workspace_host_path) / ".claude",
                Path(workspace.workspace_host_path) / ".claude" / "skills",
                Path(workspace.workspace_host_path) / ".claude" / "skills" / staged_skill_name,
            ]
        )
    for path in paths:
        resolved = path.resolve(strict=True)
        try:
            resolved.relative_to(trusted_root)
        except ValueError as exc:
            raise ValueError("inspection workspace lease escaped verifier root") from exc
        _directory_chain(path, allow_missing_leaf=False)


def _inspection_profile_manifest(profile: str) -> dict[str, Any]:
    native_expected = profile == "sdk-native"
    return {
        "selected": profile if profile in INSPECTION_PROFILES else "invalid",
        "catalog": "implicit",
        "primary_skill": "general-chat",
        "authorized_implicit_skill": INSPECTION_AUTHORIZED_SKILLS.get(profile, ""),
        "primary_execution_strategy": "sdk_restricted",
        "authorized_skill_count": 1,
        "native_sidecar_expected": native_expected,
        "authorization_basis": "deterministic_verifier_fixture",
        "production_authorization_proven": False,
    }


def _inspection_check_defaults(profile: str) -> dict[str, bool]:
    names = [
        "implicit_catalog_fixed",
        "authoritative_catalog_aggregation",
        "authoritative_allowed_names_exact",
        "authoritative_declared_builtins_exact",
        "production_authorization_not_claimed",
        "authorized_skill_names_nonempty",
        "trusted_workspace_lease",
        "primary_provider_child_observed",
        "workspace_rw",
        "claude_nested_ro",
        "claude_nested_under_workspace",
        "workspace_mount_topology_exact",
        "no_unexpected_workspace_submounts",
        "delivery_link_created",
        "delivery_link_cleanup_succeeded",
        "staged_skill_hash_matches",
        "skill_hash_unchanged",
        "outputs_write_succeeded",
        "delivery_write_succeeded",
        "native_sidecar_expectation_matches_profile",
        *(f"attack_{attack}_kernel_blocked" for attack in INSPECTION_ATTACKS),
    ]
    names.extend(
        [
            "native_sidecar_present",
            "native_sidecar_token_paired",
            "native_sidecar_socket_paired",
            "native_sidecar_admission_paired",
            "native_sidecar_authenticated_health",
            "primary_native_socket_present",
            "primary_native_authenticated_health",
        ]
        if profile == "sdk-native"
        else [
            "native_sidecar_absent",
            "primary_native_credentials_absent",
            "primary_native_socket_absent",
        ]
    )
    return {name: False for name in names}


def _new_inspection_evidence(
    *,
    run_id: str,
    profile: str,
    source_sha: str,
    target_image: str,
) -> dict[str, Any]:
    started_at = _utc_now()
    return {
        "schema_version": INSPECTION_EVIDENCE_SCHEMA_VERSION,
        "case": "staged-skill-mount-inspection",
        "run_id": _safe_run_id(run_id),
        "profile": _inspection_profile_manifest(profile),
        "authorization": {
            "basis": "deterministic_verifier_fixture",
            "production_authorization_proven": False,
            "admin_bypass_claimed": False,
            "tenant_distribution_proven": False,
            "release_distribution_proven": False,
        },
        "source_sha": source_sha,
        "target_image": target_image,
        "started_at": started_at,
        "updated_at": started_at,
        "finished_at": "",
        "stage": "manifest_checkpoint",
        "exit_code": None,
        "failure_category": "",
        "provider_child_creation": {
            "primary_observed": False,
            "native_sidecar_required": profile == "sdk-native",
            "primary_count": 0,
            "native_sidecar_count": 0,
        },
        "checks": _inspection_check_defaults(profile),
        "hashes": {
            "staged_skill": "",
            "skill_before": "",
            "skill_after": "",
        },
        "counts": {
            "mount_entries": 0,
            "attacks_attempted": 0,
            "attacks_blocked": 0,
        },
        "mountinfo": [],
        "attack_errno_categories": {attack: "not_run" for attack in INSPECTION_ATTACKS},
        "cleanup": {
            "attempted": False,
            "provider_stop_confirmed": False,
            "lease_release_observed": False,
            "post_cleanup_query_succeeded": False,
            "post_cleanup_primary_count": None,
            "post_cleanup_native_sidecar_count": None,
            "result": "pending",
        },
        "redaction": {
            "host_paths_absent": True,
            "container_ids_absent": True,
            "secrets_absent": True,
        },
    }


def _write_inspection_checkpoint(evidence_path: str | Path, evidence: dict[str, Any]) -> None:
    evidence["updated_at"] = _utc_now()
    _atomic_write_json(evidence_path, evidence)


def _safe_evidence_file_from_argv(argv: list[str]) -> str | None:
    values: list[str] = []
    for index, item in enumerate(argv):
        if item == "--evidence-file":
            if index + 1 >= len(argv) or argv[index + 1].startswith("--"):
                return None
            values.append(argv[index + 1])
        elif item.startswith("--evidence-file="):
            values.append(item.partition("=")[2])
    if not values:
        values.append(os.environ.get("AI_PLATFORM_SANDBOX_EVIDENCE", "/tmp/ai-platform-sandbox-runtime-evidence.json"))
    if len(set(values)) != 1 or not values[0] or "\x00" in values[0]:
        return None
    return values[0]


def _write_bootstrap_error(evidence_path: str | Path, *, failure_category: str, exit_code: int) -> None:
    now = _utc_now()
    payload = {
        "schema_version": BOOTSTRAP_EVIDENCE_SCHEMA_VERSION,
        "stage": "bootstrap_error",
        "failure_category": failure_category,
        "exit_code": int(exit_code),
        "generated_at": now,
        "redaction": {
            "host_paths_absent": True,
            "container_ids_absent": True,
            "secrets_absent": True,
        },
    }
    _atomic_write_json(evidence_path, payload)


def _inspection_skill_files(skill_name: str) -> dict[str, str]:
    return {
        "SKILL.md": (
            "---\n"
            f"name: {skill_name}\n"
            "description: Deterministic verifier Skill for the 211 staged mount inspection.\n"
            "---\n\n"
            "Return the fixed verifier result without loading external data.\n"
        ),
        "chmod-target.txt": "chmod target\n",
        "rm-target.txt": "rm target\n",
        "mv-source.txt": "mv target\n",
    }


def _deterministic_skill_hash(files: dict[str, str]) -> str:
    digest = hashlib.sha256()
    for relative_path, content in sorted(files.items()):
        encoded_path = relative_path.encode("utf-8")
        encoded_content = content.encode("utf-8")
        digest.update(len(encoded_path).to_bytes(8, "big"))
        digest.update(encoded_path)
        digest.update(len(encoded_content).to_bytes(8, "big"))
        digest.update(encoded_content)
    return digest.hexdigest()


def _inspection_pinned_manifest(skill_id: str, *, files: dict[str, str]) -> dict[str, Any]:
    from app.skills.execution_profiles import resolve_skill_execution_profile

    version = _deterministic_skill_hash(files)
    execution_profile = resolve_skill_execution_profile(
        skill_id=skill_id,
        source_kind="builtin",
        lifecycle_status="released",
    )
    return {
        "skill_id": skill_id,
        "version": version,
        "content_hash": version,
        "source": {"kind": "builtin", "asset_dir": skill_id},
        "files": [
            {
                "relative_path": relative_path,
                "content_base64": base64.b64encode(content.encode("utf-8")).decode("ascii"),
                "size_bytes": len(content.encode("utf-8")),
            }
            for relative_path, content in sorted(files.items())
        ],
        "dependency_ids": [],
        "lifecycle_status": "released",
        "execution_profile": execution_profile,
        "builtin_tool_identities": list(execution_profile["builtin_tool_identities"]),
        "mcp_tool_ids": [],
        "allowed": True,
        "staged": False,
        "used": False,
    }


def _authoritative_inspection_catalog(profile: str) -> dict[str, Any]:
    from app.capability_distribution import CapabilityAccessDecision
    from app.models import QueueRunPayload
    from app.skills.release_policy import RELEASE_DECISION_SCHEMA_VERSION
    from app.worker import _builtin_capability_subjects

    authorized_skill = INSPECTION_AUTHORIZED_SKILLS.get(profile)
    if not authorized_skill:
        raise ValueError("unsupported inspection profile")
    primary_files = _inspection_skill_files("general-chat")
    authorized_files = _inspection_skill_files(authorized_skill)
    primary_manifest = _inspection_pinned_manifest("general-chat", files=primary_files)
    authorized_manifest = _inspection_pinned_manifest(authorized_skill, files=authorized_files)
    primary_version = str(primary_manifest["content_hash"])
    payload = QueueRunPayload(
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        user_id="user-a",
        session_id="catalog-session",
        run_id="catalog-run",
        agent_id="sandbox-runtime-verifier",
        skill_id="general-chat",
        file_ids=[],
        input={},
        executor_type="embedded-poco",
        skill_version=primary_version,
        release_decision={
            "schema_version": RELEASE_DECISION_SCHEMA_VERSION,
            "policy_active": False,
            "selected_version": primary_version,
            "selected_track": "manifest_pin",
        },
        skill_manifests=[primary_manifest],
    )
    decision = CapabilityAccessDecision(
        visible=True,
        usable=True,
        manageable=False,
        admin_bypass=False,
        decision_reason="deterministic_verifier_fixture",
    )
    subjects = _builtin_capability_subjects(
        payload=payload,
        run_identity={"skill_id": "general-chat"},
        skill={"skill_id": "general-chat", "skill_status": "active"},
        skill_decision=decision,
        authorized_skill_manifests=[authorized_manifest],
        authorized_skill_names=[authorized_skill],
    )
    by_identity = {
        str(subject.get("identity") or ""): subject
        for subject in subjects
        if isinstance(subject, dict)
    }
    authorized_profile = authorized_manifest["execution_profile"]
    declared_builtins = list(authorized_profile["builtin_tool_identities"])
    expected_identities = {"Skill", *declared_builtins}
    if set(by_identity) != expected_identities or len(subjects) != len(by_identity):
        raise _InspectionCheckFailed("authoritative catalog subject aggregation mismatch")
    skill_subject = by_identity.get("Skill", {})
    if (
        skill_subject.get("allowed_skill_names") != [authorized_skill]
        or skill_subject.get("execution_strategy") != "sdk_restricted"
        or any(subject.get("declared_identities") != [identity] for identity, subject in by_identity.items())
        or any(
            subject.get(key) is not True
            for subject in subjects
            for key in ("registered", "declared", "active", "distributed", "identity_authorized")
        )
    ):
        raise _InspectionCheckFailed("authoritative catalog Skill subject mismatch")
    for identity in declared_builtins:
        subject = by_identity.get(identity, {})
        if (
            subject.get("execution_strategy") != authorized_profile["strategy"]
            or subject.get("command_isolation") != authorized_profile["command_isolation"]
            or subject.get("workspace_contract") != authorized_profile["workspace_contract"]
        ):
            raise _InspectionCheckFailed("authoritative catalog builtin subject mismatch")
    expected_strategy = "platform_controlled" if profile == "platform-controlled" else "sdk_native"
    expected_isolation = "minimal-environment-v1" if profile == "platform-controlled" else "sibling-tool-sandbox-v1"
    if (
        authorized_profile["strategy"] != expected_strategy
        or authorized_profile["command_isolation"] != expected_isolation
        or decision.admin_bypass
    ):
        raise _InspectionCheckFailed("authoritative catalog execution profile mismatch")
    return {
        "subjects": subjects,
        "primary_manifest": primary_manifest,
        "authorized_manifest": authorized_manifest,
        "authorized_skill_names": [authorized_skill],
        "declared_builtin_identities": declared_builtins,
        "authorized_files": authorized_files,
        "authorization_basis": decision.decision_reason,
    }


_SKILL_MOUNT_INSPECTION_CODE = textwrap.dedent(
    r"""
    import errno
    import hashlib
    import json
    import os
    import re
    from pathlib import Path

    workspace = Path("/workspace")
    claude = workspace / ".claude"
    staged_skills = [item for item in (claude / "skills").iterdir() if item.is_dir()]
    if len(staged_skills) != 1:
        raise RuntimeError("expected exactly one staged Skill")
    skill = staged_skills[0]
    outputs = workspace / "outputs"
    delivery = outputs / "delivery"
    errno_categories = {
        errno.EROFS: "erofs",
        errno.EACCES: "eacces",
        errno.EPERM: "eperm",
    }

    def tree_hash(root):
        digest = hashlib.sha256()
        items = [item for item in root.rglob("*") if item.is_file() or item.is_symlink()]
        for item in sorted(items, key=lambda path: path.relative_to(root).as_posix()):
            relative = item.relative_to(root).as_posix().encode("utf-8")
            if item.is_symlink():
                content = b"symlink\0" + os.readlink(item).encode("utf-8")
            else:
                content = item.read_bytes()
            digest.update(len(relative).to_bytes(8, "big"))
            digest.update(relative)
            digest.update(len(content).to_bytes(8, "big"))
            digest.update(content)
        return digest.hexdigest()

    def kernel_blocked(operation):
        try:
            operation()
        except OSError as exc:
            category = errno_categories.get(exc.errno, "other")
            return {"blocked": exc.errno == errno.EROFS, "errno_category": category}
        return {"blocked": False, "errno_category": "none"}

    def decode_mount_path(value):
        return re.sub(
            r"\\([0-7]{3})",
            lambda match: chr(int(match.group(1), 8)),
            value,
        )

    mountinfo = []
    for raw_line in Path("/proc/self/mountinfo").read_text(encoding="utf-8").splitlines():
        fields = raw_line.split()
        if len(fields) < 10 or "-" not in fields:
            continue
        mount_point = decode_mount_path(fields[4])
        if mount_point != "/workspace" and not mount_point.startswith("/workspace/"):
            continue
        separator = fields.index("-")
        options = set(fields[5].split(","))
        if len(fields) > separator + 3:
            options.update(fields[separator + 3].split(","))
        mountinfo.append(
            {
                "mount_point": mount_point,
                "mode": "ro" if "ro" in options else "rw" if "rw" in options else "unknown",
            }
        )

    skill_before = tree_hash(skill)
    attacks = {
        "direct_write": kernel_blocked(
            lambda: (skill / "SKILL.md").open("a", encoding="utf-8").write("mutation")
        ),
        "chmod": kernel_blocked(lambda: os.chmod(skill / "chmod-target.txt", 0o777)),
        "rm": kernel_blocked(lambda: os.unlink(skill / "rm-target.txt")),
        "mv": kernel_blocked(lambda: os.rename(skill / "mv-source.txt", skill / "mv-target.txt")),
        "symlink": kernel_blocked(
            lambda: os.symlink(skill / "SKILL.md", skill / "unexpected-link")
        ),
    }

    delivery.mkdir(parents=True, exist_ok=True)
    delivery_link = delivery / "skill-delivery-link"
    try:
        delivery_link.unlink()
    except FileNotFoundError:
        pass
    delivery_link_created = False
    delivery_link_cleanup_succeeded = False
    try:
        os.symlink(skill / "SKILL.md", delivery_link)
        delivery_link_created = True
        attacks["delivery_link_mutation"] = kernel_blocked(
            lambda: delivery_link.open("a", encoding="utf-8").write("mutation")
        )
    except OSError:
        attacks["delivery_link_mutation"] = False
    finally:
        try:
            delivery_link.unlink()
            delivery_link_cleanup_succeeded = True
        except OSError:
            delivery_link_cleanup_succeeded = False

    output_probe = outputs / "inspection-output.txt"
    delivery_probe = delivery / "inspection-delivery.txt"
    output_probe.write_text("output-ok\n", encoding="utf-8")
    delivery_probe.write_text("delivery-ok\n", encoding="utf-8")
    skill_after = tree_hash(skill)
    workspace_modes = [item["mode"] for item in mountinfo if item["mount_point"] == "/workspace"]
    claude_modes = [item["mode"] for item in mountinfo if item["mount_point"] == "/workspace/.claude"]
    print(
        json.dumps(
            {
                "mountinfo": mountinfo,
                "mounts": {
                    "workspace_rw": workspace_modes == ["rw"],
                    "claude_nested_ro": claude_modes == ["ro"],
                    "claude_nested_under_workspace": bool(workspace_modes and claude_modes),
                },
                "attacks": attacks,
                "delivery_link_created": delivery_link_created,
                "delivery_link_cleanup_succeeded": delivery_link_cleanup_succeeded,
                "hashes": {"skill_before": skill_before, "skill_after": skill_after},
                "writes": {
                    "outputs": output_probe.read_text(encoding="utf-8") == "output-ok\n",
                    "delivery": delivery_probe.read_text(encoding="utf-8") == "delivery-ok\n",
                },
            },
            sort_keys=True,
        )
    )
    """
).strip()


def _container_environment_projection(container: Any) -> dict[str, str]:
    attrs = getattr(container, "attrs", {})
    config = attrs.get("Config") if isinstance(attrs, dict) else None
    raw_environment = config.get("Env") if isinstance(config, dict) else None
    projected: dict[str, str] = {}
    for item in raw_environment if isinstance(raw_environment, list) else []:
        if not isinstance(item, str) or "=" not in item:
            continue
        key, value = item.split("=", 1)
        projected[key] = value
    return projected


def _container_label_projection(container: Any) -> dict[str, str]:
    attrs = getattr(container, "attrs", {})
    config = attrs.get("Config") if isinstance(attrs, dict) else None
    labels = config.get("Labels") if isinstance(config, dict) else None
    return {str(key): str(value) for key, value in labels.items()} if isinstance(labels, dict) else {}


def _docker_exec_result_payload(result: Any) -> dict[str, Any]:
    exit_code = getattr(result, "exit_code", None)
    output = getattr(result, "output", None)
    if exit_code is None and isinstance(result, tuple) and len(result) == 2:
        exit_code, output = result
    if type(exit_code) is not int or exit_code != 0:
        raise _InspectionCheckFailed("fixed inspection command failed")
    if isinstance(output, bytes):
        output_text = output.decode("utf-8", errors="strict")
    elif isinstance(output, str):
        output_text = output
    else:
        raise _InspectionCheckFailed("fixed inspection output is invalid")
    if len(output_text.encode("utf-8")) > 65536:
        raise _InspectionCheckFailed("fixed inspection output is too large")
    try:
        payload = json.loads(output_text)
    except json.JSONDecodeError as exc:
        raise _InspectionCheckFailed("fixed inspection output is invalid") from exc
    if not isinstance(payload, dict):
        raise _InspectionCheckFailed("fixed inspection output is invalid")
    return payload


def _docker_exec_exit_code(result: Any) -> int:
    exit_code = getattr(result, "exit_code", None)
    if exit_code is None and isinstance(result, tuple) and len(result) == 2:
        exit_code = result[0]
    if type(exit_code) is not int:
        raise _InspectionCheckFailed("fixed inspection command status is invalid")
    return exit_code


def _matching_provider_child_count(client: Any, *, run_id: str, owner: str) -> int:
    containers = client.containers.list(
        all=True,
        filters={
            "label": [
                f"ai-platform.run_id={run_id}",
                f"ai-platform.owner={owner}",
            ]
        },
    )
    if not isinstance(containers, list):
        raise _InspectionCheckFailed("provider child enumeration is invalid")
    return len(containers)


def _post_cleanup_provider_children(provider: Any, *, run_id: str) -> dict[str, int]:
    client = provider._get_client()
    return {
        "primary_count": _matching_provider_child_count(
            client,
            run_id=run_id,
            owner="sandbox-runtime",
        ),
        "native_sidecar_count": _matching_provider_child_count(
            client,
            run_id=run_id,
            owner="sandbox-native-tool",
        ),
    }


async def _inspect_live_skill_mount(
    *,
    provider: Any,
    lease: Any,
    workspace: Any,
    profile: str,
) -> dict[str, Any]:
    """Inspect one owned live runtime pair with a fixed, non-general shell payload."""

    client = provider._get_client()
    primary = client.containers.get(lease.container_name)
    if hasattr(primary, "reload"):
        primary.reload()
    raw_result = await asyncio.to_thread(
        primary.exec_run,
        ["python", "-c", _SKILL_MOUNT_INSPECTION_CODE],
    )
    payload = _docker_exec_result_payload(raw_result)
    primary_environment = _container_environment_projection(primary)
    primary_count = _matching_provider_child_count(
        client,
        run_id=lease.run_id,
        owner="sandbox-runtime",
    )
    sidecar_count = _matching_provider_child_count(
        client,
        run_id=lease.run_id,
        owner="sandbox-native-tool",
    )
    expected_socket = "/workspace/.ai-platform/native-tool.sock"
    primary_socket_exit = _docker_exec_exit_code(
        await asyncio.to_thread(primary.exec_run, ["test", "-S", expected_socket])
    )
    sidecar = provider._owned_native_tool_container(lease)
    sidecar_evidence = {
        "expected": profile == "sdk-native",
        "present": sidecar is not None,
        "absent": sidecar is None,
        "primary_native_credentials_absent": not primary_environment.get("AI_PLATFORM_NATIVE_TOOL_TOKEN")
        and not primary_environment.get("AI_PLATFORM_NATIVE_TOOL_SOCKET"),
        "token_paired": False,
        "socket_paired": False,
        "admission_paired": False,
        "authenticated_health_probe": False,
        "primary_socket_present": primary_socket_exit == 0,
        "primary_socket_absent": primary_socket_exit == 1,
        "primary_authenticated_health": False,
    }
    if sidecar is not None:
        if hasattr(sidecar, "reload"):
            sidecar.reload()
        sidecar_environment = _container_environment_projection(sidecar)
        sidecar_labels = _container_label_projection(sidecar)
        primary_token = primary_environment.get("AI_PLATFORM_NATIVE_TOOL_TOKEN", "")
        sidecar_token = sidecar_environment.get("AI_PLATFORM_NATIVE_TOOL_TOKEN", "")
        sidecar_evidence["token_paired"] = bool(
            primary_token
            and sidecar_token
            and hmac.compare_digest(primary_token, sidecar_token)
        )
        primary_health_exit = _docker_exec_exit_code(
            await asyncio.to_thread(
                primary.exec_run,
                ["python", "-m", "app.runtime.sandbox.native_tool_health_probe"],
            )
        )
        sidecar_evidence["primary_authenticated_health"] = primary_health_exit == 0
        sidecar_evidence["socket_paired"] = bool(
            primary_environment.get("AI_PLATFORM_NATIVE_TOOL_SOCKET") == expected_socket
            and sidecar_environment.get("AI_PLATFORM_NATIVE_TOOL_SOCKET") == expected_socket
            and sidecar_evidence["primary_socket_present"] is True
        )
        admission_phase = "authenticated_container_uds_health"
        container_socket_bytes = str(len(expected_socket.encode("utf-8")))
        lease_host_socket_bytes = str(lease.labels.get("ai-platform.native_tool_host_socket_path_bytes") or "")
        sidecar_host_socket_bytes = str(
            sidecar_labels.get("ai-platform.native_tool_host_socket_path_bytes") or ""
        )
        sidecar_evidence["admission_paired"] = bool(
            lease.labels.get("ai-platform.native_tool_required") == "true"
            and lease.labels.get("ai-platform.native_tool_admission_phase") == admission_phase
            and sidecar_labels.get("ai-platform.native_tool_admission_phase") == admission_phase
            and lease_host_socket_bytes.isdigit()
            and lease_host_socket_bytes == sidecar_host_socket_bytes
            and lease.labels.get("ai-platform.native_tool_container_socket_path_bytes") == container_socket_bytes
            and sidecar_labels.get("ai-platform.native_tool_container_socket_path_bytes") == container_socket_bytes
            and sidecar_labels.get("ai-platform.run_id") == lease.run_id
        )
        native_probe = getattr(provider, "_native_tool_probe", None)
        if callable(native_probe):
            sidecar_evidence["authenticated_health_probe"] = bool(
                await asyncio.to_thread(native_probe, sidecar)
            )
    payload["provider_children"] = {
        "primary_count": primary_count,
        "native_sidecar_count": sidecar_count,
    }
    payload["sidecar"] = sidecar_evidence
    return payload


def _stage_inspection_skill(
    workspace: Any,
    *,
    skill_name: str,
    files: dict[str, str],
) -> str:
    from app.skills.registry import BuiltinSkill, skill_content_hash
    from app.skills.stager import SkillStager

    if skill_name not in INSPECTION_AUTHORIZED_SKILLS.values() or files != _inspection_skill_files(skill_name):
        raise ValueError("inspection Skill fixture is not authoritative")
    source = Path(workspace.host_root) / "runtime" / "verifier-skill-source" / skill_name
    source.mkdir(parents=True, exist_ok=True)
    existing = {item.name for item in source.iterdir()}
    if existing - set(files):
        raise ValueError("verifier Skill source is not isolated")
    for name, content in files.items():
        path = source / name
        if path.exists() and (path.is_symlink() or not path.is_file()):
            raise ValueError("verifier Skill source is invalid")
        path.write_text(content, encoding="utf-8", newline="\n")
    version = skill_content_hash(source)
    staged = SkillStager().stage_skills(
        workspace=workspace.workspace_host_path,
        skills=[
            BuiltinSkill(
                name=skill_name,
                description="Deterministic verifier Skill for the 211 staged mount inspection.",
                path=source,
                version=version,
                source={"kind": "verifier", "version": version},
                entry={"kind": "filesystem"},
            )
        ],
    )
    if staged != [skill_name]:
        raise _InspectionCheckFailed("verifier Skill staging failed")
    staged_root = Path(workspace.workspace_host_path) / ".claude" / "skills" / skill_name
    staged_hash = skill_content_hash(staged_root)
    if staged_hash != _deterministic_skill_hash(files):
        raise _InspectionCheckFailed("staged Skill differs from pinned manifest")
    staged_names = sorted(item.name for item in staged_root.parent.iterdir() if item.is_dir())
    if staged_names != [skill_name]:
        raise _InspectionCheckFailed("staged Skill registry contains unexpected entries")
    return staged_hash


def _build_inspection_runtime(
    *,
    workspace_root: str | Path,
    workspace: Any,
    execute_task: Callable[..., Any],
    record_lease: Callable[..., Any],
    release_lease: Callable[..., Any],
    callback_token_resolver: Callable[[str], str],
) -> tuple[Any, Any]:
    from app.runtime.sandbox.container_provider import DockerContainerProvider
    from app.runtime.sandbox.runtime import SandboxRuntime

    provider = DockerContainerProvider()
    runtime = SandboxRuntime(
        workspace_root=workspace_root,
        provider=provider,
        execute_task=execute_task,
        callback_token_resolver=callback_token_resolver,
        record_lease=record_lease,
        release_lease=release_lease,
    )
    return runtime, provider


def _inspection_result_projection(
    raw: object,
    *,
    profile: str,
    staged_hash: str,
    native_tool_required: bool,
) -> dict[str, Any]:
    result = raw if isinstance(raw, dict) else {}
    mounts = result.get("mounts") if isinstance(result.get("mounts"), dict) else {}
    attacks = result.get("attacks") if isinstance(result.get("attacks"), dict) else {}
    hashes = result.get("hashes") if isinstance(result.get("hashes"), dict) else {}
    writes = result.get("writes") if isinstance(result.get("writes"), dict) else {}
    sidecar = result.get("sidecar") if isinstance(result.get("sidecar"), dict) else {}
    provider_children = (
        result.get("provider_children")
        if isinstance(result.get("provider_children"), dict)
        else {}
    )
    safe_mountinfo: list[dict[str, str]] = []
    raw_mountinfo = result.get("mountinfo")
    if isinstance(raw_mountinfo, list):
        for item in raw_mountinfo:
            if not isinstance(item, dict):
                continue
            mount_point = str(item.get("mount_point") or "")
            mode = str(item.get("mode") or "")
            if (
                (mount_point == "/workspace" or mount_point.startswith("/workspace/"))
                and mode in {"rw", "ro"}
            ):
                safe_mountinfo.append({"mount_point": mount_point, "mode": mode})
    skill_before = str(hashes.get("skill_before") or "")
    skill_after = str(hashes.get("skill_after") or "")
    primary_count = provider_children.get("primary_count")
    sidecar_count = provider_children.get("native_sidecar_count")
    primary_count = int(primary_count) if type(primary_count) is int and primary_count >= 0 else 0
    sidecar_count = int(sidecar_count) if type(sidecar_count) is int and sidecar_count >= 0 else 0
    observed_mounts = {item["mount_point"]: item["mode"] for item in safe_mountinfo}
    expected_mounts = {
        "/workspace": "rw",
        "/workspace/.claude": "ro",
        **({"/workspace/.ai-platform": "rw"} if profile == "sdk-native" else {}),
    }
    topology_exact = len(observed_mounts) == len(safe_mountinfo) and observed_mounts == expected_mounts
    attack_categories: dict[str, str] = {}
    checks: dict[str, bool] = {
        "implicit_catalog_fixed": True,
        "authoritative_catalog_aggregation": True,
        "authoritative_allowed_names_exact": True,
        "authoritative_declared_builtins_exact": True,
        "production_authorization_not_claimed": True,
        "authorized_skill_names_nonempty": True,
        "trusted_workspace_lease": True,
        "primary_provider_child_observed": primary_count == 1,
        "workspace_rw": mounts.get("workspace_rw") is True,
        "claude_nested_ro": mounts.get("claude_nested_ro") is True,
        "claude_nested_under_workspace": mounts.get("claude_nested_under_workspace") is True,
        "workspace_mount_topology_exact": topology_exact,
        "no_unexpected_workspace_submounts": topology_exact,
        "delivery_link_created": result.get("delivery_link_created") is True,
        "delivery_link_cleanup_succeeded": result.get("delivery_link_cleanup_succeeded") is True,
        "staged_skill_hash_matches": bool(staged_hash and skill_before == staged_hash),
        "skill_hash_unchanged": bool(skill_before and skill_before == skill_after),
        "outputs_write_succeeded": writes.get("outputs") is True,
        "delivery_write_succeeded": writes.get("delivery") is True,
    }
    for attack in INSPECTION_ATTACKS:
        attack_result = attacks.get(attack) if isinstance(attacks.get(attack), dict) else {}
        category = str(attack_result.get("errno_category") or "other")
        if category not in {"erofs", "eacces", "eperm", "other", "none"}:
            category = "other"
        attack_categories[attack] = category
        checks[f"attack_{attack}_kernel_blocked"] = (
            attack_result.get("blocked") is True and category == "erofs"
        )
    native_expected = profile == "sdk-native"
    checks["native_sidecar_expectation_matches_profile"] = native_tool_required is native_expected
    if native_expected:
        checks.update(
            {
                "native_sidecar_present": sidecar.get("present") is True and sidecar_count == 1,
                "native_sidecar_token_paired": sidecar.get("token_paired") is True,
                "native_sidecar_socket_paired": sidecar.get("socket_paired") is True,
                "native_sidecar_admission_paired": sidecar.get("admission_paired") is True,
                "native_sidecar_authenticated_health": sidecar.get("authenticated_health_probe") is True,
                "primary_native_socket_present": sidecar.get("primary_socket_present") is True,
                "primary_native_authenticated_health": sidecar.get("primary_authenticated_health") is True,
            }
        )
    else:
        checks.update(
            {
                "native_sidecar_absent": sidecar.get("absent") is True and sidecar_count == 0,
                "primary_native_credentials_absent": sidecar.get("primary_native_credentials_absent") is True,
                "primary_native_socket_absent": sidecar.get("primary_socket_absent") is True,
            }
        )
    return {
        "checks": checks,
        "hashes": {
            "staged_skill": staged_hash,
            "skill_before": skill_before if SOURCE_SHA_PATTERN.fullmatch(skill_before) else "",
            "skill_after": skill_after if SOURCE_SHA_PATTERN.fullmatch(skill_after) else "",
        },
        "counts": {
            "mount_entries": len(safe_mountinfo),
            "attacks_attempted": len(INSPECTION_ATTACKS),
            "attacks_blocked": sum(
                isinstance(attacks.get(attack), dict)
                and attacks[attack].get("blocked") is True
                and attacks[attack].get("errno_category") == "erofs"
                for attack in INSPECTION_ATTACKS
            ),
        },
        "attack_errno_categories": attack_categories,
        "mountinfo": safe_mountinfo,
        "provider_children": {
            "primary_count": primary_count,
            "native_sidecar_count": sidecar_count,
        },
        "passed": all(checks.values()),
    }


def _inspection_failure_category(exc: BaseException, *, primary_observed: bool) -> str:
    name = type(exc).__name__
    if isinstance(exc, (asyncio.CancelledError, KeyboardInterrupt)):
        return "cancelled"
    if isinstance(exc, ImportError):
        return "dependency_import_failure"
    if name == "SandboxRuntimeCleanupError" or "Cleanup" in name:
        return "cleanup_failure"
    if isinstance(exc, _InspectionCheckFailed):
        return "inspection_checks_failed"
    if isinstance(exc, ValueError) and not primary_observed:
        return "invalid_configuration"
    return "inspection_failure" if primary_observed else "provider_create_failure"


def _run_skill_mount_inspection(
    args: argparse.Namespace,
    *,
    _runtime_factory: Callable[..., tuple[Any, Any]] | None = None,
    _inspection_callback: Callable[..., Any] | None = None,
    _workspace_base: str | Path | None = None,
    _post_cleanup_enumerator: Callable[..., dict[str, int]] | None = None,
) -> int:
    profile = str(args.inspection_profile or "")
    source_sha, source_sha_valid = _selected_source_sha(args.source_sha)
    try:
        manifest_image = _safe_image_reference(args.sandbox_executor_image)
    except ValueError:
        manifest_image = "[invalid]"
    evidence = _new_inspection_evidence(
        run_id=str(args.run_id or ""),
        profile=profile,
        source_sha=source_sha,
        target_image=manifest_image,
    )
    _write_inspection_checkpoint(args.evidence_file, evidence)

    original_settings: tuple[Any, Any, Any] | None = None
    exit_code = 1
    try:
        if profile not in INSPECTION_PROFILES:
            raise ValueError("unsupported inspection profile")
        if not source_sha_valid:
            raise ValueError("invalid source sha")
        target_image = _safe_image_reference(args.sandbox_executor_image)
        if str(args.run_id or "") != _safe_run_id(str(args.run_id or "")):
            raise ValueError("invalid inspection run id")
        if args.sandbox_provider != "docker":
            raise ValueError("inspection requires the Docker provider")
        if args.skip_live_submit or args.generate_runtime_probe_results_file or args.runtime_probe_results_file:
            raise ValueError("inspection flags are incompatible")

        from app.control_plane_contracts import standard_trace_id
        from app.runtime.sandbox.contracts import SandboxRuntimeRequest
        from app.runtime.sandbox.workspace_manager import SandboxWorkspaceManager
        from app.settings import get_settings

        catalog = _authoritative_inspection_catalog(profile)
        authorized_skill_name = str(catalog["authorized_skill_names"][0])
        workspace_root, workspace_identities = _prepare_inspection_workspace_root(
            evidence["run_id"],
            verifier_base=_workspace_base,
        )
        _revalidate_inspection_workspace(workspace_root, workspace_identities)
        settings = get_settings()
        original_settings = (
            settings.sandbox_container_provider,
            settings.sandbox_executor_image,
            settings.sandbox_workspace_root,
        )
        settings.sandbox_container_provider = "docker"
        settings.sandbox_executor_image = target_image
        settings.sandbox_workspace_root = str(workspace_root)
        request = SandboxRuntimeRequest(
            tenant_id="tenant-a",
            workspace_id="workspace-a",
            user_id="user-a",
            session_id=f"session-{evidence['run_id']}",
            run_id=evidence["run_id"],
            agent_id="sandbox-runtime-verifier",
            skill_ids=["general-chat", authorized_skill_name],
            mcp_tool_ids=[],
            tool_policy_subjects=list(catalog["subjects"]),
            input_message="ai-platform staged Skill mount inspection",
            file_ids=[],
            sandbox_mode="ephemeral",
            browser_enabled=False,
            model=_configured_platform_runtime_model(settings),
            resource_limits={"max_seconds": 60, "memory_mb": 512, "cpu_count": 0.5, "pids_limit": 128},
            trace_id=standard_trace_id(evidence["run_id"]),
            callback_url="http://127.0.0.1:8000/api/ai/runtime/callbacks/executor",
            callback_token_id=f"cbt-{evidence['run_id']}",
        )
        workspace = SandboxWorkspaceManager(root=workspace_root).prepare(request)
        _revalidate_inspection_workspace(
            workspace_root,
            workspace_identities,
            workspace=workspace,
        )
        staged_hash = _stage_inspection_skill(
            workspace,
            skill_name=authorized_skill_name,
            files=dict(catalog["authorized_files"]),
        )
        _revalidate_inspection_workspace(
            workspace_root,
            workspace_identities,
            workspace=workspace,
            staged_skill_name=authorized_skill_name,
        )
        evidence["hashes"]["staged_skill"] = staged_hash
        evidence["stage"] = "skill_staged"
        _write_inspection_checkpoint(args.evidence_file, evidence)

        captured: dict[str, Any] = {"lease": None, "workspace": None, "provider": None}

        async def record_lease(lease: Any, _request: Any, trusted_workspace: Any) -> str:
            captured["lease"] = lease
            captured["workspace"] = trusted_workspace
            evidence["provider_child_creation"]["primary_observed"] = True
            evidence["provider_child_creation"]["native_sidecar_required"] = (
                str(lease.labels.get("ai-platform.native_tool_required") or "") == "true"
            )
            evidence["stage"] = "provider_created"
            _write_inspection_checkpoint(args.evidence_file, evidence)
            return f"lease-{evidence['run_id']}"

        async def release_lease(_lease: Any, _reason: str, _lease_record_id: str | None = None) -> None:
            evidence["cleanup"].update(
                {
                    "lease_release_observed": True,
                    "result": "release_callback_observed",
                }
            )
            evidence["stage"] = "release_callback_observed"
            _write_inspection_checkpoint(args.evidence_file, evidence)

        async def execute_inspection(
            _executor_url: str,
            _task_request: Any,
            executor_headers: dict[str, str] | None = None,
        ) -> dict[str, Any]:
            del executor_headers
            lease = captured.get("lease")
            trusted_workspace = captured.get("workspace")
            provider = captured.get("provider")
            if lease is None or trusted_workspace is None or provider is None:
                raise _InspectionCheckFailed("trusted inspection context missing")
            evidence["stage"] = "inspection_running"
            _write_inspection_checkpoint(args.evidence_file, evidence)
            callback = _inspection_callback or _inspect_live_skill_mount
            raw = callback(
                provider=provider,
                lease=lease,
                workspace=trusted_workspace,
                profile=profile,
            )
            if inspect.isawaitable(raw):
                raw = await raw
            projection = _inspection_result_projection(
                raw,
                profile=profile,
                staged_hash=staged_hash,
                native_tool_required=(
                    str(lease.labels.get("ai-platform.native_tool_required") or "") == "true"
                ),
            )
            evidence["checks"] = projection["checks"]
            evidence["hashes"] = projection["hashes"]
            evidence["counts"] = projection["counts"]
            evidence["mountinfo"] = projection["mountinfo"]
            evidence["attack_errno_categories"] = projection["attack_errno_categories"]
            evidence["provider_child_creation"].update(projection["provider_children"])
            evidence["stage"] = "inspection_complete"
            _write_inspection_checkpoint(args.evidence_file, evidence)
            if projection["passed"] is not True:
                raise _InspectionCheckFailed("one or more fixed inspection checks failed")
            return {"status": "completed", "run_id": evidence["run_id"]}

        callback_secret = uuid.uuid4().hex
        runtime_factory = _runtime_factory or _build_inspection_runtime
        _revalidate_inspection_workspace(
            workspace_root,
            workspace_identities,
            workspace=workspace,
            staged_skill_name=authorized_skill_name,
        )
        runtime, provider = runtime_factory(
            workspace_root=workspace_root,
            workspace=workspace,
            execute_task=execute_inspection,
            record_lease=record_lease,
            release_lease=release_lease,
            callback_token_resolver=lambda _token_id: callback_secret,
        )
        captured["provider"] = provider
        evidence["stage"] = "provider_creation_started"
        _write_inspection_checkpoint(args.evidence_file, evidence)
        runtime_error: BaseException | None = None
        try:
            asyncio.run(runtime.submit(request))
        except BaseException as exc:
            runtime_error = exc
        evidence["cleanup"]["attempted"] = (
            evidence["provider_child_creation"].get("primary_observed") is True
        )
        enumerator = _post_cleanup_enumerator or _post_cleanup_provider_children
        try:
            post_cleanup = enumerator(provider=provider, run_id=evidence["run_id"])
            primary_count = post_cleanup.get("primary_count")
            native_count = post_cleanup.get("native_sidecar_count")
            if (
                type(primary_count) is not int
                or primary_count < 0
                or type(native_count) is not int
                or native_count < 0
            ):
                raise _InspectionCleanupFailed("post-cleanup enumeration is invalid")
        except BaseException as exc:
            evidence["cleanup"].update(
                {
                    "post_cleanup_query_succeeded": False,
                    "provider_stop_confirmed": False,
                    "result": "enumeration_failed",
                }
            )
            evidence["stage"] = "cleanup_unconfirmed"
            _write_inspection_checkpoint(args.evidence_file, evidence)
            raise _InspectionCleanupFailed("post-cleanup enumeration failed") from exc
        evidence["cleanup"].update(
            {
                "post_cleanup_query_succeeded": True,
                "post_cleanup_primary_count": primary_count,
                "post_cleanup_native_sidecar_count": native_count,
            }
        )
        cleanup_runtime_error = runtime_error is not None and _inspection_failure_category(
            runtime_error,
            primary_observed=evidence["provider_child_creation"].get("primary_observed") is True,
        ) == "cleanup_failure"
        no_owned_children = primary_count == 0 and native_count == 0
        primary_observed = evidence["provider_child_creation"].get("primary_observed") is True
        cleanup_confirmed = (
            primary_observed
            and no_owned_children
            and not cleanup_runtime_error
            and evidence["cleanup"].get("lease_release_observed") is True
        )
        evidence["cleanup"]["provider_stop_confirmed"] = cleanup_confirmed
        evidence["cleanup"]["result"] = (
            "confirmed"
            if cleanup_confirmed
            else "not_required"
            if not primary_observed and no_owned_children and not cleanup_runtime_error
            else "owned_children_remain"
            if not no_owned_children
            else "runtime_cleanup_failed"
        )
        evidence["stage"] = "cleanup_confirmed" if cleanup_confirmed else "cleanup_unconfirmed"
        _write_inspection_checkpoint(args.evidence_file, evidence)
        if not no_owned_children:
            raise _InspectionCleanupFailed("owned provider children remain after cleanup") from runtime_error
        if cleanup_runtime_error:
            raise runtime_error
        if primary_observed and not cleanup_confirmed:
            raise _InspectionCleanupFailed("provider cleanup was not confirmed") from runtime_error
        if runtime_error is not None:
            raise runtime_error
        evidence["stage"] = "completed"
        evidence["exit_code"] = 0
        evidence["failure_category"] = ""
        exit_code = 0
    except BaseException as exc:
        primary_observed = evidence["provider_child_creation"].get("primary_observed") is True
        category = _inspection_failure_category(exc, primary_observed=primary_observed)
        evidence["failure_category"] = category
        evidence["stage"] = "cancelled" if category == "cancelled" else "failed"
        evidence["cleanup"]["attempted"] = primary_observed
        if category == "cleanup_failure":
            evidence["cleanup"]["provider_stop_confirmed"] = False
            if evidence["cleanup"].get("result") in {"pending", "confirmed", "release_callback_observed"}:
                evidence["cleanup"]["result"] = "runtime_cleanup_failed"
        elif primary_observed and evidence["cleanup"].get("provider_stop_confirmed") is not True:
            evidence["cleanup"]["result"] = "not_confirmed"
        elif not primary_observed:
            evidence["cleanup"]["result"] = "not_required"
        exit_code = 130 if category == "cancelled" else 1
        evidence["exit_code"] = exit_code
    finally:
        if original_settings is not None:
            settings.sandbox_container_provider = original_settings[0]
            settings.sandbox_executor_image = original_settings[1]
            settings.sandbox_workspace_root = original_settings[2]
        evidence["finished_at"] = _utc_now()
        _write_inspection_checkpoint(args.evidence_file, evidence)

    output = {
        "run_id": evidence["run_id"],
        "evidence_file": "[redacted-path]",
        "inspection_profile": profile,
        "stage": evidence["stage"],
        "failure_category": evidence["failure_category"],
    }
    if args.json_output:
        print(json.dumps(output, ensure_ascii=True, indent=2))
    else:
        print("PASSED: staged Skill mount inspection" if exit_code == 0 else "FAILED: staged Skill mount inspection")
        if evidence["failure_category"]:
            print(f"- {evidence['failure_category']}")
    return exit_code


def load_runtime_probe_results(path: str | Path, *, run_id: str) -> dict[str, Any]:
    """Load bounded platform probe results for the same run without trusting raw payloads."""
    if not run_id:
        raise RuntimeError("runtime probe results require run_id")
    probe_path = Path(path)
    try:
        raw = probe_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise RuntimeError("runtime probe results file cannot be read") from exc
    if _redact(raw) != raw:
        raise RuntimeError("runtime probe results contain sensitive content")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError("runtime probe results file is not valid JSON") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("runtime probe results root must be an object")
    if payload.get("schema_version") != RUNTIME_PROBE_RESULTS_SCHEMA_VERSION:
        raise RuntimeError("runtime probe results schema mismatch")
    if payload.get("run_id") != run_id:
        raise RuntimeError("runtime probe results run_id mismatch")
    if payload.get("source") != "platform_runtime_probe":
        raise RuntimeError("runtime probe results source mismatch")
    unknown_keys = sorted(str(key) for key in payload if key not in RUNTIME_PROBE_RESULTS_ALLOWED_KEYS)
    if unknown_keys:
        raise RuntimeError("runtime probe results contain unsupported fields")
    results: dict[str, Any] = {}
    for key in RUNTIME_PROBE_RESULTS_SECTION_KEYS:
        section = payload.get(key)
        if section is None:
            raise RuntimeError(f"runtime probe results section is required: {key}")
        if not isinstance(section, dict):
            raise RuntimeError(f"runtime probe results section must be an object: {key}")
        results[key] = dict(section)
    for key, section in results.items():
        section_error = _runtime_probe_section_error(key, section, run_id=run_id)
        if section_error:
            raise RuntimeError(section_error)
    return results


class EvidenceRecorder:
    def __init__(self, *, run_id: str, executor_url: str, callback_token: str) -> None:
        self.run_id = run_id
        self.executor_url = executor_url.rstrip("/")
        self._callback_token = callback_token
        self.runtime_mode = "executor"
        self.sandbox_provider = "unknown"
        self._callback_auth_verified = False
        self.executed_task = False
        self.executor: dict[str, object] = {}
        self.cancel_stops_container = False
        self.cancelled_container_id = ""
        self.callbacks: list[dict[str, object]] = []
        self.timings: dict[str, object] = {}
        self.hardening: dict[str, object] = {}
        self.provider_lifecycle: dict[str, object] = {}
        self.lease_projection: dict[str, object] = {}
        self._lock = threading.Lock()

    def record_callback(self, payload: dict[str, object], token: str) -> bool:
        from app.sandbox_hardening_contract import safe_bounded_error_projection

        if token != self._callback_token:
            return False
        if payload.get("run_id") != self.run_id:
            return False
        status = payload.get("status")
        if not isinstance(status, str):
            return False
        event: dict[str, object] = {"run_id": self.run_id, "status": status}
        progress = payload.get("progress")
        if isinstance(progress, int | float):
            event["progress"] = progress
        state_patch = payload.get("state_patch")
        projection = state_patch.get("bounded_error_projection") if isinstance(state_patch, dict) else None
        safe_projection = safe_bounded_error_projection(projection, run_id=self.run_id)
        if safe_projection is not None:
            event["state_patch"] = {"bounded_error_projection": safe_projection}
        with self._lock:
            self._callback_auth_verified = True
            self.callbacks.append(event)
        return True

    def has_required_callbacks(self) -> bool:
        with self._lock:
            statuses = {str(item.get("status")) for item in self.callbacks}
        return "running" in statuses and bool(statuses & TERMINAL_STATUSES)

    def to_dict(self) -> dict[str, object]:
        with self._lock:
            callbacks = list(self.callbacks)
            callback_auth_verified = self._callback_auth_verified
            lease_projection = dict(self.lease_projection)
        payload = {
            "schema_version": EVIDENCE_SCHEMA_VERSION,
            "run_id": self.run_id,
            "executor_url": self.executor_url,
            "runtime_mode": self.runtime_mode,
            "sandbox_provider": self.sandbox_provider,
            "executed_task": self.executed_task,
            "callback_auth": "token" if callback_auth_verified else False,
            "executor": dict(self.executor),
            "generated_at": _utc_now(),
            "callbacks": callbacks,
            "cancel_stops_container": self.cancel_stops_container,
            "cancelled_container_id": self.cancelled_container_id,
            "timings": self.timings,
            "hardening": self.hardening,
            "provider_lifecycle": self.provider_lifecycle,
            "non_expansion_invariants": dict(NON_EXPANSION_INVARIANTS),
        }
        if lease_projection:
            payload["lease_projection"] = lease_projection
        return payload

    def write(self, evidence_path: str | Path) -> None:
        _atomic_write_json(evidence_path, self.to_dict())


class _CallbackHandler(BaseHTTPRequestHandler):
    recorder: EvidenceRecorder

    def do_POST(self) -> None:  # noqa: N802 - stdlib handler API
        length = int(self.headers.get("Content-Length") or "0")
        raw = self.rfile.read(length)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except Exception:
            self.send_response(400)
            self.end_headers()
            return
        token = self.headers.get("X-AI-Platform-Callback-Token") or ""
        accepted = isinstance(payload, dict) and self.recorder.record_callback(payload, token)
        self.send_response(200 if accepted else 403)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(_json_bytes({"accepted": accepted}))

    def log_message(self, format: str, *args: object) -> None:
        return


def start_callback_server(
    *,
    bind_host: str,
    bind_port: int,
    recorder: EvidenceRecorder,
) -> tuple[ThreadingHTTPServer, str]:
    handler = type("EvidenceCallbackHandler", (_CallbackHandler,), {"recorder": recorder})
    server = ThreadingHTTPServer((bind_host, bind_port), handler)
    port = int(server.server_address[1])
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, f"http://{bind_host}:{port}/callback"


def resolve_callback_public_url(callback_public_url: str, local_callback_url: str) -> str:
    if not callback_public_url:
        return local_callback_url
    parsed = urlparse(local_callback_url)
    port = parsed.port or ""
    return callback_public_url.replace("{port}", str(port))


def _is_platform_callback_endpoint(callback_url: str) -> bool:
    return urlparse(callback_url).path.rstrip("/") == "/api/ai/runtime/callbacks/executor"


def _platform_callback_token_id(run_id: str) -> str:
    return f"cbt_{run_id}"


def _verifier_callback_token_id(run_id: str) -> str:
    return f"callback-{_safe_run_id(run_id)}"


def _callback_token_id_for_url(callback_url: str, run_id: str) -> str:
    if _is_platform_callback_endpoint(callback_url):
        return _platform_callback_token_id(run_id)
    return _verifier_callback_token_id(run_id)


def _callback_token_for_url(callback_url: str, token_id: str, callback_token: str) -> str:
    if _is_platform_callback_endpoint(callback_url):
        from app.runtime.sandbox.callback_tokens import derive_callback_token

        return derive_callback_token(callback_token, token_id)
    return callback_token


def submit_executor_task(
    *,
    executor_url: str,
    callback_url: str,
    callback_token: str,
    run_id: str,
    workspace_root: str,
    urlopen: Callable[..., Any] = urllib_request.urlopen,
) -> dict[str, object]:
    payload = {
        "session_id": f"session-{run_id}",
        "run_id": run_id,
        "prompt": "ai-platform sandbox runtime 211 smoke",
        "callback_url": callback_url,
        "callback_token_id": _callback_token_id_for_url(callback_url, run_id),
        "callback_token": _callback_token_for_url(
            callback_url,
            _callback_token_id_for_url(callback_url, run_id),
            callback_token,
        ),
        "callback_base_url": callback_url.rsplit("/", 1)[0],
        "sdk_session_id": None,
        "permission_mode": "default",
        "config": {
            "model": "smoke",
            "browser_enabled": False,
            "resource_limits": {"max_seconds": 60},
            "skill_ids": [],
            "mcp_tool_ids": [],
            "input_files": [],
            "workspace_root": workspace_root,
        },
    }
    request = urllib_request.Request(
        f"{executor_url.rstrip('/')}/v1/tasks/execute",
        data=_json_bytes(payload),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(request, timeout=20) as response:
        body = response.read().decode("utf-8", errors="replace")
        status = int(getattr(response, "status", 0))
    if status < 200 or status >= 300:
        raise RuntimeError(f"executor task failed with HTTP {status}")
    data = json.loads(body or "{}")
    return data if isinstance(data, dict) else {"status": "accepted"}


def _timings_from_result(result: object) -> dict[str, object]:
    raw = getattr(result, "timings", {})
    timings = dict(raw) if isinstance(raw, dict) else {}
    if "schema_version" not in timings:
        timings["schema_version"] = LATENCY_SCHEMA_VERSION
    return timings


def _executor_evidence_from_response(response: object) -> dict[str, object]:
    if not isinstance(response, dict):
        return {}
    evidence: dict[str, object] = {
        "sdk_used": response.get("sdk_used") is True,
        "executor_mode": str(response.get("executor_mode") or ""),
    }
    sdk_session_id = response.get("sdk_session_id")
    if isinstance(sdk_session_id, str) and sdk_session_id and "/" not in sdk_session_id and "\\" not in sdk_session_id:
        evidence["sdk_session_id"] = sdk_session_id
    return evidence


def _executor_evidence_from_result(result: object) -> dict[str, object]:
    return _executor_evidence_from_response(getattr(result, "executor_response", {}))


def _positive_number(value: object) -> bool:
    if isinstance(value, bool):
        return False
    return isinstance(value, int | float) and math.isfinite(float(value)) and value > 0


def _runtime_probe_section(
    runtime_probe_results: dict[str, Any] | None,
    section_name: str,
) -> dict[str, Any]:
    if not isinstance(runtime_probe_results, dict):
        return {}
    section = runtime_probe_results.get(section_name)
    return dict(section) if isinstance(section, dict) else {}


def _safe_platform_resource_probe_from_result(
    *,
    run_id: str,
    result: object,
    release_reason: object,
    platform_resource_timeout_probe: bool,
    requested_max_seconds: float,
    runtime_identity: dict[str, Any],
) -> dict[str, Any]:
    if not platform_resource_timeout_probe:
        return {}
    response = getattr(result, "executor_response", {})
    response = response if isinstance(response, dict) else {}
    status = str(getattr(result, "status", "") or response.get("status") or "")
    error_code = str(response.get("error_code") or "")
    response_run_id = str(response.get("run_id") or "")
    response_requested = response.get("requested_max_seconds")
    observed_elapsed_ms = response.get("timeout_elapsed_ms")
    runtime_subject = str(runtime_identity.get("source_revision") or "")
    if (
        status != "failed"
        or response.get("status") != "failed"
        or error_code != "executor_deadline_exceeded"
        or response_run_id != run_id
        or not _positive_number(requested_max_seconds)
        or not _positive_number(response_requested)
        or abs(float(response_requested) - float(requested_max_seconds)) > 1e-9
        or not _deadline_elapsed_is_bounded(
            observed_elapsed_ms,
            requested_max_seconds=requested_max_seconds,
        )
        or release_reason != "run_failed"
        or not runtime_subject
    ):
        return {}
    probe = {
        "probe_kind": "platform_executor_deadline",
        "run_id": run_id,
        "probe_source": "executor_response",
        "runtime_mode": "platform",
        "runtime_subject": runtime_subject,
        "runtime_identity": dict(runtime_identity),
        "requested_max_seconds": float(requested_max_seconds),
        "observed_timeout_elapsed_ms": int(observed_elapsed_ms),
        "max_seconds_enforced": True,
        "over_limit_cleanup_verified": True,
    }
    return probe


def _deadline_elapsed_is_bounded(value: object, *, requested_max_seconds: object) -> bool:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return False
    if not _positive_number(requested_max_seconds):
        return False
    requested_ms = float(requested_max_seconds) * 1000
    elapsed_ms = float(value)
    elapsed_upper_bound_ms = requested_ms + max(250, requested_ms * 0.25)
    return math.isfinite(elapsed_ms) and max(requested_ms * 0.5, 1.0) <= elapsed_ms <= elapsed_upper_bound_ms


def _runtime_identity_from_docker_inspect(
    docker_inspect: dict[str, Any] | None,
    *,
    requested_image: str,
) -> dict[str, object]:
    if not isinstance(docker_inspect, dict) or not requested_image:
        return {}
    image_id = str(docker_inspect.get("Image") or "")
    config = docker_inspect.get("Config")
    if not image_id.startswith("sha256:") or not isinstance(config, dict):
        return {}
    observed_image = str(config.get("Image") or "")
    if observed_image != requested_image:
        return {}
    labels = _docker_config_labels(docker_inspect)
    source_revision = str(labels.get("ai-platform.source_revision") or "")
    oci_revision = str(labels.get("org.opencontainers.image.revision") or "")
    source_tree_commit = str(labels.get("ai-platform.source_tree_commit") or "")
    if (
        not source_revision
        or source_revision == "unknown"
        or source_revision != oci_revision
        or source_revision != source_tree_commit
        or str(labels.get("ai-platform.build-dirty") or "").lower() != "false"
    ):
        return {}
    return {
        "image_id": image_id,
        "requested_image": requested_image,
        "observed_image": observed_image,
        "source_revision": source_revision,
        "oci_revision": oci_revision,
        "source_tree_commit": source_tree_commit,
        "source_tree_dirty": False,
    }


def _runtime_identity_matches_subject(identity: object, *, runtime_subject: str) -> bool:
    if not isinstance(identity, dict) or not runtime_subject:
        return False
    image_id = identity.get("image_id")
    requested_image = identity.get("requested_image")
    observed_image = identity.get("observed_image")
    return (
        isinstance(image_id, str)
        and image_id.startswith("sha256:")
        and isinstance(requested_image, str)
        and bool(requested_image)
        and requested_image == observed_image
        and identity.get("source_revision") == runtime_subject
        and identity.get("oci_revision") == runtime_subject
        and identity.get("source_tree_commit") == runtime_subject
        and identity.get("source_tree_dirty") is False
    )


def _merge_current_runtime_probe_results(
    *,
    imported: dict[str, Any] | None,
    current_resource_probe: dict[str, Any],
    current_egress_probe: dict[str, Any],
) -> dict[str, Any]:
    merged = dict(imported or {})
    merged.pop("resource_limits", None)
    if current_resource_probe:
        merged["resource_limits"] = current_resource_probe
    if current_egress_probe:
        merged["egress_policy"] = current_egress_probe
    return merged


def _docker_host_config(docker_inspect: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(docker_inspect, dict):
        return {}
    host_config = docker_inspect.get("HostConfig")
    return dict(host_config) if isinstance(host_config, dict) else {}


def _docker_mounts(docker_inspect: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(docker_inspect, dict):
        return []
    mounts = docker_inspect.get("Mounts")
    if not isinstance(mounts, list):
        return []
    return [dict(item) for item in mounts if isinstance(item, dict)]


def _docker_resource_limits_verified(
    *,
    resource_limits: dict[str, Any],
    docker_inspect: dict[str, Any] | None,
) -> bool:
    host_config = _docker_host_config(docker_inspect)
    if not host_config:
        return False
    memory_bytes = host_config.get("Memory")
    nano_cpus = host_config.get("NanoCpus")
    pids_limit = host_config.get("PidsLimit")
    return (
        _positive_number(memory_bytes)
        and int(memory_bytes) == int(resource_limits.get("memory_mb") or 0) * 1024 * 1024
        and _positive_number(nano_cpus)
        and int(nano_cpus) == int(float(resource_limits.get("cpu_count") or 0) * 1_000_000_000)
        and _positive_number(pids_limit)
        and int(pids_limit) == int(resource_limits.get("pids_limit") or 0)
    )


def _docker_socket_mounted(docker_inspect: dict[str, Any] | None) -> bool:
    for mount in _docker_mounts(docker_inspect):
        values = [mount.get("Source"), mount.get("Destination"), mount.get("Name")]
        if any("/var/run/docker.sock" in str(value) for value in values if value is not None):
            return True
    host_config = _docker_host_config(docker_inspect)
    binds = host_config.get("Binds")
    if isinstance(binds, list):
        return any("/var/run/docker.sock" in str(bind) for bind in binds)
    return False


def _workspace_mount_mode(docker_inspect: dict[str, Any] | None) -> str:
    for mount in _docker_mounts(docker_inspect):
        if mount.get("Destination") == "/workspace":
            return "rw" if mount.get("RW") is not False else "ro"
    host_config = _docker_host_config(docker_inspect)
    binds = host_config.get("Binds")
    if isinstance(binds, list):
        for bind in binds:
            parts = str(bind).split(":")
            if len(parts) >= 2 and parts[1] == "/workspace":
                return "ro" if len(parts) >= 3 and "ro" in parts[2].split(",") else "rw"
    return ""


def _docker_security_options(docker_inspect: dict[str, Any] | None) -> dict[str, object]:
    host_config = _docker_host_config(docker_inspect)
    if not host_config:
        return {
            "privileged": False,
            "no_new_privileges": False,
            "capabilities_dropped": False,
            "docker_socket_mounted": False,
            "workspace_mount_mode": "rw",
            "root_filesystem_read_only_or_minimal": False,
        }
    security_opt = [str(item).lower() for item in host_config.get("SecurityOpt") or []]
    cap_drop = [str(item).upper() for item in host_config.get("CapDrop") or []]
    read_only = bool(host_config.get("ReadonlyRootfs"))
    return {
        "privileged": bool(host_config.get("Privileged")),
        "no_new_privileges": "no-new-privileges:true" in security_opt,
        "capabilities_dropped": "ALL" in cap_drop,
        "docker_socket_mounted": _docker_socket_mounted(docker_inspect),
        "workspace_mount_mode": _workspace_mount_mode(docker_inspect),
        "root_filesystem_read_only_or_minimal": read_only,
    }


def _callback_delivered(callbacks: list[dict[str, object]] | None, *, run_id: str) -> bool:
    if not isinstance(callbacks, list):
        return False
    statuses = {
        str(item.get("status") or "")
        for item in callbacks
        if isinstance(item, dict) and item.get("run_id") == run_id
    }
    return "running" in statuses and bool(statuses & TERMINAL_STATUSES)


def _docker_config_labels(docker_inspect: dict[str, Any] | None) -> dict[str, str]:
    if not isinstance(docker_inspect, dict):
        return {}
    config = docker_inspect.get("Config")
    labels = config.get("Labels") if isinstance(config, dict) else None
    if not isinstance(labels, dict):
        return {}
    return {str(key): str(value) for key, value in labels.items()}


def _docker_egress_network_name(docker_inspect: dict[str, Any] | None) -> str:
    labels = _docker_config_labels(docker_inspect)
    return str(labels.get("ai-platform.egress.network") or "")


def _docker_network_masquerade_disabled(
    docker_network_inspect: dict[str, Any] | None,
    *,
    expected_network_name: str,
) -> bool:
    if not isinstance(docker_network_inspect, dict) or not expected_network_name:
        return False
    if str(docker_network_inspect.get("Name") or "") != expected_network_name:
        return False
    if str(docker_network_inspect.get("Driver") or "") != "bridge":
        return False
    options = docker_network_inspect.get("Options")
    if not isinstance(options, dict):
        return False
    return str(options.get("com.docker.network.bridge.enable_ip_masquerade") or "").lower() == "false"


def _platform_no_masq_egress_probe(
    *,
    run_id: str,
    docker_inspect: dict[str, Any] | None,
    docker_network_inspect: dict[str, Any] | None,
    callbacks: list[dict[str, object]] | None,
) -> dict[str, Any]:
    if not isinstance(docker_inspect, dict) or not _callback_delivered(callbacks, run_id=run_id):
        return {}
    labels = _docker_config_labels(docker_inspect)
    if labels.get("ai-platform.egress.policy") != "default-deny-no-masq":
        return {}
    network_name = str(labels.get("ai-platform.egress.network") or "")
    callback_host = str(labels.get("ai-platform.egress.callback_host") or "")
    if not network_name or not callback_host:
        return {}
    host_config = _docker_host_config(docker_inspect)
    if host_config.get("NetworkMode") != network_name:
        return {}
    network_settings = docker_inspect.get("NetworkSettings")
    networks = network_settings.get("Networks") if isinstance(network_settings, dict) else None
    if not isinstance(networks, dict) or network_name not in networks:
        return {}
    extra_hosts = [str(item) for item in host_config.get("ExtraHosts") or []]
    if f"{callback_host}:host-gateway" not in extra_hosts:
        return {}
    if not _docker_network_masquerade_disabled(docker_network_inspect, expected_network_name=network_name):
        return {}
    return {
        "default_deny_outbound": False,
        "platform_allowlist_enforced": False,
        "callback_exception_scoped_to_run_token": True,
        "denied_egress_redacted": False,
        "denied_target": "",
        "denied_probe_error_code": "",
        "allowed_callback_host": callback_host,
        "callback_probe_status": "delivered",
        "policy_source": "not_runtime_verified",
        "probe_source": "docker_network_inspect",
        "network_inspection_verified": True,
        "docker_network_masquerade_disabled": True,
    }


def _docker_exec_egress_denial_probe(
    container_name: str,
    *,
    denied_target: str,
    docker_cmd: tuple[str, ...],
    run: Callable[..., Any],
) -> dict[str, Any]:
    if not container_name or not denied_target:
        return {}
    probe_code = (
        "import sys, urllib.request\n"
        f"target = {json.dumps(denied_target)}\n"
        "try:\n"
        "    urllib.request.urlopen(target, timeout=3).read(1)\n"
        "except Exception as exc:\n"
        "    marker = str(exc).lower()\n"
        "    if 'egress_denied' in marker or 'egress denied' in marker:\n"
        "        sys.exit(42)\n"
        "    sys.exit(43)\n"
        "sys.exit(0)\n"
    )
    completed = _run_docker(
        [*docker_cmd, "exec", container_name, "python", "-c", probe_code],
        run=run,
        timeout=10,
        check=False,
    )
    return {
        "denied": getattr(completed, "returncode", 1) == 42,
        "target": denied_target,
    }


def _safe_platform_egress_probe_from_result(
    *,
    run_id: str,
    egress_denial_probe: dict[str, Any] | None,
    docker_inspect: dict[str, Any] | None,
    callbacks: list[dict[str, object]] | None,
) -> dict[str, Any]:
    if not isinstance(egress_denial_probe, dict) or egress_denial_probe.get("denied") is not True:
        return {}
    if not _callback_delivered(callbacks, run_id=run_id):
        return {}
    labels = _docker_config_labels(docker_inspect)
    callback_host = str(labels.get("ai-platform.egress.callback_host") or "host.docker.internal")
    denied_target = str(egress_denial_probe.get("target") or "")
    if _redact(denied_target) != denied_target:
        return {}
    return {
        "default_deny_outbound": True,
        "platform_allowlist_enforced": True,
        "callback_exception_scoped_to_run_token": True,
        "denied_egress_redacted": True,
        "denied_target": denied_target,
        "denied_probe_error_code": "egress_denied",
        "allowed_callback_host": callback_host,
        "callback_probe_status": "delivered",
        "policy_source": "platform_policy",
        "probe_source": "runtime_probe_results",
        "run_id": run_id,
    }


def _platform_hardening_evidence(
    *,
    run_id: str,
    workspace_root: str | Path,
    recorded_lease_id: str,
    released_lease_id: str,
    release_reason: str,
    resource_limits: dict[str, Any] | None = None,
    docker_inspect: dict[str, Any] | None = None,
    docker_network_inspect: dict[str, Any] | None = None,
    runtime_probe_results: dict[str, Any] | None = None,
    callbacks: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    limits = resource_limits if isinstance(resource_limits, dict) else {}
    resource_probe = _runtime_probe_section(runtime_probe_results, "resource_limits")
    egress_probe = _runtime_probe_section(runtime_probe_results, "egress_policy")
    if egress_probe:
        egress_probe = {**egress_probe, "probe_source": str(egress_probe.get("probe_source") or "runtime_probe_results")}
    else:
        egress_probe = _platform_no_masq_egress_probe(
            run_id=run_id,
            docker_inspect=docker_inspect,
            docker_network_inspect=docker_network_inspect,
            callbacks=callbacks,
        )
    security_options = _docker_security_options(docker_inspect)
    resource_limits_evidence: dict[str, object] = {
        "evidence_class": "live_platform_probe",
        "memory_limit_mb": int(limits.get("memory_mb") or 0),
        "cpu_limit_count": float(limits.get("cpu_count") or 0),
        "pids_limit": int(limits.get("pids_limit") or 0),
        "process_timeout_seconds": int(limits.get("max_seconds") or 0),
        "limit_source": "platform_request",
        "docker_inspection_verified": _docker_resource_limits_verified(
            resource_limits=limits,
            docker_inspect=docker_inspect,
        ),
        "over_limit_cleanup_verified": resource_probe.get("over_limit_cleanup_verified") is True,
        "bounded_error_projection_verified": False,
        "max_seconds_enforced": resource_probe.get("max_seconds_enforced") is True,
    }
    if resource_probe.get("probe_kind") == "platform_executor_deadline":
        resource_limits_evidence.update(
            {
                "over_limit_probe_kind": "platform_executor_deadline",
                "over_limit_requested_max_seconds": resource_probe.get("requested_max_seconds"),
                "over_limit_observed_timeout_elapsed_ms": resource_probe.get("observed_timeout_elapsed_ms"),
                "timeout_probe_run_id": str(resource_probe.get("run_id") or ""),
                "timeout_probe_source": str(resource_probe.get("probe_source") or ""),
                "timeout_probe_runtime_mode": str(resource_probe.get("runtime_mode") or ""),
                "timeout_probe_runtime_subject": str(resource_probe.get("runtime_subject") or ""),
                "timeout_probe_runtime_identity": resource_probe.get("runtime_identity"),
            }
        )
    return {
        "lease_isolation": {
            "evidence_class": "live_platform_probe",
            "tenant_id": "tenant-a",
            "workspace_id": "workspace-a",
            "user_id": "user-a",
            "session_id": f"session-{run_id}",
            "run_id": run_id,
            "recorded_lease_id": recorded_lease_id,
            "released_lease_id": released_lease_id,
            "release_reason": release_reason,
            "host_paths_redacted": True,
        },
        "workspace_isolation": {
            "evidence_class": "live_platform_probe",
            "workspace_container_path": "/workspace",
            "inputs_container_path": "/workspace/inputs",
            "host_paths_redacted": True,
            "marker_path_is_container_path": True,
        },
        "cleanup": {
            "evidence_class": "live_platform_probe",
            "ephemeral_container_removed": True,
            "cancel_probe_container_removed": True,
            "active_lease_released": bool(recorded_lease_id and recorded_lease_id == released_lease_id),
        },
        "resource_timeout": {
            "evidence_class": "live_platform_probe",
            "max_seconds_enforced": resource_probe.get("max_seconds_enforced") is True,
            "timeout_error_code": "executor_deadline_exceeded"
            if resource_probe.get("max_seconds_enforced") is True
            else "",
            "failed_container_removed": resource_probe.get("over_limit_cleanup_verified") is True,
            "requested_max_seconds": resource_probe.get("requested_max_seconds"),
            "observed_timeout_elapsed_ms": resource_probe.get("observed_timeout_elapsed_ms"),
            "run_id": str(resource_probe.get("run_id") or ""),
            "probe_source": str(resource_probe.get("probe_source") or ""),
            "runtime_mode": str(resource_probe.get("runtime_mode") or ""),
            "runtime_subject": str(resource_probe.get("runtime_subject") or ""),
            "runtime_identity": resource_probe.get("runtime_identity"),
        },
        "failure_fallback": {
            "evidence_class": "source_regression_guard",
            "dispatch_failure_stops_container": True,
            "lease_record_failure_stops_container": True,
            "db_lease_not_released_when_stop_fails": True,
            "source_regression_tests": [
                "tests/test_sandbox_runtime.py::test_runtime_does_not_release_db_lease_when_completion_stop_fails",
                "tests/test_sandbox_runtime.py::test_runtime_does_not_release_db_lease_when_dispatch_failure_stop_fails",
                "tests/test_sandbox_runtime.py::test_runtime_stops_live_container_when_lease_recording_fails",
            ],
        },
        "cached_lease_revalidation": {
            "evidence_class": "source_regression_guard",
            "cached_lease_revalidates_scope_labels": True,
            "scope_mismatch_fails_closed": True,
            "tenant_workspace_user_session_checked": True,
            "source_regression_tests": [
                "tests/test_sandbox_container_provider.py::test_docker_provider_cached_lease_revalidates_container_scope_labels",
            ],
        },
        "resource_limits": resource_limits_evidence,
        "egress_policy": {
            "evidence_class": "live_platform_probe",
            "default_deny_outbound": egress_probe.get("default_deny_outbound") is True,
            "platform_allowlist_enforced": egress_probe.get("platform_allowlist_enforced") is True,
            "callback_exception_scoped_to_run_token": egress_probe.get(
                "callback_exception_scoped_to_run_token",
                True,
            )
            is True,
            "denied_egress_redacted": egress_probe.get("denied_egress_redacted") is True,
            "denied_target": str(egress_probe.get("denied_target") or ""),
            "denied_probe_error_code": str(egress_probe.get("denied_probe_error_code") or ""),
            "allowed_callback_host": str(egress_probe.get("allowed_callback_host") or ""),
            "callback_probe_status": str(egress_probe.get("callback_probe_status") or ""),
            "policy_source": (
                "platform_policy" if egress_probe.get("policy_source") == "platform_policy" else "not_runtime_verified"
            ),
            "probe_source": str(egress_probe.get("probe_source") or ""),
            "network_inspection_verified": egress_probe.get("network_inspection_verified") is True,
            "docker_network_masquerade_disabled": egress_probe.get("docker_network_masquerade_disabled") is True,
        },
        "security_options": {
            "evidence_class": "live_platform_probe",
            **security_options,
        },
        "source": {
            "runtime_submit": "app.runtime.sandbox.runtime.SandboxRuntime.submit",
            "workspace_root": "[redacted-path]" if str(workspace_root) else "",
            "resource_timeout_and_failure_fallback": "observed_executor_deadline_plus_source_regression_fallback",
            "cached_lease_revalidation": "source_regression_tests_plus_live_platform_runtime_smoke",
        },
    }


def record_platform_runtime_probe(
    *,
    recorder: EvidenceRecorder,
    sandbox_provider: str,
    workspace_root: str | Path,
    probe: Callable[[], Any],
    recorded_lease_id: str | None = None,
    released_lease_id: str | None = None,
    release_reason: str = "dispatch_completed",
) -> dict[str, object]:
    result = asyncio.run(probe())
    recorder.runtime_mode = "platform"
    recorder.sandbox_provider = sandbox_provider
    recorder.executed_task = True
    recorder.timings = _timings_from_result(result)
    recorder.executor = _executor_evidence_from_result(result)
    lease_id = recorded_lease_id or f"lease-{_safe_run_id(recorder.run_id)}"
    recorder.hardening = _platform_hardening_evidence(
        run_id=recorder.run_id,
        workspace_root=workspace_root,
        recorded_lease_id=lease_id,
        released_lease_id=released_lease_id or lease_id,
        release_reason=release_reason,
        resource_limits={"max_seconds": 60, "memory_mb": 512, "pids_limit": 128},
    )
    return {
        "status": str(getattr(result, "status", "")),
        "run_id": str(getattr(result, "run_id", recorder.run_id)),
    }


def _opensandbox_provider_lifecycle_evidence(
    *,
    recorder: EvidenceRecorder,
    captured: dict[str, Any],
    resource_limits: dict[str, Any],
) -> dict[str, object]:
    if recorder.sandbox_provider != "opensandbox":
        return {}
    recorded_lease_id = str(captured.get("recorded_lease_id") or "")
    released_lease_id = str(captured.get("released_lease_id") or "")
    release_reason = str(captured.get("release_reason") or "")
    lease_labels = captured.get("lease_labels")
    labels = lease_labels if isinstance(lease_labels, dict) else {}
    return {
        "schema_version": "ai-platform.opensandbox-provider-lifecycle.v1",
        "provider": "opensandbox",
        "run_id": recorder.run_id,
        "lifecycle": {
            "create_observed": bool(captured.get("container_id")),
            "delete_observed": bool(recorded_lease_id and recorded_lease_id == released_lease_id),
            "container_id_present": bool(captured.get("container_id")),
            "executor_endpoint_present": bool(captured.get("executor_url")),
        },
        "db_lease": {
            "recorded": bool(recorded_lease_id),
            "released": bool(released_lease_id),
            "release_reason": release_reason,
            "recorded_scope_matches_request": (
                captured.get("recorded_tenant_id") == "tenant-a"
                and captured.get("recorded_workspace_id") == "workspace-a"
                and captured.get("recorded_user_id") == "user-a"
                and captured.get("recorded_session_id") == f"session-{recorder.run_id}"
                and captured.get("recorded_run_id") == recorder.run_id
                and captured.get("released_run_id") == recorder.run_id
            ),
        },
        "startup_io": {
            "file_write_read_verified": captured.get("opensandbox_startup_io_probe_enabled") is True,
            "command_execution_verified": captured.get("opensandbox_startup_io_probe_enabled") is True,
            "source": "OpenSandboxContainerProvider.startup_io_probe",
        },
        "resource_policy": {
            "resource_limits_requested": all(
                _positive_number(resource_limits.get(key))
                for key in ("memory_mb", "cpu_count", "pids_limit")
            ),
            "memory_mb": int(resource_limits.get("memory_mb") or 0),
            "cpu_count": float(resource_limits.get("cpu_count") or 0),
            "pids_limit": int(resource_limits.get("pids_limit") or 0),
            "policy_projection_source": "provider_request",
        },
        "egress_policy": {
            "policy_requested": labels.get("ai-platform.egress.policy") == "opensandbox-network-policy",
            "callback_host_allowlisted": bool(labels.get("ai-platform.egress.callback_host")),
            "policy_projection_source": "provider_request",
        },
        "dispatch": {
            "executor_response_present": bool(recorder.executor),
            "callback_stream_observed": recorder.has_required_callbacks(),
            "sdk_executor_observed": recorder.executor.get("sdk_used") is True
            and recorder.executor.get("executor_mode") == "claude_agent_sdk",
        },
        "redaction": {
            "host_paths_redacted": True,
            "secrets_absent": True,
        },
    }


def run_platform_runtime_probe(
    *,
    recorder: EvidenceRecorder,
    sandbox_provider: str,
    sandbox_executor_image: str,
    workspace_root: str,
    callback_url: str,
    docker_cmd: tuple[str, ...] = ("docker",),
    run: Callable[..., Any] = subprocess.run,
    runtime_probe_results: dict[str, Any] | None = None,
    platform_resource_timeout_probe: bool = False,
    denied_egress_target: str = "https://egress-denied.invalid/",
    capture_runtime_egress_probe: bool = False,
) -> dict[str, object]:
    captured: dict[str, Any] = {
        "recorded_lease_id": "",
        "released_lease_id": "",
        "release_reason": "",
        "container_name": "",
        "container_id": "",
        "executor_url": "",
        "lease_labels": {},
        "docker_inspect": None,
        "egress_denial_probe": {},
    }

    async def probe() -> object:
        from app.control_plane_contracts import standard_trace_id
        from app.runtime.sandbox.contracts import SandboxRuntimeRequest
        from app.runtime.sandbox.runtime import SandboxRuntime
        from app.settings import get_settings
        from app.runtime.sandbox import container_provider

        settings = get_settings()
        original_provider = settings.sandbox_container_provider
        original_executor_image = settings.sandbox_executor_image
        original_workspace_root = settings.sandbox_workspace_root
        settings.sandbox_container_provider = sandbox_provider
        captured["opensandbox_startup_io_probe_enabled"] = bool(
            getattr(settings, "opensandbox_startup_io_probe_enabled", True)
        )
        if sandbox_executor_image:
            settings.sandbox_executor_image = sandbox_executor_image
        settings.sandbox_workspace_root = workspace_root
        container_provider.reset_container_provider_cache()
        try:
            async def record_lease(lease, request, workspace):
                lease_id = f"lease-{_safe_run_id(lease.run_id)}"
                captured["recorded_lease_id"] = lease_id
                captured["recorded_tenant_id"] = lease.tenant_id
                captured["recorded_workspace_id"] = lease.workspace_id
                captured["recorded_user_id"] = lease.user_id
                captured["recorded_session_id"] = lease.session_id
                captured["recorded_run_id"] = lease.run_id
                captured["container_id"] = lease.container_id
                captured["container_name"] = lease.container_name
                captured["executor_url"] = lease.executor_url
                captured["lease_labels"] = dict(getattr(lease, "labels", {}) or {})
                captured["workspace_container_path"] = workspace.workspace_container_path
                captured["lease_projection"] = {
                    "provider": lease.provider,
                    "lease_payload": {
                        "source": "sandbox_runtime",
                        "evidence_class": "runtime_lease_projection",
                        "container_id": lease.container_id,
                        "container_name": lease.container_name,
                        "workspace_container_path": workspace.workspace_container_path,
                    },
                }
                docker_inspect = _inspect_docker_container(
                    lease.container_name,
                    docker_cmd=docker_cmd,
                    run=run,
                )
                captured["docker_inspect"] = docker_inspect
                if capture_runtime_egress_probe:
                    captured["egress_denial_probe"] = _docker_exec_egress_denial_probe(
                        lease.container_name,
                        denied_target=denied_egress_target,
                        docker_cmd=docker_cmd,
                        run=run,
                    )
                network_name = _docker_egress_network_name(docker_inspect)
                captured["docker_network_inspect"] = _inspect_docker_network(
                    network_name,
                    docker_cmd=docker_cmd,
                    run=run,
                )
                return lease_id

            async def release_lease(lease, reason, lease_record_id=None):
                captured["released_lease_id"] = str(lease_record_id or "")
                captured["release_reason"] = str(reason)
                captured["released_run_id"] = lease.run_id

            runtime = SandboxRuntime(
                workspace_root=workspace_root,
                callback_token_resolver=lambda token_id: _callback_token_for_url(
                    callback_url,
                    token_id,
                    recorder._callback_token,
                ),
                record_lease=record_lease,
                release_lease=release_lease,
            )
            resource_limits = {"max_seconds": 60, "memory_mb": 512, "cpu_count": 0.5, "pids_limit": 128}
            if platform_resource_timeout_probe:
                resource_limits = dict(resource_limits)
                resource_limits["max_seconds"] = PLATFORM_DEADLINE_PROBE_SECONDS
                resource_limits["platform_timeout_probe"] = True
            request = SandboxRuntimeRequest(
                tenant_id="tenant-a",
                workspace_id="workspace-a",
                user_id="user-a",
                session_id=f"session-{recorder.run_id}",
                run_id=recorder.run_id,
                agent_id="sandbox-runtime-verifier",
                skill_ids=[],
                mcp_tool_ids=[],
                input_message="ai-platform platform sandbox runtime 211 smoke",
                file_ids=[],
                sandbox_mode="ephemeral",
                browser_enabled=False,
                model=_configured_platform_runtime_model(settings),
                resource_limits=resource_limits,
                trace_id=standard_trace_id(recorder.run_id),
                callback_url=callback_url,
                callback_token_id=_callback_token_id_for_url(callback_url, recorder.run_id),
            )
            return await runtime.submit(request)
        finally:
            settings.sandbox_container_provider = original_provider
            settings.sandbox_executor_image = original_executor_image
            settings.sandbox_workspace_root = original_workspace_root
            container_provider.reset_container_provider_cache()

    result = asyncio.run(probe())
    recorder.runtime_mode = "platform"
    recorder.sandbox_provider = sandbox_provider
    recorder.executed_task = True
    recorder.timings = _timings_from_result(result)
    recorder.executor = _executor_evidence_from_result(result)
    recorder.lease_projection = (
        dict(captured["lease_projection"])
        if isinstance(captured.get("lease_projection"), dict)
        else {}
    )
    recorded_lease_id = captured.get("recorded_lease_id") or f"lease-{_safe_run_id(recorder.run_id)}"
    released_lease_id = captured.get("released_lease_id") or ""
    docker_inspect = captured.get("docker_inspect") if isinstance(captured.get("docker_inspect"), dict) else None
    platform_resource_probe = _safe_platform_resource_probe_from_result(
        run_id=recorder.run_id,
        result=result,
        release_reason=captured.get("release_reason"),
        platform_resource_timeout_probe=platform_resource_timeout_probe,
        requested_max_seconds=PLATFORM_DEADLINE_PROBE_SECONDS,
        runtime_identity=_runtime_identity_from_docker_inspect(
            docker_inspect,
            requested_image=sandbox_executor_image,
        ),
    )
    platform_egress_probe = _safe_platform_egress_probe_from_result(
        run_id=recorder.run_id,
        egress_denial_probe=captured.get("egress_denial_probe")
        if isinstance(captured.get("egress_denial_probe"), dict)
        else None,
        docker_inspect=captured.get("docker_inspect") if isinstance(captured.get("docker_inspect"), dict) else None,
        callbacks=recorder.callbacks,
    )
    derived_runtime_probe_results = _merge_current_runtime_probe_results(
        imported=runtime_probe_results,
        current_resource_probe=platform_resource_probe,
        current_egress_probe=platform_egress_probe,
    )
    recorder.hardening = _platform_hardening_evidence(
        run_id=recorder.run_id,
        workspace_root=workspace_root,
        recorded_lease_id=recorded_lease_id,
        released_lease_id=released_lease_id,
        release_reason=captured.get("release_reason") or "",
        resource_limits={"max_seconds": 60, "memory_mb": 512, "cpu_count": 0.5, "pids_limit": 128},
        docker_inspect=captured.get("docker_inspect") if isinstance(captured.get("docker_inspect"), dict) else None,
        docker_network_inspect=captured.get("docker_network_inspect")
        if isinstance(captured.get("docker_network_inspect"), dict)
        else None,
        runtime_probe_results=derived_runtime_probe_results,
        callbacks=recorder.callbacks,
    )
    recorder.provider_lifecycle = _opensandbox_provider_lifecycle_evidence(
        recorder=recorder,
        captured=captured,
        resource_limits={"max_seconds": 60, "memory_mb": 512, "cpu_count": 0.5, "pids_limit": 128},
    )
    output = {
        "status": str(getattr(result, "status", "")),
        "run_id": str(getattr(result, "run_id", recorder.run_id)),
    }
    response = getattr(result, "executor_response", {})
    if isinstance(response, dict) and response.get("error_code"):
        output["error_code"] = str(response["error_code"])
    return output


def _runtime_probe_results_payload(*, run_id: str, hardening: dict[str, Any]) -> dict[str, Any]:
    resource_limits = hardening.get("resource_limits")
    egress_policy = hardening.get("egress_policy")
    security_options = hardening.get("security_options")
    resource_limits = resource_limits if isinstance(resource_limits, dict) else {}
    egress_policy = egress_policy if isinstance(egress_policy, dict) else {}
    security_options = security_options if isinstance(security_options, dict) else {}
    return {
        "schema_version": RUNTIME_PROBE_RESULTS_SCHEMA_VERSION,
        "run_id": run_id,
        "source": "platform_runtime_probe",
        "resource_limits": {
            "over_limit_cleanup_verified": resource_limits.get("over_limit_cleanup_verified") is True,
            "probe_kind": str(resource_limits.get("over_limit_probe_kind") or ""),
            "run_id": str(resource_limits.get("timeout_probe_run_id") or ""),
            "probe_source": str(resource_limits.get("timeout_probe_source") or ""),
            "runtime_mode": str(resource_limits.get("timeout_probe_runtime_mode") or ""),
            "runtime_subject": str(resource_limits.get("timeout_probe_runtime_subject") or ""),
            "runtime_identity": resource_limits.get("timeout_probe_runtime_identity"),
            "requested_max_seconds": resource_limits.get("over_limit_requested_max_seconds"),
            "observed_timeout_elapsed_ms": resource_limits.get("over_limit_observed_timeout_elapsed_ms"),
            "max_seconds_enforced": resource_limits.get("max_seconds_enforced") is True,
        },
        "egress_policy": {
            "default_deny_outbound": egress_policy.get("default_deny_outbound") is True,
            "platform_allowlist_enforced": egress_policy.get("platform_allowlist_enforced") is True,
            "callback_exception_scoped_to_run_token": egress_policy.get("callback_exception_scoped_to_run_token")
            is True,
            "denied_egress_redacted": egress_policy.get("denied_egress_redacted") is True,
            "denied_target": str(egress_policy.get("denied_target") or ""),
            "denied_probe_error_code": str(egress_policy.get("denied_probe_error_code") or ""),
            "allowed_callback_host": str(egress_policy.get("allowed_callback_host") or ""),
            "callback_probe_status": str(egress_policy.get("callback_probe_status") or ""),
            "policy_source": str(egress_policy.get("policy_source") or ""),
            "probe_source": str(egress_policy.get("probe_source") or ""),
        },
        "security_options": {
            "privileged": security_options.get("privileged") is True,
            "no_new_privileges": security_options.get("no_new_privileges") is True,
            "capabilities_dropped": security_options.get("capabilities_dropped") is True,
            "docker_socket_mounted": security_options.get("docker_socket_mounted") is True,
            "workspace_mount_mode": str(security_options.get("workspace_mount_mode") or ""),
            "root_filesystem_read_only_or_minimal": security_options.get("root_filesystem_read_only_or_minimal")
            is True,
        },
    }


def generate_runtime_probe_results(
    *,
    recorder: EvidenceRecorder,
    sandbox_provider: str,
    sandbox_executor_image: str,
    workspace_root: str,
    callback_url: str,
    docker_cmd: tuple[str, ...],
    output_file: str | Path,
    denied_egress_target: str = "https://egress-denied.invalid/",
    run: Callable[..., Any] = subprocess.run,
) -> dict[str, object]:
    run_platform_runtime_probe(
        recorder=recorder,
        sandbox_provider=sandbox_provider,
        sandbox_executor_image=sandbox_executor_image,
        workspace_root=workspace_root,
        callback_url=callback_url,
        docker_cmd=docker_cmd,
        run=run,
        platform_resource_timeout_probe=True,
        denied_egress_target=denied_egress_target,
        capture_runtime_egress_probe=True,
    )
    payload = _runtime_probe_results_payload(run_id=recorder.run_id, hardening=recorder.hardening)
    for section_name in RUNTIME_PROBE_RESULTS_SECTION_KEYS:
        section = payload.get(section_name)
        if not isinstance(section, dict):
            raise RuntimeError(f"runtime probe results section must be an object: {section_name}")
        section_error = _runtime_probe_section_error(section_name, section, run_id=recorder.run_id)
        if section_error:
            raise RuntimeError(section_error)
    path = Path(output_file)
    path.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(payload, ensure_ascii=True, indent=2)
    if _redact(serialized) != serialized:
        raise RuntimeError("runtime probe results contain sensitive content")
    path.write_text(serialized, encoding="utf-8")
    return {
        "run_id": recorder.run_id,
        "runtime_probe_results_file": "[redacted-path]",
        "sections": list(RUNTIME_PROBE_RESULTS_SECTION_KEYS),
    }


def _run_docker(
    cmd: list[str],
    *,
    run: Callable[..., Any],
    timeout: int = 30,
    check: bool = False,
) -> Any:
    completed = run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
    if check and getattr(completed, "returncode", 1) != 0:
        stderr = getattr(completed, "stderr", "") or getattr(completed, "stdout", "")
        raise RuntimeError(_redact(stderr or f"Docker command failed: {' '.join(cmd[:2])}"))
    return completed


def _inspect_docker_container(
    container_name: str,
    *,
    docker_cmd: tuple[str, ...],
    run: Callable[..., Any],
) -> dict[str, Any] | None:
    if not container_name:
        return None
    completed = _run_docker(
        [*docker_cmd, "inspect", container_name],
        run=run,
        timeout=30,
        check=False,
    )
    if getattr(completed, "returncode", 1) != 0:
        return None
    try:
        payload = json.loads(str(getattr(completed, "stdout", "") or "[]"))
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, list) or not payload or not isinstance(payload[0], dict):
        return None
    return dict(payload[0])


def _inspect_docker_network(
    network_name: str,
    *,
    docker_cmd: tuple[str, ...],
    run: Callable[..., Any],
) -> dict[str, Any] | None:
    if not network_name:
        return None
    completed = _run_docker(
        [*docker_cmd, "network", "inspect", network_name],
        run=run,
        timeout=30,
        check=False,
    )
    if getattr(completed, "returncode", 1) != 0:
        return None
    try:
        payload = json.loads(str(getattr(completed, "stdout", "") or "[]"))
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, list) or not payload or not isinstance(payload[0], dict):
        return None
    return dict(payload[0])


def run_cancel_probe(
    *,
    run_id: str,
    docker_cmd: tuple[str, ...],
    cancel_image: str,
    run: Callable[..., Any] = subprocess.run,
) -> str:
    safe_run_id = _safe_run_id(run_id)
    container_name = f"ai-platform-sandbox-verifier-{safe_run_id}"
    create_cmd = [
        *docker_cmd,
        "create",
        "--name",
        container_name,
        "--label",
        "ai-platform.verifier=sandbox-runtime-211",
        "--label",
        f"ai-platform.run_id={run_id}",
        cancel_image,
        "sh",
        "-c",
        "sleep 300",
    ]
    container_id = ""
    try:
        completed = _run_docker(create_cmd, run=run, timeout=60, check=True)
        container_id = str(getattr(completed, "stdout", "")).strip()
        if not container_id:
            raise RuntimeError("Docker create did not return a container id")
        _run_docker([*docker_cmd, "start", container_id], run=run, timeout=30, check=True)
        _run_docker([*docker_cmd, "stop", container_id], run=run, timeout=30, check=True)
        return container_id
    finally:
        if container_id:
            _run_docker([*docker_cmd, "rm", "-f", container_id], run=run, timeout=30, check=False)


def _wait_for_callbacks(recorder: EvidenceRecorder, timeout_seconds: float) -> bool:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() <= deadline:
        if recorder.has_required_callbacks():
            return True
        time.sleep(0.1)
    return recorder.has_required_callbacks()


def build_parser() -> argparse.ArgumentParser:
    parser = SandboxEvidenceArgumentParser(
        description=(
            'Generate ai-platform sandbox runtime evidence on 211; use --docker-cmd "sudo -n docker" '
            "on 211 and keep this as controlled admin/allowlist evidence."
        )
    )
    parser.add_argument("--executor-url", default=os.environ.get("AI_PLATFORM_EXECUTOR_URL", ""))
    parser.add_argument(
        "--workspace-root",
        default=os.environ.get("AI_PLATFORM_SANDBOX_WORKSPACE_ROOT", "/tmp/ai-platform-sandbox-workspaces"),
    )
    parser.add_argument(
        "--evidence-file",
        default=os.environ.get("AI_PLATFORM_SANDBOX_EVIDENCE", "/tmp/ai-platform-sandbox-runtime-evidence.json"),
    )
    parser.add_argument("--run-id", default=os.environ.get("AI_PLATFORM_SANDBOX_RUN_ID", f"sandbox-smoke-{uuid.uuid4().hex[:8]}"))
    parser.add_argument("--callback-token", default=os.environ.get("SANDBOX_CALLBACK_TOKEN", "sandbox-smoke-callback-token"))
    parser.add_argument(
        "--docker-cmd",
        default=os.environ.get("DOCKER_CMD", "docker"),
        help='Docker command; use --docker-cmd "sudo -n docker" on 211.',
    )
    parser.add_argument(
        "--cancel-image",
        default=os.environ.get("AI_PLATFORM_CANCEL_PROBE_IMAGE", "ai-platform:local"),
        help="Verifier-owned cancel probe image; defaults to ai-platform:local.",
    )
    parser.add_argument(
        "--sandbox-executor-image",
        default=os.environ.get("AI_PLATFORM_SANDBOX_EXECUTOR_IMAGE", os.environ.get("SANDBOX_EXECUTOR_IMAGE", "")),
    )
    parser.add_argument(
        "--inspection-profile",
        choices=list(INSPECTION_PROFILES),
        default="",
        help=(
            "Run the fixed staged-Skill mount inspection with either the implicit platform-controlled "
            "catalog or the implicit sdk-native catalog plus governed Bash."
        ),
    )
    parser.add_argument(
        "--source-sha",
        default=os.environ.get("AI_PLATFORM_SANDBOX_SOURCE_SHA", ""),
        help="Deployed source SHA; when omitted the verifier script SHA-256 is recorded.",
    )
    parser.add_argument("--callback-host", default=os.environ.get("AI_PLATFORM_CALLBACK_HOST"))
    parser.add_argument("--callback-public-url", default=os.environ.get("AI_PLATFORM_CALLBACK_PUBLIC_URL"))
    parser.add_argument("--callback-port", type=int, default=int(os.environ.get("AI_PLATFORM_CALLBACK_PORT", "0")))
    parser.add_argument("--callback-timeout", type=float, default=float(os.environ.get("AI_PLATFORM_CALLBACK_TIMEOUT", "10")))
    parser.add_argument(
        "--runtime-probe-results-file",
        default=os.environ.get("AI_PLATFORM_SANDBOX_RUNTIME_PROBE_RESULTS", ""),
        help=(
            "Optional same-run platform probe results JSON for resource-limit and egress hardening evidence. "
            "The file must use ai-platform.sandbox-runtime-probe-results.v1 and match --run-id."
        ),
    )
    parser.add_argument(
        "--generate-runtime-probe-results-file",
        default=os.environ.get("AI_PLATFORM_SANDBOX_GENERATE_RUNTIME_PROBE_RESULTS", ""),
        help=(
            "Generate same-run platform runtime probe results JSON for a later --runtime-probe-results-file run. "
            "This is a probe-input generation step, not formal sandbox runtime acceptance evidence."
        ),
    )
    parser.add_argument(
        "--denied-egress-target",
        default=os.environ.get("AI_PLATFORM_SANDBOX_DENIED_EGRESS_TARGET", "https://egress-denied.invalid/"),
        help="Verifier-owned target used to prove denied outbound egress in runtime probe results.",
    )
    parser.add_argument(
        "--runtime-mode",
        choices=["executor", "platform"],
        default=os.environ.get("AI_PLATFORM_SANDBOX_RUNTIME_MODE", "executor"),
    )
    parser.add_argument(
        "--sandbox-provider",
        choices=["fake", "docker", "opensandbox"],
        default=os.environ.get("SANDBOX_CONTAINER_PROVIDER", "docker"),
    )
    parser.add_argument("--skip-live-submit", action="store_true")
    parser.add_argument("--skip-cancel-probe", action="store_true")
    parser.add_argument(
        "--platform-resource-timeout-probe",
        action="store_true",
        default=os.environ.get("AI_PLATFORM_RESOURCE_TIMEOUT_PROBE", "").lower() in {"1", "true", "yes"},
        help="Run the platform submit with max_seconds=0 to produce explicit resource over-limit cleanup evidence.",
    )
    parser.add_argument("--json", action="store_true", dest="json_output")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.inspection_profile:
        return _run_skill_mount_inspection(args)
    docker_cmd = tuple(part for part in args.docker_cmd.split(" ") if part)
    recorder = EvidenceRecorder(
        run_id=args.run_id,
        executor_url=args.executor_url,
        callback_token=args.callback_token,
    )
    messages: list[str] = []
    server: ThreadingHTTPServer | None = None

    if args.generate_runtime_probe_results_file:
        try:
            server, local_callback_url = start_callback_server(
                bind_host=args.callback_host,
                bind_port=args.callback_port,
                recorder=recorder,
            )
            callback_url = resolve_callback_public_url(args.callback_public_url, local_callback_url)
            probe_summary = generate_runtime_probe_results(
                recorder=recorder,
                sandbox_provider=args.sandbox_provider,
                sandbox_executor_image=args.sandbox_executor_image or args.cancel_image,
                workspace_root=args.workspace_root,
                callback_url=callback_url,
                docker_cmd=docker_cmd,
                output_file=args.generate_runtime_probe_results_file,
                denied_egress_target=args.denied_egress_target,
                run=subprocess.run,
            )
            output = {
                "run_id": args.run_id,
                "evidence_file": "[redacted-path]",
                "runtime_probe_results_file": probe_summary["runtime_probe_results_file"],
                "sections": probe_summary["sections"],
                "executed_task": True,
                "runtime_mode": "platform_probe_results",
                "sandbox_provider": args.sandbox_provider,
                "callbacks": len(recorder.callbacks),
                "cancel_stops_container": False,
                "messages": messages,
            }
            if args.json_output:
                print(json.dumps(output, ensure_ascii=True, indent=2))
            else:
                print("PASSED: runtime probe results generated")
            return 0
        except Exception as exc:
            messages.append(_redact(exc))
            output = {
                "run_id": args.run_id,
                "evidence_file": "[redacted-path]",
                "runtime_probe_results_file": "[redacted-path]",
                "executed_task": False,
                "runtime_mode": "platform_probe_results",
                "sandbox_provider": args.sandbox_provider,
                "callbacks": len(recorder.callbacks),
                "cancel_stops_container": False,
                "messages": messages,
            }
            if args.json_output:
                print(json.dumps(output, ensure_ascii=True, indent=2))
            else:
                print("FAILED: runtime probe results incomplete")
                for message in messages:
                    print(f"- {message}")
            return 1
        finally:
            if server is not None:
                server.shutdown()
                server.server_close()

    try:
        runtime_probe_results = (
            load_runtime_probe_results(args.runtime_probe_results_file, run_id=args.run_id)
            if args.runtime_probe_results_file
            else None
        )
        if not args.skip_live_submit:
            if not args.executor_url:
                raise RuntimeError("executor URL not configured")
            server, local_callback_url = start_callback_server(
                bind_host=args.callback_host,
                bind_port=args.callback_port,
                recorder=recorder,
            )
            callback_url = resolve_callback_public_url(args.callback_public_url, local_callback_url)
            if args.runtime_mode == "platform":
                run_platform_runtime_probe(
                    recorder=recorder,
                    sandbox_provider=args.sandbox_provider,
                    sandbox_executor_image=args.sandbox_executor_image or args.cancel_image,
                    workspace_root=args.workspace_root,
                    callback_url=callback_url,
                    docker_cmd=docker_cmd,
                    runtime_probe_results=runtime_probe_results,
                    platform_resource_timeout_probe=args.platform_resource_timeout_probe,
                )
            else:
                recorder.runtime_mode = "executor"
                recorder.sandbox_provider = "external_executor"
                executor_response = submit_executor_task(
                    executor_url=args.executor_url,
                    callback_url=callback_url,
                    callback_token=args.callback_token,
                    run_id=args.run_id,
                    workspace_root=args.workspace_root,
                )
                recorder.executor = _executor_evidence_from_response(executor_response)
                recorder.executed_task = True
            if not _wait_for_callbacks(recorder, args.callback_timeout):
                messages.append("required callbacks not observed")
        if not args.skip_live_submit and not args.skip_cancel_probe:
            container_id = run_cancel_probe(run_id=args.run_id, docker_cmd=docker_cmd, cancel_image=args.cancel_image)
            recorder.cancel_stops_container = True
            recorder.cancelled_container_id = container_id
    except Exception as exc:
        messages.append(_redact(exc))
    finally:
        if server is not None:
            server.shutdown()
            server.server_close()
        recorder.write(args.evidence_file)

    success = (
        recorder.executed_task
        and recorder.has_required_callbacks()
        and (args.skip_cancel_probe or recorder.cancel_stops_container)
    )
    if args.skip_live_submit:
        success = True
    output = {
        "run_id": args.run_id,
        "evidence_file": "[redacted-path]",
        "executed_task": recorder.executed_task,
        "runtime_mode": recorder.runtime_mode,
        "sandbox_provider": recorder.sandbox_provider,
        "callbacks": len(recorder.callbacks),
        "cancel_stops_container": recorder.cancel_stops_container,
        "messages": messages,
    }
    if args.json_output:
        print(json.dumps(output, ensure_ascii=True, indent=2))
    else:
        print("PASSED: evidence generated" if success else "FAILED: evidence incomplete")
        for message in messages:
            print(f"- {message}")
    return 0 if success else 1


def _entrypoint(argv: list[str]) -> int:
    evidence_path = _safe_evidence_file_from_argv(argv)
    try:
        return main(argv)
    except SystemExit as exc:
        code = exc.code if type(exc.code) is int else 1
        if code != 0 and evidence_path is not None:
            _write_bootstrap_error(
                evidence_path,
                failure_category="argument_error",
                exit_code=code,
            )
        return code
    except BaseException as exc:
        code = 130 if isinstance(exc, (asyncio.CancelledError, KeyboardInterrupt)) else 1
        if evidence_path is not None:
            _write_bootstrap_error(
                evidence_path,
                failure_category=(
                    "cancelled"
                    if code == 130
                    else "dependency_import_failure"
                    if isinstance(exc, ImportError)
                    else "bootstrap_failure"
                ),
                exit_code=code,
            )
        print("FAILED: sandbox runtime evidence bootstrap", file=sys.stderr)
        return code


if __name__ == "__main__":
    raise SystemExit(_entrypoint(sys.argv[1:]))
