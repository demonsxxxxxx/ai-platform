from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from tools import s72_release_contract as contract


def _environment() -> dict[str, str]:
    digest = "sha256:" + "1" * 64
    return {
        "AI_PLATFORM_MODEL_UPSTREAM": "http://host.docker.internal:3002",
        "AI_PLATFORM_FRONTEND_PORT": "18001",
        "WORKER_CLAUDE_AGENT_SDK_ENABLED": "true",
        "CLAUDE_AGENT_PERMISSION_MODE": "dontAsk",
        "CLAUDE_AGENT_ALLOWED_TOOLS": "Read,Glob,LS,Bash",
        "CLAUDE_AGENT_DISALLOWED_TOOLS": "Write,Edit,NotebookEdit",
        "SANDBOX_CONTAINER_PROVIDER": "opensandbox",
        "SANDBOX_SECURITY_PROFILE": "governed",
        "OPENSANDBOX_API_KEY": "a" * 32,
        "OPENSANDBOX_DOMAIN": "opensandbox.internal:8080",
        "OPENSANDBOX_PROTOCOL": "http",
        "OPENSANDBOX_EXECUTOR_IMAGE": f"registry.example/executor@{digest}",
        "OPENSANDBOX_EXECUTOR_IMAGE_DIGEST": digest,
        "OPENSANDBOX_ATTESTATION_PATH": "/v1/sandboxes/{sandbox_id}/attestation",
        "OPENSANDBOX_ATTESTATION_CONTRACT_VERSION": (
            "ai-platform.opensandbox.topology-attestation.v1"
        ),
        "OPENSANDBOX_EXTERNAL_EGRESS_CAPABILITY_URL": (
            "http://s72-broker-entry:8080/v1/capabilities/governed-egress"
        ),
        "OPENSANDBOX_EXTERNAL_EGRESS_CAPABILITY_TOKEN": "b" * 32,
        "OPENSANDBOX_EXTERNAL_EGRESS_GATEWAY_POLICY_SUBJECT": "s72/policy/v1",
        "OPENSANDBOX_EXTERNAL_EGRESS_CALLBACK_BOUNDARY_SUBJECT": "callbacks/v1",
        "SANDBOX_CALLBACK_TOKEN": "c" * 32,
        "SANDBOX_EGRESS_PROOF_SIGNING_KEY": "d" * 32,
        "SANDBOX_RUNTIME_SUBJECT": "s72/runsc/v1",
    }


def _write_environment(path: Path, values: dict[str, str]) -> None:
    path.write_text(
        "".join(f"{key}={value}\n" for key, value in values.items()),
        encoding="utf-8",
    )


