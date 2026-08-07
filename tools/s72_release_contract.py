"""Typed, read-only configuration authority for an s72 co-located release."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import stat
from typing import Mapping, Sequence
from urllib.parse import urlsplit

if __package__:
    from . import release_authority
else:  # pragma: no cover - direct script execution
    import release_authority  # type: ignore[no-redef]


SCHEMA_VERSION = "ai-platform.s72-release-contract.v1"
IMMUTABLE_IMAGE_RE = re.compile(r"[a-z0-9][a-z0-9._:/-]*@sha256:[0-9a-f]{64}\Z")
IDENTITY_SUBJECT_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}\Z")
PLACEHOLDER_RE = re.compile(r"change[_-]?me|replace[_-]?me|required|placeholder|example|secret.value", re.I)

RETIRED_CROSS_HOST_KEYS = frozenset(
    {
        "AI_PLATFORM_S72_BRIDGE_PORT",
        "AI_PLATFORM_S72_BRIDGE_SERVER_NAME",
        "AI_PLATFORM_S72_BRIDGE_ALLOWED_SOURCE_IP",
        "AI_PLATFORM_S72_BRIDGE_TLS_CERT_FILE",
        "AI_PLATFORM_S72_BRIDGE_TLS_KEY_FILE",
        "OPENSANDBOX_EXTERNAL_EGRESS_CALLBACK_BASE_URL",
        "OPENSANDBOX_EXTERNAL_EGRESS_OPENAI_BASE_URL",
        "OPENSANDBOX_EXTERNAL_EGRESS_ANTHROPIC_BASE_URL",
    }
)
REQUIRED_KEYS = frozenset(
    {
        "AI_PLATFORM_MODEL_UPSTREAM",
        "AI_PLATFORM_FRONTEND_PORT",
        "WORKER_CLAUDE_AGENT_SDK_ENABLED",
        "CLAUDE_AGENT_PERMISSION_MODE",
        "CLAUDE_AGENT_ALLOWED_TOOLS",
        "CLAUDE_AGENT_DISALLOWED_TOOLS",
        "SANDBOX_CONTAINER_PROVIDER",
        "SANDBOX_SECURITY_PROFILE",
        "OPENSANDBOX_API_KEY",
        "OPENSANDBOX_DOMAIN",
        "OPENSANDBOX_PROTOCOL",
        "OPENSANDBOX_EXECUTOR_IMAGE",
        "OPENSANDBOX_EXECUTOR_IMAGE_DIGEST",
        "OPENSANDBOX_ATTESTATION_PATH",
        "OPENSANDBOX_ATTESTATION_CONTRACT_VERSION",
        "OPENSANDBOX_EXTERNAL_EGRESS_CAPABILITY_URL",
        "OPENSANDBOX_EXTERNAL_EGRESS_CAPABILITY_TOKEN",
        "OPENSANDBOX_EXTERNAL_EGRESS_GATEWAY_POLICY_SUBJECT",
        "OPENSANDBOX_EXTERNAL_EGRESS_CALLBACK_BOUNDARY_SUBJECT",
        "SANDBOX_CALLBACK_TOKEN",
        "SANDBOX_EGRESS_PROOF_SIGNING_KEY",
        "SANDBOX_RUNTIME_SUBJECT",
    }
)
SECRET_KEYS = frozenset(
    {
        "OPENSANDBOX_API_KEY",
        "OPENSANDBOX_EXTERNAL_EGRESS_CAPABILITY_TOKEN",
        "SANDBOX_CALLBACK_TOKEN",
        "SANDBOX_EGRESS_PROOF_SIGNING_KEY",
    }
)
IDENTITY_SUBJECT_KEYS = frozenset(
    {
        "OPENSANDBOX_EXTERNAL_EGRESS_GATEWAY_POLICY_SUBJECT",
        "OPENSANDBOX_EXTERNAL_EGRESS_CALLBACK_BOUNDARY_SUBJECT",
        "SANDBOX_RUNTIME_SUBJECT",
    }
)


class S72ReleaseContractError(RuntimeError):
    """Raised without projecting managed configuration values."""


@dataclass(frozen=True)
class ValidatedS72ReleaseContract:
    """Non-secret typed facts retained after configuration validation."""

    frontend_port: int
    executor_image_digest: str
    present_key_count: int

    def projection(self) -> dict[str, object]:
        return {
            "schema_version": SCHEMA_VERSION,
            "verified": True,
            "present_key_count": self.present_key_count,
            "required_key_count": len(REQUIRED_KEYS),
            "required_keys_present": True,
            "retired_cross_host_keys_absent": True,
            "sdk_selection_fail_closed": True,
            "sandbox_authority": "opensandbox",
            "executor_image_digest": self.executor_image_digest,
            "frontend_port": self.frontend_port,
            "attempt_credentials_required": True,
            "callback_attestation_identity_bound": True,
            "secret_values_projected": False,
        }


def _parse_env_file(path: Path) -> dict[str, str]:
    descriptor: int | None = None
    try:
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags)
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise S72ReleaseContractError(
                "managed configuration must be a regular non-link file"
            )
        with os.fdopen(descriptor, encoding="utf-8") as stream:
            descriptor = None
            lines = stream.read().splitlines()
        current = path.stat(follow_symlinks=False)
    except (OSError, UnicodeError) as exc:
        raise S72ReleaseContractError("managed configuration is unavailable") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
    if path.is_symlink() or (current.st_dev, current.st_ino) != (metadata.st_dev, metadata.st_ino):
        raise S72ReleaseContractError("managed configuration must be a regular non-link file")
    values: dict[str, str] = {}
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        key, separator, value = stripped.partition("=")
        if not separator or not re.fullmatch(r"[A-Z][A-Z0-9_]*", key) or key in values:
            raise S72ReleaseContractError("managed configuration shape is invalid")
        values[key] = value
    return values


def _csv(value: str) -> tuple[str, ...]:
    return tuple(item.strip() for item in value.split(","))


def _validate_model_upstream(value: str) -> None:
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as exc:
        raise S72ReleaseContractError("model upstream contract is invalid") from exc
    if (
        parsed.scheme != "http"
        or parsed.hostname != "host.docker.internal"
        or port != 3002
        or parsed.username
        or parsed.password
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise S72ReleaseContractError("model upstream contract is invalid")


def _validate_url(value: str, *, message: str, allow_path: bool = True) -> None:
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as exc:
        raise S72ReleaseContractError(message) from exc
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or (not allow_path and parsed.path not in {"", "/"})
        or (port is not None and not 1 <= port <= 65_535)
    ):
        raise S72ReleaseContractError(message)


def _validate_secret_authority(values: Mapping[str, str]) -> None:
    for key in SECRET_KEYS:
        value = values[key]
        if (
            not 16 <= len(value) <= 4096
            or PLACEHOLDER_RE.search(value)
            or any(ord(character) < 0x20 or ord(character) == 0x7F for character in value)
        ):
            raise S72ReleaseContractError("secret authority is invalid")


def _validated_contract(values: Mapping[str, str]) -> ValidatedS72ReleaseContract:
    retired = RETIRED_CROSS_HOST_KEYS.intersection(values)
    if retired:
        raise S72ReleaseContractError("retired cross-host configuration keys are present")
    if REQUIRED_KEYS.difference(values):
        raise S72ReleaseContractError("required s72 configuration keys are missing")
    if (
        values["WORKER_CLAUDE_AGENT_SDK_ENABLED"] != "true"
        or values["CLAUDE_AGENT_PERMISSION_MODE"] != "dontAsk"
        or _csv(values["CLAUDE_AGENT_ALLOWED_TOOLS"]) != ("Read", "Glob", "LS", "Bash")
        or _csv(values["CLAUDE_AGENT_DISALLOWED_TOOLS"])
        != ("Write", "Edit", "NotebookEdit")
    ):
        raise S72ReleaseContractError("SDK production selection is unsafe")
    if (
        values["SANDBOX_CONTAINER_PROVIDER"] != "opensandbox"
        or values["SANDBOX_SECURITY_PROFILE"] != "governed"
    ):
        raise S72ReleaseContractError("sandbox authority selection is unsafe")
    _validate_model_upstream(values["AI_PLATFORM_MODEL_UPSTREAM"])
    digest = values["OPENSANDBOX_EXECUTOR_IMAGE_DIGEST"]
    image = values["OPENSANDBOX_EXECUTOR_IMAGE"]
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest) or not IMMUTABLE_IMAGE_RE.fullmatch(image) or not image.endswith(f"@{digest}"):
        raise S72ReleaseContractError("OpenSandbox executor image is not immutable")
    if (
        values["OPENSANDBOX_ATTESTATION_PATH"]
        != "/v1/sandboxes/{sandbox_id}/attestation"
        or values["OPENSANDBOX_ATTESTATION_CONTRACT_VERSION"]
        != "ai-platform.opensandbox.topology-attestation.v1"
    ):
        raise S72ReleaseContractError("attestation authority is invalid")
    if any(not IDENTITY_SUBJECT_RE.fullmatch(values[key]) for key in IDENTITY_SUBJECT_KEYS):
        raise S72ReleaseContractError("identity subject authority is invalid")
    _validate_secret_authority(values)
    _validate_url(
        values["OPENSANDBOX_EXTERNAL_EGRESS_CAPABILITY_URL"],
        message="capability authority URL is invalid",
    )
    _validate_url(
        f"{values['OPENSANDBOX_PROTOCOL']}://{values['OPENSANDBOX_DOMAIN']}",
        message="OpenSandbox endpoint authority is invalid",
        allow_path=False,
    )
    try:
        frontend_port = int(values["AI_PLATFORM_FRONTEND_PORT"])
    except ValueError as exc:
        raise S72ReleaseContractError("frontend port authority is invalid") from exc
    if not 1 <= frontend_port <= 65_535:
        raise S72ReleaseContractError("frontend port authority is invalid")
    return ValidatedS72ReleaseContract(frontend_port, digest, len(values))


def validate_s72_environment(path: Path) -> dict[str, object]:
    """Validate one file and return a non-secret release projection."""
    return _validated_contract(_parse_env_file(Path(path))).projection()


def validate_managed_s72_contract(
    coordination_source: Path,
    commit: str,
    release_root: Path,
    env_file: Path | None = None,
) -> dict[str, object]:
    """Bind the typed contract to existing exact-source and managed-env authorities."""
    try:
        release_authority.assert_clean_coordination_source(
            Path(coordination_source),
            expected_commit=commit,
        )
        managed_env = release_authority.resolve_managed_env_file(Path(release_root), env_file)
    except release_authority.ReleaseAuthorityError as exc:
        raise S72ReleaseContractError(str(exc)) from None
    projection = validate_s72_environment(managed_env)
    return {
        **projection,
        "source_commit": commit,
        "source_authority_verified": True,
        "managed_env_authority_verified": True,
    }


def main(argv: Sequence[str] | None = None) -> int:
    """Run the source-only s72 configuration authority."""
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    validate = subparsers.add_parser("validate", help="Validate the managed s72 release contract")
    validate.add_argument("--coordination-source", type=Path, required=True)
    validate.add_argument("--commit", required=True)
    validate.add_argument("--release-root", type=Path, required=True)
    validate.add_argument("--env-file", type=Path)
    args = parser.parse_args(argv)
    try:
        payload = validate_managed_s72_contract(
            args.coordination_source,
            args.commit,
            args.release_root,
            args.env_file,
        )
    except S72ReleaseContractError as exc:
        payload = {"verified": False, "command": args.command, "error": str(exc)}
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 2
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
