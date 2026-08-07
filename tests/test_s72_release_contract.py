from __future__ import annotations

import json
import multiprocessing
import os
from pathlib import Path
import subprocess

import pytest
import yaml

from app.runtime.sandbox import container_provider as runtime_provider
from tools import s72_release_contract as contract


REPO_ROOT = Path(__file__).resolve().parents[1]


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
            "http://127.0.0.1:18043/v1/capabilities/governed-egress"
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


def _git(*args: str, cwd: Path) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
        timeout=15,
    ).stdout.strip()


def _clean_source(tmp_path: Path) -> tuple[Path, str]:
    source = tmp_path / "source"
    source.mkdir()
    _git("init", cwd=source)
    _git("config", "user.email", "stack-a@example.invalid", cwd=source)
    _git("config", "user.name", "Stack A Test", cwd=source)
    (source / "authority.txt").write_text("stack-a\n", encoding="utf-8")
    _git("add", "authority.txt", cwd=source)
    _git("commit", "-m", "fixture", cwd=source)
    return source, _git("rev-parse", "HEAD", cwd=source)


def _managed_layout(tmp_path: Path) -> tuple[Path, Path]:
    managed_root = tmp_path / "managed"
    release_root = managed_root / "releases"
    release_root.mkdir(parents=True)
    env_file = managed_root / "deploy" / "ai-platform" / ".env"
    env_file.parent.mkdir(parents=True)
    _write_environment(env_file, _environment())
    env_file.chmod(0o600)
    return release_root, env_file


def _adapt_posix_metadata_on_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    if os.name != "posix":
        monkeypatch.setattr(
            contract.release_authority,
            "_posix_owner_mode",
            lambda path: (1000, 0o600 if Path(path).name == ".env" else 0o700),
        )


def _runtime_accepts_capability_endpoint(value: str) -> bool:
    try:
        runtime_provider._normalized_capability_profile_endpoint(value)
    except runtime_provider.OpenSandboxCapabilityAdmissionError:
        return False
    return True


def _runtime_accepts_capability_token(value: str) -> bool:
    try:
        runtime_provider._validated_configured_capability_token(value)
    except runtime_provider.OpenSandboxCapabilityAdmissionError:
        return False
    return True


def test_required_backend_job_collects_linux_special_node_contract() -> None:
    workflow = yaml.safe_load(
        (REPO_ROOT / ".github" / "workflows" / "ai-platform-backend.yml").read_text(
            encoding="utf-8"
        )
    )
    sandbox_job = workflow["jobs"]["sandbox-provider"]
    targeted_step = next(
        step
        for step in sandbox_job["steps"]
        if step.get("name") == "Run sandbox provider targeted tests"
    )

    assert sandbox_job["runs-on"] == "ubuntu-latest"
    assert targeted_step["run"].count("tests/test_s72_release_contract.py") == 1
    assert "sandbox-provider" in workflow["jobs"]["required"]["needs"]


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