def test_managed_contract_reuses_exact_source_and_env_authorities(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    env_file = tmp_path / ".env"
    _write_environment(env_file, _environment())
    commit = "f" * 40
    calls: list[tuple[object, ...]] = []

    def clean_source(path: Path, expected_commit: str) -> Path:
        calls.append(("source", path, expected_commit))
        return path

    def managed_env(release_root: Path, supplied: Path | None) -> Path:
        calls.append(("env", release_root, supplied))
        return env_file

    monkeypatch.setattr(contract.release_authority, "assert_clean_coordination_source", clean_source)
    monkeypatch.setattr(contract.release_authority, "resolve_managed_env_file", managed_env)

    projection = contract.validate_managed_s72_contract(
        tmp_path,
        commit,
        tmp_path / "releases",
    )

    assert calls == [
        ("source", tmp_path, commit),
        ("env", tmp_path / "releases", None),
    ]
    assert projection["schema_version"] == contract.SCHEMA_VERSION
    assert projection["source_commit"] == commit
    assert projection["sdk_selection_fail_closed"] is True
    assert projection["sandbox_authority"] == "opensandbox"
    rendered = json.dumps(projection, sort_keys=True)
    for secret in ("a" * 32, "b" * 32, "c" * 32, "d" * 32):
        assert secret not in rendered


def test_invalid_commit_fails_before_source_or_env_access(tmp_path: Path) -> None:
    with pytest.raises(contract.S72ReleaseContractError, match="full 40-character"):
        contract.validate_managed_s72_contract(
            tmp_path / "missing-source",
            "not-a-commit",
            tmp_path / "missing-release-root",
        )


@pytest.mark.parametrize("key", sorted(contract.RETIRED_CROSS_HOST_KEYS))
def test_retired_cross_host_keys_are_rejected(tmp_path: Path, key: str) -> None:
    env_file = tmp_path / ".env"
    values = _environment()
    values[key] = "legacy-value"
    _write_environment(env_file, values)

    with pytest.raises(contract.S72ReleaseContractError, match="retired cross-host"):
        contract.validate_s72_environment(env_file)


@pytest.mark.parametrize(
    ("key", "value", "message"),
    [
        ("WORKER_CLAUDE_AGENT_SDK_ENABLED", "false", "SDK production selection"),
        ("CLAUDE_AGENT_PERMISSION_MODE", "bypassPermissions", "SDK production selection"),
        ("CLAUDE_AGENT_ALLOWED_TOOLS", "Read,Glob,LS", "SDK production selection"),
        ("CLAUDE_AGENT_DISALLOWED_TOOLS", "", "SDK production selection"),
        ("SANDBOX_CONTAINER_PROVIDER", "fake", "sandbox authority selection"),
        ("SANDBOX_SECURITY_PROFILE", "trusted_internal", "sandbox authority selection"),
    ],
)
def test_unsafe_sdk_or_sandbox_selection_fails_closed(
    tmp_path: Path,
    key: str,
    value: str,
    message: str,
) -> None:
    env_file = tmp_path / ".env"
    values = _environment()
    values[key] = value
    _write_environment(env_file, values)

    with pytest.raises(contract.S72ReleaseContractError, match=message):
        contract.validate_s72_environment(env_file)


@pytest.mark.parametrize(
    ("key", "value", "message"),
    [
        ("OPENSANDBOX_EXECUTOR_IMAGE", "registry.example/executor:latest", "immutable"),
        ("OPENSANDBOX_EXECUTOR_IMAGE_DIGEST", "sha256:1234", "immutable"),
        ("AI_PLATFORM_MODEL_UPSTREAM", "http://postgres:5432", "model upstream"),
        ("OPENSANDBOX_ATTESTATION_PATH", "/attestation", "attestation"),
        ("SANDBOX_RUNTIME_SUBJECT", "contains whitespace", "identity subject"),
        ("SANDBOX_CALLBACK_TOKEN", "replace_me", "secret authority"),
    ],
)
def test_immutable_identity_and_secret_contracts_fail_closed(
    tmp_path: Path,
    key: str,
    value: str,
    message: str,
) -> None:
    env_file = tmp_path / ".env"
    values = _environment()
    values[key] = value
    _write_environment(env_file, values)

    with pytest.raises(contract.S72ReleaseContractError, match=message):
        contract.validate_s72_environment(env_file)


def test_duplicate_or_missing_environment_keys_are_rejected(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    values = _environment()
    values.pop("OPENSANDBOX_EXTERNAL_EGRESS_CAPABILITY_TOKEN")
    _write_environment(env_file, values)
    with pytest.raises(contract.S72ReleaseContractError, match="required s72"):
        contract.validate_s72_environment(env_file)

    _write_environment(env_file, _environment())
    with env_file.open("a", encoding="utf-8") as stream:
        stream.write("SANDBOX_RUNTIME_SUBJECT=duplicate\n")
    with pytest.raises(contract.S72ReleaseContractError, match="shape"):
        contract.validate_s72_environment(env_file)


def test_environment_read_is_bound_to_one_opened_object(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    env_file = tmp_path / ".env"
    replacement = tmp_path / "replacement.env"
    _write_environment(env_file, _environment())
    hostile = _environment()
    hostile["AI_PLATFORM_S72_BRIDGE_PORT"] = "18443"
    _write_environment(replacement, hostile)
    original_read_text = Path.read_text

    def replace_before_read(path: Path, *args: object, **kwargs: object) -> str:
        if path == env_file:
            os.replace(replacement, env_file)
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", replace_before_read)

    assert contract.validate_s72_environment(env_file)["verified"] is True


@pytest.mark.parametrize(
    ("key", "value", "message"),
    [
        ("OPENSANDBOX_DOMAIN", "opensandbox.internal:8080/admin", "endpoint authority"),
        (
            "OPENSANDBOX_EXTERNAL_EGRESS_CAPABILITY_URL",
            "http://s72-broker-entry:8080/v1/capabilities?token=unsafe",
            "capability authority",
        ),
    ],
)
def test_endpoint_authorities_reject_path_or_query(
    tmp_path: Path,
    key: str,
    value: str,
    message: str,
) -> None:
    env_file = tmp_path / ".env"
    values = _environment()
    values[key] = value
    _write_environment(env_file, values)

    with pytest.raises(contract.S72ReleaseContractError, match=message):
        contract.validate_s72_environment(env_file)


def test_cli_projects_only_redacted_contract_evidence(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        contract,
        "validate_managed_s72_contract",
        lambda *_args, **_kwargs: {
            "schema_version": contract.SCHEMA_VERSION,
            "verified": True,
            "required_keys_present": True,
        },
    )

    assert contract.main(
        [
            "validate",
            "--coordination-source",
            str(tmp_path),
            "--commit",
            "a" * 40,
            "--release-root",
            str(tmp_path / "releases"),
        ]
    ) == 0
    assert json.loads(capsys.readouterr().out) == {
        "required_keys_present": True,
        "schema_version": contract.SCHEMA_VERSION,
        "verified": True,
    }