def test_managed_environment_rejects_real_inode_replacement(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source, commit = _clean_source(tmp_path)
    release_root, env_file = _managed_layout(tmp_path)
    replacement = tmp_path / "replacement.env"
    _write_environment(replacement, _environment())
    replacement.chmod(0o600)
    original_inode = env_file.stat().st_ino
    original_resolver = contract.release_authority.resolve_managed_env_file
    calls = 0
    replacement_completed = False

    def replace_during_revalidation(release: Path, supplied: Path | None) -> Path:
        nonlocal calls, replacement_completed
        calls += 1
        resolved = original_resolver(release, supplied)
        if calls == 2:
            os.replace(replacement, env_file)
            replacement_completed = True
        return resolved

    _adapt_posix_metadata_on_windows(monkeypatch)
    monkeypatch.setattr(
        contract.release_authority,
        "resolve_managed_env_file",
        replace_during_revalidation,
    )

    with pytest.raises(
        contract.S72ReleaseContractError,
        match="identity changed|unavailable",
    ):
        contract.validate_managed_s72_contract(source, commit, release_root)

    assert calls == 2
    if replacement_completed:
        assert env_file.stat().st_ino != original_inode


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


@pytest.mark.parametrize(
    ("value", "accepted"),
    [
        ("http://127.0.0.1:18043/v1/capabilities", True),
        ("http://localhost:18043/v1/capabilities", True),
        ("https://10.1.2.3:8443/v1/capabilities", True),
        (" http://127.0.0.1:18043/v1/capabilities ", True),
        ("https://8.8.8.8/v1/capabilities", False),
        ("https://capability.internal/v1/capabilities", False),
        ("http://10.1.2.3/v1/capabilities", False),
        ("https://169.254.1.1/v1/capabilities", False),
        ("https://user:pass@10.1.2.3/path", False),
        ("https://10.1.2.3/path?token=unsafe", False),
        ("https://10.1.2.3/path#fragment", False),
        ("https://10.1.2.3:0/path", False),
    ],
)
def test_capability_endpoint_matches_runtime_authority(
    tmp_path: Path,
    value: str,
    accepted: bool,
) -> None:
    env_file = tmp_path / ".env"
    values = _environment()
    values["OPENSANDBOX_EXTERNAL_EGRESS_CAPABILITY_URL"] = value
    _write_environment(env_file, values)

    runtime_accepted = _runtime_accepts_capability_endpoint(value)
    try:
        contract.validate_s72_environment(env_file)
    except contract.S72ReleaseContractError:
        candidate_accepted = False
    else:
        candidate_accepted = True

    assert runtime_accepted is accepted
    assert candidate_accepted is runtime_accepted


@pytest.mark.parametrize(
    ("value", "accepted"),
    [
        ("x", True),
        ("replace_me", True),
        ("a" * 4096, True),
        ("", False),
        ("contains space", False),
        ("contains\ttab", False),
        ("contains\x7fdelete", False),
        ("non-ascii-\N{SNOWMAN}", False),
        ("a" * 4097, False),
    ],
)
def test_capability_token_matches_runtime_authority(
    tmp_path: Path,
    value: str,
    accepted: bool,
) -> None:
    env_file = tmp_path / ".env"
    values = _environment()
    values["OPENSANDBOX_EXTERNAL_EGRESS_CAPABILITY_TOKEN"] = value
    _write_environment(env_file, values)

    runtime_accepted = _runtime_accepts_capability_token(value)
    try:
        contract.validate_s72_environment(env_file)
    except contract.S72ReleaseContractError:
        candidate_accepted = False
    else:
        candidate_accepted = True

    assert runtime_accepted is accepted
    assert candidate_accepted is runtime_accepted


def test_cli_projects_only_redacted_contract_evidence(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    source, commit = _clean_source(tmp_path)
    release_root, _ = _managed_layout(tmp_path)
    _adapt_posix_metadata_on_windows(monkeypatch)

    assert contract.main(
        [
            "validate",
            "--coordination-source",
            str(source),
            "--commit",
            commit,
            "--release-root",
            str(release_root),
        ]
    ) == 0
    assert json.loads(capsys.readouterr().out) == {
        "attempt_credentials_required": True,
        "callback_attestation_identity_bound": True,
        "command": "validate",
        "managed_env_authority_verified": True,
        "present_key_count": len(_environment()),
        "required_key_count": len(contract.REQUIRED_KEYS),
        "required_keys_present": True,
        "retired_cross_host_keys_absent": True,
        "sandbox_authority": "opensandbox",
        "schema_version": contract.SCHEMA_VERSION,
        "sdk_selection_fail_closed": True,
        "secret_values_projected": False,
        "source_authority_verified": True,
        "verified": True,
    }


def test_cli_failure_projects_only_a_classification(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    source, commit = _clean_source(tmp_path)
    release_root, env_file = _managed_layout(tmp_path)
    values = _environment()
    values["AI_PLATFORM_S72_BRIDGE_TLS_KEY_FILE"] = "env-derived-value-must-not-escape"
    _write_environment(env_file, values)
    _adapt_posix_metadata_on_windows(monkeypatch)

    assert contract.main(
        [
            "validate",
            "--coordination-source",
            str(source),
            "--commit",
            commit,
            "--release-root",
            str(release_root),
        ]
    ) == 2
    assert json.loads(capsys.readouterr().out) == {
        "command": "validate",
        "error_category": "contract_invalid",
        "verified": False,
    }


def _special_node_worker(
    source: str,
    commit: str,
    release_root: str,
    env_file: str,
    node_kind: str,
    result: multiprocessing.Queue[str],
) -> None:
    env_path = Path(env_file)
    original_resolver = contract.release_authority.resolve_managed_env_file

    def replace_after_validation(release: Path, supplied: Path | None) -> Path:
        resolved = original_resolver(release, supplied)
        env_path.unlink()
        if node_kind == "fifo":
            os.mkfifo(env_path, mode=0o600)
        else:
            target = env_path.with_name("symlink-target.env")
            _write_environment(target, _environment())
            target.chmod(0o600)
            env_path.symlink_to(target)
        return resolved

    contract.release_authority.resolve_managed_env_file = replace_after_validation
    try:
        contract.validate_managed_s72_contract(
            Path(source),
            commit,
            Path(release_root),
        )
    except contract.S72ReleaseContractError:
        result.put("rejected")
    else:
        result.put("accepted")


@pytest.mark.skipif(os.name != "posix", reason="required Linux special-node evidence")
@pytest.mark.parametrize("node_kind", ["fifo", "symlink"])
def test_managed_environment_special_node_replacement_is_bounded(
    tmp_path: Path,
    node_kind: str,
) -> None:
    source, commit = _clean_source(tmp_path)
    release_root, env_file = _managed_layout(tmp_path)
    context = multiprocessing.get_context("fork")
    result = context.Queue()
    process = context.Process(
        target=_special_node_worker,
        args=(
            str(source),
            commit,
            str(release_root),
            str(env_file),
            node_kind,
            result,
        ),
    )
    process.start()
    process.join(timeout=3)
    if process.is_alive():
        process.terminate()
        process.join(timeout=2)
        pytest.fail(f"managed environment {node_kind} replacement blocked before fstat")

    assert process.exitcode == 0
    assert result.get(timeout=1) == "rejected"
