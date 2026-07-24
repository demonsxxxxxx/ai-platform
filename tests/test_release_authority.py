import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import traceback
from datetime import datetime, timedelta, timezone

import pytest
import yaml

import tools.release_authority as release_authority

from tools.release_authority import (
    ReleaseAuthorityError,
    assert_clean_commit,
    build_image_references,
    build_parity_report,
    collect_live_parity,
    deploy_clean_commit,
    preserve_dirty_source,
)


ROOT = Path(__file__).resolve().parents[1]
COMPOSE = ROOT / "deploy" / "ai-platform" / "docker-compose.yml"
SANDBOX_COMPOSE = ROOT / "deploy" / "ai-platform" / "docker-compose.sandbox.yml"
OPENSANDBOX_COMPOSE = ROOT / "deploy" / "ai-platform" / "docker-compose.opensandbox.yml"
RUNBOOK = ROOT / "docs" / "operations" / "211-release-operations-runbook.md"
LEGACY_FRONTEND_COMPOSE = ROOT / "deploy" / "ai-platform" / "docker-compose.frontend.yml"
AUTHORITATIVE_REPOSITORY = "https://github.com/demonsxxxxxx/ai-platform.git"
SANDBOX_IMAGE_ID = "sha256:" + "e" * 64
WORKER_HEARTBEAT_FILENAME = "ai-platform-worker-runtime-heartbeat.json"
COMPOSE_RELATIVE_PATH = "deploy/ai-platform/docker-compose.yml"
SANDBOX_COMPOSE_RELATIVE_PATH = "deploy/ai-platform/docker-compose.sandbox.yml"
OPENSANDBOX_COMPOSE_RELATIVE_PATH = "deploy/ai-platform/docker-compose.opensandbox.yml"


def test_repo_local_compose_is_the_only_frontend_owner_and_binds_one_commit():
    compose = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
    services = compose["services"]

    assert "frontend" in services
    assert not LEGACY_FRONTEND_COMPOSE.exists()
    for service_name in ("api", "worker", "frontend"):
        labels = services[service_name]["labels"]
        assert labels["ai-platform.source-commit"] == "${AI_PLATFORM_SOURCE_COMMIT:?set AI_PLATFORM_SOURCE_COMMIT}"
        assert labels["ai-platform.source-dirty"] == "false"
        assert labels["ai-platform.release-owner"] == "repo-local-compose"
        assert labels["ai-platform.release-role"] == service_name
    for service_name in ("api", "worker"):
        assert services[service_name]["environment"]["AI_PLATFORM_RUNTIME_COMMIT"] == (
            "${AI_PLATFORM_SOURCE_COMMIT:?set AI_PLATFORM_SOURCE_COMMIT}"
        )
        assert services[service_name]["environment"]["SANDBOX_EXECUTOR_IMAGE"] == (
            "${SANDBOX_EXECUTOR_IMAGE:-ai-platform:local}"
        )


def test_release_authority_rejects_non_authoritative_origin(monkeypatch, tmp_path):
    from tools.release_authority import authoritative_repository

    monkeypatch.setattr(
        "tools.release_authority._git",
        lambda repo, *args: "https://example.invalid/fork.git\n",
    )

    try:
        authoritative_repository(tmp_path)
    except ReleaseAuthorityError as exc:
        assert "authoritative repository mismatch" in str(exc)
    else:
        raise AssertionError("a local origin rewrite must not redefine release authority")


def test_repo_local_compose_requires_immutable_backend_and_frontend_images():
    compose = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
    services = compose["services"]

    assert services["api"]["image"] == "${AI_PLATFORM_IMAGE:?set AI_PLATFORM_IMAGE}"
    assert services["worker"]["image"] == "${AI_PLATFORM_IMAGE:?set AI_PLATFORM_IMAGE}"
    assert services["frontend"]["image"] == "${AI_PLATFORM_FRONTEND_IMAGE:?set AI_PLATFORM_FRONTEND_IMAGE}"


def test_sandbox_compose_overlay_preserves_live_docker_provider_and_mounts():
    compose = yaml.safe_load(SANDBOX_COMPOSE.read_text(encoding="utf-8"))
    services = compose["services"]

    assert services["api"]["environment"]["SANDBOX_CONTAINER_PROVIDER"] == "docker"
    assert services["worker"]["environment"]["SANDBOX_CONTAINER_PROVIDER"] == "docker"
    assert "${DOCKER_SOCKET_GID:?set DOCKER_SOCKET_GID}" in services["worker"]["group_add"]
    assert "/var/run/docker.sock:/var/run/docker.sock" in services["worker"]["volumes"]
    workspace_mount = (
        "${SANDBOX_WORKSPACE_ROOT:-/tmp/ai-platform-sandbox-workspaces}:"
        "${SANDBOX_WORKSPACE_ROOT:-/tmp/ai-platform-sandbox-workspaces}"
    )
    assert workspace_mount in services["api"]["volumes"]
    assert workspace_mount in services["worker"]["volumes"]


def test_opensandbox_compose_overlay_disables_docker_egress_policy_for_api_and_worker():
    base_services = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))["services"]
    docker_services = yaml.safe_load(SANDBOX_COMPOSE.read_text(encoding="utf-8"))["services"]
    opensandbox_services = yaml.safe_load(
        OPENSANDBOX_COMPOSE.read_text(encoding="utf-8")
    )["services"]

    for service_name in ("api", "worker"):
        base_environment = base_services[service_name]["environment"]
        docker_environment = docker_services[service_name]["environment"]
        opensandbox_environment = opensandbox_services[service_name]["environment"]

        assert base_environment["SANDBOX_EGRESS_POLICY_ENABLED"] == (
            "${SANDBOX_EGRESS_POLICY_ENABLED:-false}"
        )
        assert "SANDBOX_EGRESS_POLICY_ENABLED" not in docker_environment
        assert docker_environment["SANDBOX_CONTAINER_PROVIDER"] == "docker"
        assert opensandbox_environment["SANDBOX_CONTAINER_PROVIDER"] == "opensandbox"
        assert opensandbox_environment["SANDBOX_EGRESS_POLICY_ENABLED"] == "false"

        release_environment = {
            **base_environment,
            "SANDBOX_EGRESS_POLICY_ENABLED": "true",
        }
        docker_merged_environment = {**release_environment, **docker_environment}
        opensandbox_merged_environment = {
            **release_environment,
            **opensandbox_environment,
        }
        assert docker_merged_environment["SANDBOX_EGRESS_POLICY_ENABLED"] == "true"
        assert opensandbox_merged_environment["SANDBOX_EGRESS_POLICY_ENABLED"] == "false"


def test_runbook_states_governed_proof_key_rotation_and_sandbox_overlay_contract():
    text = RUNBOOK.read_text(encoding="utf-8")
    contract_text = " ".join(text.split())

    assert "SANDBOX_EGRESS_PROOF_KEY_ID=<non-secret-current-key-id>" in text
    assert "SANDBOX_EGRESS_PROOF_PREVIOUS_KEYS_JSON=<empty-or-bounded-read-only-previous-key-map>" in text
    assert text.count("python3 tools/release_authority.py deploy-main-commit") == 1
    assert "Resolve `SOURCE`" in text
    assert "and `ROOT` from the current 211 host mapping" in text
    assert "`docs/agent-rules/ai-platform-guardrails.md`, the authoritative source" in text
    assert ': "${SOURCE:?set SOURCE to the guardrails-designated 211 coordination checkout}"' in text
    assert ': "${ROOT:?set ROOT to the guardrails-designated 211 managed release root}"' in text
    assert '--release-root "$ROOT/releases"' in text
    assert '--canonical-build-timeout-seconds 1800' in text
    command_bound = re.search(
        r"timeout --signal=INT --kill-after=(\d+)s (\d+)s",
        text,
    )
    durable_bound = re.search(r"durable runner with a (\d+)-second deadline", text)
    assert command_bound is not None
    assert durable_bound is not None
    assert "timeout --signal=TERM" not in text
    kill_grace_seconds = int(command_bound.group(1))
    command_timeout_seconds = int(command_bound.group(2))
    durable_timeout_seconds = int(durable_bound.group(1))
    default_timeout_slot_counts = {
        "coordination_source": 2,
        "materialize_existing_checkout": 11,
        "initial_managed_target": 4,
        "current_runtime_and_parity": 14,
        "runtime_diff": 1,
        "deploy_and_converge": 22,
        "final_parity": 11,
    }
    assert sum(default_timeout_slot_counts.values()) == 65
    aggregate_stage_maximum_seconds = (
        2 * release_authority.CANONICAL_DEPENDENCY_BUILD_TIMEOUT_SECONDS
        + sum(default_timeout_slot_counts.values())
        * release_authority.DEFAULT_SUBPROCESS_TIMEOUT_SECONDS
        + 4 * release_authority.HTTP_PROBE_TIMEOUT_SECONDS
    )
    assert command_timeout_seconds >= aggregate_stage_maximum_seconds
    assert command_timeout_seconds - aggregate_stage_maximum_seconds >= (
        2 * release_authority.DEFAULT_SUBPROCESS_TIMEOUT_SECONDS
    )
    assert kill_grace_seconds > 2 * release_authority.PROCESS_TREE_TERMINATION_GRACE_SECONDS
    assert durable_timeout_seconds >= (
        command_timeout_seconds
        + kill_grace_seconds
        + release_authority.DEFAULT_SUBPROCESS_TIMEOUT_SECONDS
    )
    assert "Do not add `--env-file`" in text
    assert "normal flow" in text
    assert "$ROOT/deploy/ai-platform/.env" in text
    assert "tracked, staged, and ordinary untracked" in text
    assert "Ignored-only artifacts" in text
    assert "immutable target checkout" in text
    assert "mode `0600`" in text
    assert "must equal that exact canonical path after normalization" in contract_text
    assert "commit/tree is the tracked-source manifest" in contract_text
    assert "symlinks and non-regular entries" in contract_text
    assert "there is no separate manifest" in contract_text
    assert "world-writable" in contract_text
    assert "before any Git command or fetch" in contract_text
    assert "Only after that local trust gate" in contract_text
    assert "--compose-file deploy/ai-platform/docker-compose.yml" in text
    assert "--compose-file deploy/ai-platform/docker-compose.sandbox.yml" in text
    assert "The base Compose and `docker-compose.sandbox.yml` Docker rollback path do not" in text
    assert "OpenSandbox overlay only under an" in text
    assert "explicit provider-transition release charter" in text
    assert "exact provider-overlay ownership transition" in text
    assert "base-only, reordered, duplicate" in text
    assert "missing, extra, or arbitrary overlay" in text
    assert "ai-platform-phaseb" in text
    assert "--env-file <release-root>/deploy/ai-platform/.env" not in text
    assert "--env-file deploy/ai-platform/.env" not in text
    assert '--env-file "$ROOT/deploy/ai-platform/.env"' in text


def test_backend_and_frontend_images_publish_release_authority_labels():
    backend = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    frontend = (ROOT / "frontend" / "web" / "Dockerfile").read_text(encoding="utf-8")

    for dockerfile, role in ((backend, "backend"), (frontend, "frontend")):
        assert "ARG AI_PLATFORM_BUILD_REPOSITORY=unknown" in dockerfile
        assert "LABEL ai-platform.source-commit=$AI_PLATFORM_BUILD_COMMIT" in dockerfile
        assert 'LABEL ai-platform.build-dirty="$AI_PLATFORM_BUILD_DIRTY"' in dockerfile
        assert "LABEL ai-platform.source-repository=$AI_PLATFORM_BUILD_REPOSITORY" in dockerfile
        assert f"LABEL ai-platform.release-role={role}" in dockerfile

    backend_stage = backend.split("FROM python:3.11-slim", 1)[1]
    assert "ARG AI_PLATFORM_BUILD_COMMIT=unknown" in backend_stage
    assert "ARG AI_PLATFORM_BUILD_DIRTY=unknown" in backend_stage
    assert "ARG AI_PLATFORM_BUILD_REPOSITORY=unknown" in backend_stage


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _init_repo(repo: Path) -> str:
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.name", "Release Test")
    _git(repo, "config", "user.email", "release@example.invalid")
    (repo / "tracked.txt").write_text("baseline\n", encoding="utf-8")
    _git(repo, "add", "tracked.txt")
    _git(repo, "commit", "-m", "baseline")
    return _git(repo, "rev-parse", "HEAD")


def _write_compose_files(repo_root: Path) -> tuple[Path, Path]:
    compose_dir = repo_root / "deploy" / "ai-platform"
    compose_dir.mkdir(parents=True, exist_ok=True)
    main = compose_dir / "docker-compose.yml"
    overlay = compose_dir / "docker-compose.sandbox.yml"
    main.write_text("services: {}\n", encoding="utf-8")
    overlay.write_text("services: {}\n", encoding="utf-8")
    return main, overlay


def _write_provider_compose_files(repo_root: Path) -> tuple[Path, Path, Path]:
    main, sandbox = _write_compose_files(repo_root)
    opensandbox = sandbox.with_name("docker-compose.opensandbox.yml")
    opensandbox.write_text("services: {}\n", encoding="utf-8")
    return main, sandbox, opensandbox


def _prepare_managed_release_layout(monkeypatch, tmp_path: Path) -> tuple[Path, Path, Path]:
    managed_root = tmp_path / "managed"
    release_root = managed_root / "releases"
    env_file = managed_root / "deploy" / "ai-platform" / ".env"
    release_root.mkdir(parents=True)
    env_file.parent.mkdir(parents=True)
    env_file.write_text("PRIVATE_VALUE=must-not-be-read\n", encoding="utf-8")
    monkeypatch.setattr(
        release_authority,
        "_posix_owner_mode",
        lambda path: (1000, 0o600 if Path(path) == env_file else 0o700),
        raising=False,
    )
    return managed_root, release_root, env_file


def _prepare_managed_target_checkout(
    monkeypatch,
    tmp_path: Path,
) -> tuple[Path, Path, Path, Path, str]:
    managed_root = tmp_path / "m"
    release_root = managed_root / "releases"
    release_root.mkdir(parents=True)
    staging = release_root / "staging"
    commit = _init_repo(staging)
    checkout = release_root / commit
    staging.rename(checkout)
    env_file = managed_root / "deploy" / "ai-platform" / ".env"
    env_file.parent.mkdir(parents=True)
    env_file.write_text("PRIVATE_VALUE=must-not-be-read\n", encoding="utf-8")

    def owner_mode(path):
        candidate = Path(path)
        if candidate == env_file:
            return (1000, 0o600)
        return (1000, 0o755 if candidate.is_dir() else 0o644)

    monkeypatch.setattr(release_authority, "_posix_owner_mode", owner_mode)
    return managed_root, release_root, checkout, env_file, commit


def _compose_config_value(*paths: Path) -> str:
    return ",".join(path.resolve().as_posix() for path in paths)


def _owned_container_payload(role: str, compose_dir: Path, config_files: str) -> list[dict]:
    return [
        {
            "Image": "sha256:old",
            "Config": {
                "Image": f"ai-platform-{role}:old",
                "Labels": {
                    "ai-platform.release-owner": "repo-local-compose",
                    "ai-platform.release-role": role,
                    "com.docker.compose.project.working_dir": compose_dir.resolve().as_posix(),
                    "com.docker.compose.project.config_files": config_files,
                    "com.docker.compose.project": "ai-platform-phaseb",
                    "com.docker.compose.service": role,
                    "com.docker.compose.oneoff": "False",
                    "com.docker.compose.config-hash": "config-hash",
                },
            },
        }
    ]


def test_clean_commit_and_immutable_image_reference_contract(tmp_path):
    repo = tmp_path / "repo"
    commit = _init_repo(repo)

    assert assert_clean_commit(repo, commit) == commit
    assert build_image_references(commit) == {
        "backend": f"ai-platform:{commit}",
        "frontend": f"ai-platform-frontend:{commit}",
    }


def test_deploy_main_derives_managed_env_and_allows_ignored_coordination_pyc(
    monkeypatch,
    tmp_path,
):
    commit = "a" * 40
    source = tmp_path / "coordination-source"
    _init_repo(source)
    (source / ".gitignore").write_text("__pycache__/\n", encoding="utf-8")
    _git(source, "add", ".gitignore")
    _git(source, "commit", "-m", "ignore bytecode")
    ignored = source / "tools" / "__pycache__" / "release_authority.cpython-312.pyc"
    ignored.parent.mkdir(parents=True)
    ignored.write_bytes(b"ignored coordination artifact")
    managed_root, release_root, managed_env = _prepare_managed_release_layout(
        monkeypatch,
        tmp_path,
    )
    checkout = release_root / commit
    observed: dict[str, object] = {}

    monkeypatch.setattr(
        "tools.release_authority.materialize_main_checkout",
        lambda root, requested: checkout,
    )

    def fake_deploy(repo_root, requested, **kwargs):
        observed.update(kwargs)
        return {"commit": requested}

    monkeypatch.setattr("tools.release_authority.deploy_clean_commit", fake_deploy)
    monkeypatch.setattr(
        "tools.release_authority.collect_live_parity",
        lambda *args, **kwargs: {"verified": True, "mismatches": []},
    )

    result = release_authority.deploy_main_commit(
        release_root,
        commit,
        docker_cmd="sudo -n docker",
        env_file=None,
        coordination_source=source,
        replace_known_manual_frontend=False,
    )

    assert result["checkout"] == str(checkout)
    assert observed["env_file"] == managed_env
    assert observed["managed_release_root"] == release_root
    assert not (source / "deploy" / "ai-platform" / ".env").exists()
    assert managed_root.is_dir()


@pytest.mark.parametrize("dirty_kind", ["tracked", "staged", "ordinary-untracked"])
def test_deploy_main_blocks_coordination_dirt_before_target_materialization(
    monkeypatch,
    tmp_path,
    dirty_kind,
):
    source = tmp_path / "coordination-source"
    _init_repo(source)
    if dirty_kind == "ordinary-untracked":
        (source / "ordinary-untracked.txt").write_text("dirty\n", encoding="utf-8")
    else:
        (source / "tracked.txt").write_text("dirty\n", encoding="utf-8")
        if dirty_kind == "staged":
            _git(source, "add", "tracked.txt")
    _, release_root, _ = _prepare_managed_release_layout(monkeypatch, tmp_path)
    materialized: list[Path] = []

    monkeypatch.setattr(
        "tools.release_authority.materialize_main_checkout",
        lambda root, requested: materialized.append(Path(root)),
    )

    with pytest.raises(
        ReleaseAuthorityError,
        match="^coordination-source-cleanliness gate failed:",
    ):
        release_authority.deploy_main_commit(
            release_root,
            "b" * 40,
            docker_cmd="docker",
            env_file=None,
            coordination_source=source,
            replace_known_manual_frontend=False,
        )

    assert materialized == []


def test_managed_env_missing_and_relative_override_fail_before_materialization(
    monkeypatch,
    tmp_path,
):
    managed_root = tmp_path / "managed"
    release_root = managed_root / "releases"
    release_root.mkdir(parents=True)
    materialized: list[Path] = []
    monkeypatch.setattr(
        "tools.release_authority.materialize_main_checkout",
        lambda root, requested: materialized.append(Path(root)),
    )

    with pytest.raises(
        ReleaseAuthorityError,
        match="^managed-env-file-presence gate failed:",
    ):
        release_authority.deploy_main_commit(
            release_root,
            "c" * 40,
            docker_cmd="docker",
            env_file=None,
            replace_known_manual_frontend=False,
        )
    with pytest.raises(
        ReleaseAuthorityError,
        match="^managed-env-path gate failed:",
    ):
        release_authority.deploy_main_commit(
            release_root,
            "c" * 40,
            docker_cmd="docker",
            env_file=Path("deploy/ai-platform/.env"),
            replace_known_manual_frontend=False,
        )

    assert materialized == []


def test_managed_env_symlink_is_rejected_without_reading_contents(monkeypatch, tmp_path):
    managed_root = tmp_path / "managed"
    release_root = managed_root / "releases"
    release_root.mkdir(parents=True)
    actual = managed_root / "operator-held.env"
    actual.write_text("PRIVATE_VALUE=must-not-be-read\n", encoding="utf-8")
    linked = managed_root / "deploy" / "ai-platform" / ".env"
    linked.parent.mkdir(parents=True)
    try:
        linked.symlink_to(actual)
    except OSError:
        linked.write_text("placeholder\n", encoding="utf-8")
        original = release_authority._is_link_or_junction
        monkeypatch.setattr(
            "tools.release_authority._is_link_or_junction",
            lambda path: Path(path) == linked or original(Path(path)),
        )
    monkeypatch.setattr(
        Path,
        "read_text",
        lambda *args, **kwargs: pytest.fail("managed env contents must never be read"),
    )

    with pytest.raises(
        ReleaseAuthorityError,
        match="^managed-env-file-safety gate failed:",
    ):
        release_authority.resolve_managed_env_file(release_root, None)


@pytest.mark.parametrize(
    ("env_metadata", "expected_gate"),
    [
        ((1001, 0o600), "managed-env-file-ownership"),
        ((1000, 0o640), "managed-env-file-mode"),
    ],
)
def test_managed_env_owner_and_mode_fail_closed(
    monkeypatch,
    tmp_path,
    env_metadata,
    expected_gate,
):
    managed_root = tmp_path / "managed"
    release_root = managed_root / "releases"
    env_file = managed_root / "deploy" / "ai-platform" / ".env"
    release_root.mkdir(parents=True)
    env_file.parent.mkdir(parents=True)
    env_file.write_text("PRIVATE_VALUE=must-not-be-read\n", encoding="utf-8")
    monkeypatch.setattr(
        release_authority,
        "_posix_owner_mode",
        lambda path: env_metadata if Path(path) == env_file else (1000, 0o700),
        raising=False,
    )

    with pytest.raises(ReleaseAuthorityError, match=rf"^{expected_gate} gate failed:"):
        release_authority.resolve_managed_env_file(release_root, None)


def test_managed_env_external_same_owner_0600_override_is_rejected(
    monkeypatch,
    tmp_path,
):
    managed_root, release_root, default_env = _prepare_managed_release_layout(
        monkeypatch,
        tmp_path,
    )
    override = managed_root / "operator" / "release.env"
    override.parent.mkdir()
    override.write_text("PRIVATE_VALUE=must-not-be-read\n", encoding="utf-8")
    monkeypatch.setattr(
        release_authority,
        "_posix_owner_mode",
        lambda path: (1000, 0o600 if Path(path) == override else 0o700),
        raising=False,
    )
    monkeypatch.setattr(
        Path,
        "read_text",
        lambda *args, **kwargs: pytest.fail("managed env contents must never be read"),
    )
    materialized: list[Path] = []
    monkeypatch.setattr(
        release_authority,
        "materialize_main_checkout",
        lambda root, requested: materialized.append(Path(root)),
    )

    with pytest.raises(
        ReleaseAuthorityError,
        match="^managed-env-path gate failed:",
    ):
        release_authority.deploy_main_commit(
            release_root,
            "d" * 40,
            docker_cmd="docker",
            env_file=override,
            replace_known_manual_frontend=False,
        )
    assert default_env.is_file()
    assert materialized == []


def test_managed_env_exact_canonical_override_is_accepted_without_reading_contents(
    monkeypatch,
    tmp_path,
):
    _, release_root, canonical_env = _prepare_managed_release_layout(
        monkeypatch,
        tmp_path,
    )
    monkeypatch.setattr(
        Path,
        "read_text",
        lambda *args, **kwargs: pytest.fail("managed env contents must never be read"),
    )

    assert release_authority.resolve_managed_env_file(
        release_root,
        canonical_env,
    ) == canonical_env


def test_managed_target_checkout_accepts_safe_exact_git_tree(monkeypatch, tmp_path):
    _, release_root, checkout, _, commit = _prepare_managed_target_checkout(
        monkeypatch,
        tmp_path,
    )

    assert release_authority.assert_managed_target_checkout(
        checkout,
        commit,
        release_root,
    ) == commit


@pytest.mark.parametrize(
    ("unsafe_subject", "unsafe_metadata", "expected_gate"),
    [
        ("checkout", (1001, 0o755), "target-checkout-authority-ownership"),
        ("checkout", (1000, 0o775), "target-checkout-authority-mode"),
        ("tracked-file", (1001, 0o644), "target-checkout-authority-ownership"),
        ("tracked-file", (1000, 0o666), "target-checkout-authority-mode"),
        ("git-config", (1001, 0o644), "target-checkout-authority-ownership"),
        ("git-object", (1000, 0o666), "target-checkout-authority-mode"),
    ],
)
def test_managed_target_owner_or_mode_rejects_before_docker(
    monkeypatch,
    tmp_path,
    unsafe_subject,
    unsafe_metadata,
    expected_gate,
):
    managed_root, release_root, checkout, env_file, commit = (
        _prepare_managed_target_checkout(monkeypatch, tmp_path)
    )
    git_config = checkout / ".git" / "config"
    git_object = checkout / ".git" / "objects" / "aa" / ("b" * 38)
    git_object.parent.mkdir(parents=True, exist_ok=True)
    git_object.write_bytes(b"opaque-object")
    expected_unsafe_paths = {
        "checkout": checkout,
        "tracked-file": checkout / "tracked.txt",
        "git-config": git_config,
        "git-object": git_object,
    }
    unsafe_path = expected_unsafe_paths[unsafe_subject]
    unsafe_metadata_reads: list[Path] = []

    def owner_mode(path):
        candidate = Path(path)
        if candidate == unsafe_path:
            unsafe_metadata_reads.append(candidate)
            return unsafe_metadata
        if candidate == env_file:
            return (1000, 0o600)
        return (1000, 0o755 if candidate.is_dir() else 0o644)

    monkeypatch.setattr(release_authority, "_posix_owner_mode", owner_mode)
    docker_bases: list[str] = []

    def forbidden_docker_base(value):
        docker_bases.append(value)
        raise AssertionError("unsafe managed target must fail before Docker")

    monkeypatch.setattr("tools.release_authority._docker_base", forbidden_docker_base)

    with pytest.raises(ReleaseAuthorityError, match=rf"^{expected_gate} gate failed:"):
        deploy_clean_commit(
            checkout,
            commit,
            docker_cmd="docker",
            env_file=env_file,
            replace_known_manual_frontend=False,
            managed_release_root=release_root,
        )

    assert managed_root.is_dir()
    assert unsafe_metadata_reads == [expected_unsafe_paths[unsafe_subject]]
    assert docker_bases == []


def test_managed_env_is_revalidated_before_any_container_or_compose_mutation(
    monkeypatch,
    tmp_path,
):
    commit = "d" * 40
    managed_root = tmp_path / "managed"
    release_root = managed_root / "releases"
    checkout = release_root / commit
    env_file = managed_root / "deploy" / "ai-platform" / ".env"
    _write_compose_files(checkout)
    env_file.parent.mkdir(parents=True)
    env_file.write_text("PRIVATE_VALUE=must-not-be-read\n", encoding="utf-8")
    env_metadata_reads = 0
    commands: list[list[str]] = []

    def owner_mode(path):
        nonlocal env_metadata_reads
        if Path(path) != env_file:
            return (1000, 0o700)
        env_metadata_reads += 1
        return (1000, 0o600 if env_metadata_reads == 1 else 0o640)

    monkeypatch.setattr(release_authority, "_posix_owner_mode", owner_mode)
    monkeypatch.setattr(
        "tools.release_authority.assert_clean_commit",
        lambda repo, requested: commit,
    )
    monkeypatch.setattr(
        "tools.release_authority._git",
        lambda repo, *args: AUTHORITATIVE_REPOSITORY + "\n",
    )
    monkeypatch.setattr(
        "tools.release_authority._image_record",
        lambda docker, image: {
            "id": "sha256:frontend" if "frontend" in image else SANDBOX_IMAGE_ID,
            "labels": _published_image_labels(
                commit,
                "frontend" if "frontend" in image else "backend",
            ),
        },
    )

    def fake_run(command, **kwargs):
        command = list(command)
        commands.append(command)
        if len(command) >= 3 and command[-3:-1] == ["container", "inspect"]:
            return subprocess.CompletedProcess(command, 1, stdout="", stderr="not found")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr("tools.release_authority._run", fake_run)
    monkeypatch.setattr(
        "tools.release_authority.assert_managed_target_checkout",
        lambda repo, requested, root: commit,
        raising=False,
    )

    with pytest.raises(
        ReleaseAuthorityError,
        match="^managed-env-file-mode gate failed:",
    ):
        deploy_clean_commit(
            checkout,
            commit,
            docker_cmd="docker",
            env_file=env_file,
            replace_known_manual_frontend=False,
            managed_release_root=release_root,
        )

    assert env_metadata_reads == 2
    assert not any("rm" in command or "compose" in command for command in commands)


def test_ignored_worktree_file_is_not_clean_and_blocks_deploy_before_docker(
    monkeypatch,
    tmp_path,
):
    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / ".gitignore").write_text("ignored-build-input.bin\n", encoding="utf-8")
    _git(repo, "add", ".gitignore")
    _git(repo, "commit", "-m", "ignore build input")
    _git(repo, "remote", "add", "origin", AUTHORITATIVE_REPOSITORY)
    commit = _git(repo, "rev-parse", "HEAD")
    (repo / "ignored-build-input.bin").write_bytes(b"must not enter Docker context\n")
    docker_lookups: list[str] = []

    def forbidden_image_lookup(docker, image):
        docker_lookups.append(image)
        raise AssertionError("Docker image lookup must not run for ignored source")

    monkeypatch.setattr("tools.release_authority._image_record", forbidden_image_lookup)

    try:
        deploy_clean_commit(
            repo,
            commit,
            docker_cmd="docker",
            env_file=tmp_path / ".env",
            replace_known_manual_frontend=False,
        )
    except ReleaseAuthorityError as exc:
        assert str(exc).startswith("target-checkout-ignored-content gate failed:")
    else:
        raise AssertionError("an ignored worktree file must not be reported clean")

    assert docker_lookups == []

    (repo / "tracked.txt").write_text("dirty\n", encoding="utf-8")
    try:
        assert_clean_commit(repo, commit)
    except ReleaseAuthorityError as exc:
        assert str(exc).startswith("target-checkout-cleanliness gate failed:")
    else:
        raise AssertionError("dirty source must be rejected")


def test_clean_commit_uses_git_porcelain_flag_supported_by_211(monkeypatch, tmp_path):
    commands: list[tuple[str, ...]] = []

    def fake_git(repo_root: Path, *args: str, text: bool = True):
        commands.append(args)
        if args[:2] == ("rev-parse", "HEAD"):
            return "d" * 40 + "\n"
        if args[:2] == ("status", "--porcelain"):
            return ""
        if args == ("ls-files", "--others", "--ignored", "--exclude-standard", "-z"):
            return b""
        raise AssertionError(args)

    monkeypatch.setattr("tools.release_authority._git", fake_git)

    assert assert_clean_commit(tmp_path, "d" * 40) == "d" * 40
    assert ("status", "--porcelain", "--untracked-files=all") in commands
    assert ("ls-files", "--others", "--ignored", "--exclude-standard", "-z") in commands
    assert all("--porcelain=v1" not in args for args in commands)


def test_preserve_dirty_source_writes_hashed_manifest_without_cleaning_repo(tmp_path):
    repo = tmp_path / "repo"
    commit = _init_repo(repo)
    (repo / "tracked.txt").write_text("dirty\n", encoding="utf-8")
    (repo / "notes.txt").write_text("preserve me\n", encoding="utf-8")
    (repo / ".env").write_text("SECRET=do-not-read\n", encoding="utf-8")

    output = preserve_dirty_source(repo, tmp_path / "preserved")
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    inventory = json.loads((output / "inventory.json").read_text(encoding="utf-8"))

    assert manifest["schema_version"] == "ai-platform.release-authority-preservation.v1"
    assert manifest["source_head"] == commit
    assert manifest["source_was_dirty"] is True
    assert manifest["artifacts"]["tracked.patch"]["sha256"]
    assert manifest["artifacts"]["untracked.tar"]["sha256"]
    env_record = next(item for item in inventory if item["path"] == ".env")
    assert env_record["content_preserved"] is False
    assert env_record["sha256"] is None
    assert (repo / "tracked.txt").read_text(encoding="utf-8") == "dirty\n"
    assert (repo / "notes.txt").is_file()
    assert (repo / ".env").is_file()


def test_parity_report_rejects_manual_frontend_and_commit_mismatch():
    commit = "a" * 40
    repository = AUTHORITATIVE_REPOSITORY
    source = {"commit": commit, "dirty": False}
    images = {
        "backend": {"id": SANDBOX_IMAGE_ID, "labels": {"ai-platform.source-commit": commit, "org.opencontainers.image.revision": commit, "ai-platform.source-repository": repository, "ai-platform.build-dirty": "false", "ai-platform.release-role": "backend"}},
        "frontend": {"id": "sha256:frontend", "labels": {"ai-platform.source-commit": commit, "org.opencontainers.image.revision": commit, "ai-platform.source-repository": repository, "ai-platform.build-dirty": "false", "ai-platform.release-role": "frontend", "com.docker.compose.service": "frontend"}},
    }
    compose_dir = "/srv/ai-platform-release/deploy/ai-platform"
    common = {"ai-platform.source-commit": commit, "ai-platform.source-dirty": "false", "ai-platform.release-owner": "repo-local-compose", "com.docker.compose.project.working_dir": compose_dir, "com.docker.compose.project.config_files": f"{compose_dir}/docker-compose.yml", "com.docker.compose.project": "ai-platform-phaseb", "com.docker.compose.oneoff": "False", "com.docker.compose.config-hash": "config-hash"}
    containers = {
        "api": {"image_id": SANDBOX_IMAGE_ID, "running": True, "labels": {**common, "ai-platform.release-role": "api", "com.docker.compose.service": "api"}},
        "worker": {"image_id": SANDBOX_IMAGE_ID, "running": True, "labels": {**common, "ai-platform.release-role": "worker", "com.docker.compose.service": "worker"}},
        "frontend": {"image_id": "sha256:frontend", "running": True, "labels": {**common, "ai-platform.release-owner": "manual", "ai-platform.release-role": "frontend", "com.docker.compose.service": "frontend"}},
    }
    runtime = {
        "api_commit": commit,
        "api_health_status": "ok",
        "worker_commit": commit,
        "worker_running": True,
        "frontend_commit": "b" * 40,
    }

    report = build_parity_report(
        expected_commit=commit,
        source=source,
        images=images,
        containers=containers,
        runtime=runtime,
        expected_compose_dir=compose_dir,
        expected_repository=repository,
    )

    assert report["verified"] is False
    assert "frontend_container_not_repo_local_compose_owned" in report["mismatches"]
    assert "frontend_runtime_commit_mismatch" in report["mismatches"]


def test_parity_report_verifies_one_clean_repo_local_compose_commit():
    commit = "c" * 40
    repository = AUTHORITATIVE_REPOSITORY
    compose_dir = "/srv/ai-platform-release/deploy/ai-platform"
    images = {
        "backend": {"id": SANDBOX_IMAGE_ID, "labels": {"ai-platform.source-commit": commit, "org.opencontainers.image.revision": commit, "ai-platform.source-repository": repository, "ai-platform.build-dirty": "false", "ai-platform.release-role": "backend"}},
        "frontend": {"id": "sha256:frontend", "labels": {"ai-platform.source-commit": commit, "org.opencontainers.image.revision": commit, "ai-platform.source-repository": repository, "ai-platform.build-dirty": "false", "ai-platform.release-role": "frontend", "com.docker.compose.service": "frontend"}},
    }
    common = {"ai-platform.source-commit": commit, "ai-platform.source-dirty": "false", "ai-platform.release-owner": "repo-local-compose", "com.docker.compose.project.working_dir": compose_dir, "com.docker.compose.project.config_files": f"{compose_dir}/docker-compose.yml", "com.docker.compose.project": "ai-platform-phaseb", "com.docker.compose.oneoff": "False", "com.docker.compose.config-hash": "config-hash"}
    containers = {
        "api": {"image_id": SANDBOX_IMAGE_ID, "running": True, "labels": {**common, "ai-platform.release-role": "api", "com.docker.compose.service": "api"}},
        "worker": {"image_id": SANDBOX_IMAGE_ID, "running": True, "labels": {**common, "ai-platform.release-role": "worker", "com.docker.compose.service": "worker"}},
        "frontend": {"image_id": "sha256:frontend", "running": True, "labels": {**common, "ai-platform.release-role": "frontend", "com.docker.compose.service": "frontend"}},
    }

    report = build_parity_report(
        expected_commit=commit,
        source={"commit": commit, "dirty": False},
        images=images,
        containers=containers,
        runtime={
            "api_commit": commit,
            "api_health_status": "ok",
            "worker_commit": commit,
            "worker_running": True,
            "frontend_commit": commit,
            "api_sandbox_executor_image_matches_expected": True,
            "worker_sandbox_executor_image_matches_expected": True,
            "api_worker_sandbox_executor_images_match": True,
        },
        expected_compose_dir=compose_dir,
        expected_repository=repository,
    )

    assert report["verified"] is True
    assert report["mismatches"] == []


def test_parity_report_rejects_api_worker_executor_image_drift():
    commit = "a" * 40
    report = build_parity_report(
        expected_commit=commit,
        source={},
        images={},
        containers={},
        runtime={
            "api_sandbox_executor_image_matches_expected": True,
            "worker_sandbox_executor_image_matches_expected": False,
            "api_worker_sandbox_executor_images_match": False,
        },
        expected_compose_dir="/srv/ai-platform/deploy/ai-platform",
        expected_repository=AUTHORITATIVE_REPOSITORY,
    )

    assert "worker_sandbox_executor_image_mismatch" in report["mismatches"]
    assert "api_worker_sandbox_executor_image_mismatch" in report["mismatches"]
def test_parity_report_verifies_exact_ordered_two_file_compose_set_for_all_services():
    commit = "4" * 40
    repository = AUTHORITATIVE_REPOSITORY
    compose_dir = "/srv/ai-platform-release/deploy/ai-platform"
    compose_files = [
        f"{compose_dir}/docker-compose.yml",
        f"{compose_dir}/docker-compose.sandbox.yml",
    ]
    image_labels = {
        "ai-platform.source-commit": commit,
        "org.opencontainers.image.revision": commit,
        "ai-platform.source-repository": repository,
        "ai-platform.build-dirty": "false",
    }
    images = {
        "backend": {
            "id": SANDBOX_IMAGE_ID,
            "labels": {**image_labels, "ai-platform.release-role": "backend"},
        },
        "frontend": {
            "id": "sha256:frontend",
            "labels": {**image_labels, "ai-platform.release-role": "frontend"},
        },
    }
    common = {
        "ai-platform.source-commit": commit,
        "ai-platform.source-dirty": "false",
        "ai-platform.release-owner": "repo-local-compose",
        "com.docker.compose.project.working_dir": compose_dir,
        "com.docker.compose.project.config_files": ",".join(compose_files),
        "com.docker.compose.project": "ai-platform-phaseb",
        "com.docker.compose.oneoff": "False",
        "com.docker.compose.config-hash": "config-hash",
    }
    containers = {
        role: {
            "image_id": "sha256:frontend" if role == "frontend" else SANDBOX_IMAGE_ID,
            "running": True,
            "labels": {
                **common,
                "ai-platform.release-role": role,
                "com.docker.compose.service": role,
            },
        }
        for role in ("api", "worker", "frontend")
    }
    runtime = {
        "api_commit": commit,
        "api_health_status": "ok",
        "worker_commit": commit,
        "worker_running": True,
        "frontend_commit": commit,
        "api_sandbox_executor_image_matches_expected": True,
        "worker_sandbox_executor_image_matches_expected": True,
        "api_worker_sandbox_executor_images_match": True,
    }

    report = build_parity_report(
        expected_commit=commit,
        source={"commit": commit, "dirty": False},
        images=images,
        containers=containers,
        runtime=runtime,
        expected_compose_dir=compose_dir,
        expected_compose_files=compose_files,
        expected_repository=repository,
    )

    assert report["verified"] is True
    for role in ("api", "worker", "frontend"):
        mismatched = {name: {**record, "labels": dict(record["labels"])} for name, record in containers.items()}
        mismatched[role]["labels"]["com.docker.compose.project.config_files"] = ",".join(
            reversed(compose_files)
        )
        rejected = build_parity_report(
            expected_commit=commit,
            source={"commit": commit, "dirty": False},
            images=images,
            containers=mismatched,
            runtime=runtime,
            expected_compose_dir=compose_dir,
            expected_compose_files=compose_files,
            expected_repository=repository,
        )
        assert f"{role}_compose_config_mismatch" in rejected["mismatches"]


@pytest.mark.parametrize(
    "selected",
    [
        [],
        [SANDBOX_COMPOSE_RELATIVE_PATH, COMPOSE_RELATIVE_PATH],
        [COMPOSE_RELATIVE_PATH, COMPOSE_RELATIVE_PATH],
        [COMPOSE_RELATIVE_PATH, "../docker-compose.sandbox.yml"],
        [COMPOSE_RELATIVE_PATH, "deploy//ai-platform/docker-compose.sandbox.yml"],
        [COMPOSE_RELATIVE_PATH, "deploy\\ai-platform\\docker-compose.sandbox.yml"],
        [COMPOSE_RELATIVE_PATH, "deploy/./ai-platform/docker-compose.sandbox.yml"],
        [COMPOSE_RELATIVE_PATH, "deploy/ai-platform/docker-compose.sandbox.yml/"],
        [COMPOSE_RELATIVE_PATH, "deploy/ai-platform/docker,compose.sandbox.yml"],
        [COMPOSE_RELATIVE_PATH, "deploy/ai-platform/docker-compose.sandbox.yml\n"],
        [COMPOSE_RELATIVE_PATH, "/private-marker/docker-compose.sandbox.yml"],
        [COMPOSE_RELATIVE_PATH, "C:/private-marker/docker-compose.sandbox.yml"],
        [COMPOSE_RELATIVE_PATH, 42],
        [COMPOSE_RELATIVE_PATH, "deploy/ai-platform/missing.yml"],
        [COMPOSE_RELATIVE_PATH, "deploy/ai-platform"],
    ],
)
def test_compose_file_selection_rejects_unsafe_or_noncanonical_paths(
    monkeypatch,
    tmp_path,
    selected,
):
    _write_compose_files(tmp_path)

    with pytest.raises(ReleaseAuthorityError):
        release_authority.resolve_compose_files(tmp_path, selected)

    docker_bases: list[str] = []
    monkeypatch.setattr(
        "tools.release_authority.assert_clean_commit",
        lambda repo, requested: "a" * 40,
    )

    def forbidden_docker_base(value):
        docker_bases.append(value)
        raise AssertionError("unsafe Compose selection must fail before Docker")

    monkeypatch.setattr("tools.release_authority._docker_base", forbidden_docker_base)
    with pytest.raises(ReleaseAuthorityError):
        deploy_clean_commit(
            tmp_path,
            "a" * 40,
            docker_cmd="docker",
            env_file=tmp_path / ".env",
            replace_known_manual_frontend=False,
            compose_files=selected,
        )
    assert docker_bases == []


def test_compose_file_selection_rejects_absolute_and_linked_paths(monkeypatch, tmp_path):
    main, overlay = _write_compose_files(tmp_path)
    outside = tmp_path.parent / "private-marker-compose.yml"
    outside.write_text("services: {}\n", encoding="utf-8")

    with pytest.raises(ReleaseAuthorityError) as absolute_error:
        release_authority.resolve_compose_files(
            tmp_path,
            [COMPOSE_RELATIVE_PATH, str(outside.resolve())],
        )
    assert "private-marker" not in str(absolute_error.value)

    original = release_authority._is_link_or_junction
    monkeypatch.setattr(
        "tools.release_authority._is_link_or_junction",
        lambda path: Path(path) == overlay or original(Path(path)),
    )
    with pytest.raises(ReleaseAuthorityError):
        release_authority.resolve_compose_files(
            tmp_path,
            [COMPOSE_RELATIVE_PATH, SANDBOX_COMPOSE_RELATIVE_PATH],
        )

    assert main.is_file()


def test_compose_file_selection_rejects_duplicate_file_identity(tmp_path):
    main, overlay = _write_compose_files(tmp_path)
    alias = overlay.with_name("docker-compose.alias.yml")
    os.link(overlay, alias)

    with pytest.raises(ReleaseAuthorityError):
        release_authority.resolve_compose_files(
            tmp_path,
            [
                COMPOSE_RELATIVE_PATH,
                SANDBOX_COMPOSE_RELATIVE_PATH,
                "deploy/ai-platform/docker-compose.alias.yml",
            ],
        )

    assert main.is_file()


def test_parity_report_rejects_stopped_release_container():
    commit = "6" * 40
    repository = AUTHORITATIVE_REPOSITORY
    compose_dir = "/srv/ai-platform/deploy/ai-platform"
    image_labels = {
        "ai-platform.source-commit": commit,
        "org.opencontainers.image.revision": commit,
        "ai-platform.source-repository": repository,
        "ai-platform.build-dirty": "false",
    }
    common = {
        "ai-platform.source-commit": commit,
        "ai-platform.source-dirty": "false",
        "ai-platform.release-owner": "repo-local-compose",
        "com.docker.compose.project.working_dir": compose_dir,
        "com.docker.compose.project.config_files": f"{compose_dir}/docker-compose.yml",
        "com.docker.compose.project": "ai-platform-phaseb",
        "com.docker.compose.oneoff": "False",
        "com.docker.compose.config-hash": "config-hash",
    }
    report = build_parity_report(
        expected_commit=commit,
        source={"commit": commit, "dirty": False},
        images={
            "backend": {"id": SANDBOX_IMAGE_ID, "labels": {**image_labels, "ai-platform.release-role": "backend"}},
            "frontend": {"id": "sha256:frontend", "labels": {**image_labels, "ai-platform.release-role": "frontend", "com.docker.compose.service": "frontend"}},
        },
        containers={
            "api": {"image_id": SANDBOX_IMAGE_ID, "running": False, "labels": {**common, "ai-platform.release-role": "api", "com.docker.compose.service": "api"}},
            "worker": {"image_id": SANDBOX_IMAGE_ID, "running": True, "labels": {**common, "ai-platform.release-role": "worker", "com.docker.compose.service": "worker"}},
            "frontend": {"image_id": "sha256:frontend", "running": False, "labels": {**common, "ai-platform.release-role": "frontend", "com.docker.compose.service": "frontend"}},
        },
        runtime={
            "api_commit": commit,
            "api_health_status": "ok",
            "worker_commit": commit,
            "worker_running": True,
            "frontend_commit": commit,
        },
        expected_compose_dir=compose_dir,
        expected_repository=repository,
    )

    assert report["verified"] is False
    assert "api_container_not_running" in report["mismatches"]
    assert "frontend_container_not_running" in report["mismatches"]


def test_parity_report_rejects_incomplete_compose_identity():
    commit = "a" * 40
    compose_dir = "/srv/ai-platform/deploy/ai-platform"
    image_labels = {
        "ai-platform.source-commit": commit,
        "org.opencontainers.image.revision": commit,
        "ai-platform.source-repository": AUTHORITATIVE_REPOSITORY,
        "ai-platform.build-dirty": "false",
    }
    common = {
        "ai-platform.source-commit": commit,
        "ai-platform.source-dirty": "false",
        "ai-platform.release-owner": "repo-local-compose",
        "com.docker.compose.project.working_dir": compose_dir,
        "com.docker.compose.project.config_files": f"{compose_dir}/docker-compose.yml",
    }
    report = build_parity_report(
        expected_commit=commit,
        source={"commit": commit, "dirty": False},
        images={
            "backend": {"id": SANDBOX_IMAGE_ID, "labels": {**image_labels, "ai-platform.release-role": "backend"}},
            "frontend": {"id": "sha256:frontend", "labels": {**image_labels, "ai-platform.release-role": "frontend", "com.docker.compose.service": "frontend"}},
        },
        containers={
            role: {
                "image_id": "sha256:frontend" if role == "frontend" else SANDBOX_IMAGE_ID,
                "running": True,
                "labels": {**common, "ai-platform.release-role": role, "com.docker.compose.service": role},
            }
            for role in ("api", "worker", "frontend")
        },
        runtime={
            "api_commit": commit,
            "api_health_status": "ok",
            "worker_commit": commit,
            "worker_running": True,
            "frontend_commit": commit,
        },
        expected_compose_dir=compose_dir,
        expected_repository=AUTHORITATIVE_REPOSITORY,
    )

    assert report["verified"] is False
    assert "api_compose_project_mismatch" in report["mismatches"]
    assert "worker_compose_oneoff_mismatch" in report["mismatches"]
    assert "frontend_compose_config_hash_missing" in report["mismatches"]


def test_worker_heartbeat_path_uses_validated_container_tmpdir():
    path = release_authority._worker_heartbeat_path(
        {
            "Config": {
                "Env": ["UNRELATED_ENV=private-marker", "TMPDIR=/home/ai-platform/tmp"]
            }
        }
    )

    assert path == f"/home/ai-platform/tmp/{WORKER_HEARTBEAT_FILENAME}"


def test_worker_heartbeat_path_defaults_to_tmp_when_entry_is_absent():
    assert release_authority._worker_heartbeat_path(
        {"Config": {"Env": ["PATH=/usr/bin"]}}
    ) == (
        f"/tmp/{WORKER_HEARTBEAT_FILENAME}"
    )


@pytest.mark.parametrize(
    "container_id",
    [
        None,
        "",
        " ",
        "private-marker",
        "a" * 63,
        "g" * 64,
        "A" * 64,
        f"sha256:{'a' * 64}",
    ],
)
def test_worker_container_id_rejects_invalid_metadata_without_leakage(container_id):
    with pytest.raises(
        ReleaseAuthorityError,
        match="^worker container ID metadata is invalid$",
    ) as exc_info:
        release_authority._worker_container_id({"Id": container_id})
    assert str(exc_info.value) == "worker container ID metadata is invalid"
    assert "private-marker" not in str(exc_info.value)


def test_worker_container_id_accepts_full_immutable_id():
    container_id = "a" * 64

    assert release_authority._worker_container_id({"Id": container_id}) == container_id


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"Config": {"Env": "TMPDIR=/tmp"}},
        {"Config": {"Env": [None]}},
        {"Config": {"Env": ["TMPDIR"]}},
        {"Config": {"Env": ["TMPDIR="]}},
        {"Config": {"Env": ["TMPDIR=relative"]}},
        {"Config": {"Env": ["TMPDIR=/tmp/../secret"]}},
        {"Config": {"Env": [r"TMPDIR=/tmp\secret"]}},
        {"Config": {"Env": ["TMPDIR=/tmp/\nsecret"]}},
        {"Config": {"Env": ["TMPDIR=/tmp/*"]}},
        {"Config": {"Env": ["TMPDIR=/tmp/?.json"]}},
        {"Config": {"Env": ["TMPDIR=/tmp/[ab]"]}},
        {"Config": {"Env": ["TMPDIR=/tmp/{a,b}"]}},
        {"Config": {"Env": ["TMPDIR=/tmp/$HOME"]}},
        {"Config": {"Env": ["TMPDIR=/tmp/$(id)"]}},
        {"Config": {"Env": ["TMPDIR=/tmp/`id`"]}},
        {"Config": {"Env": ["TMPDIR=/tmp/\u0085private-marker"]}},
        {"Config": {"Env": ["TMPDIR=/tmp/\u202eprivate-marker"]}},
        {"Config": {"Env": ["TMPDIR=/tmp/\ud800"]}},
        {"Config": {"Env": ["TMPDIR=/tmp//nested"]}},
        {"Config": {"Env": ["TMPDIR=/tmp/./nested"]}},
        {
            "Config": {
                "Env": ["TMPDIR=/one", "UNRELATED_ENV=private-marker", "TMPDIR=/two"]
            }
        },
    ],
)
def test_worker_heartbeat_path_rejects_invalid_metadata_without_leaking_env(
    payload,
):
    with pytest.raises(
        ReleaseAuthorityError,
        match="^worker container TMPDIR metadata is invalid$",
    ) as exc_info:
        release_authority._worker_heartbeat_path(payload)
    assert "private-marker" not in str(exc_info.value)
    assert "UNRELATED_ENV" not in str(exc_info.value)


def test_worker_heartbeat_read_failure_is_static_without_path_leakage(monkeypatch):
    container_id = "a" * 64
    sensitive_path = release_authority._worker_heartbeat_path(
        {"Config": {"Env": ["TMPDIR=/private-marker/tmp"]}}
    )
    failure = subprocess.CalledProcessError(
        1,
        ["docker", "exec", container_id, "cat", sensitive_path],
        stderr=f"cannot read {sensitive_path}",
    )
    monkeypatch.setattr(
        "tools.release_authority._container_json_file",
        lambda docker, name, path: (_ for _ in ()).throw(failure),
    )

    with pytest.raises(
        ReleaseAuthorityError,
        match="^worker runtime heartbeat read failed$",
    ) as exc_info:
        release_authority._read_worker_heartbeat(
            ["docker"],
            container_id,
            sensitive_path,
        )

    assert "private-marker" not in str(exc_info.value)
    assert container_id not in str(exc_info.value)
    assert exc_info.value.__suppress_context__ is True


def test_verify_cli_report_redacts_worker_heartbeat_read_failure(
    monkeypatch,
    capsys,
    tmp_path,
):
    container_id = "a" * 64
    sensitive_path = release_authority._worker_heartbeat_path(
        {"Config": {"Env": ["TMPDIR=/private-marker/tmp"]}}
    )
    failure = subprocess.CalledProcessError(
        1,
        ["docker", "exec", container_id, "cat", sensitive_path],
        stderr=f"cannot read {sensitive_path}",
    )
    monkeypatch.setattr(
        "tools.release_authority._container_json_file",
        lambda docker, name, path: (_ for _ in ()).throw(failure),
    )
    monkeypatch.setattr(
        "tools.release_authority.collect_live_parity",
        lambda *args, **kwargs: release_authority._read_worker_heartbeat(
            ["docker"],
            container_id,
            sensitive_path,
        ),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "release_authority.py",
            "verify",
            "--repo-root",
            str(tmp_path),
            "--commit",
            "d" * 40,
        ],
    )

    assert release_authority.main() == 2
    report = json.loads(capsys.readouterr().out)
    assert report == {
        "command": "verify",
        "error": "worker runtime heartbeat read failed",
        "verified": False,
    }
    assert "private-marker" not in json.dumps(report)
    assert container_id not in json.dumps(report)


def test_collect_live_parity_derives_repo_local_compose_and_live_endpoints(monkeypatch, tmp_path):
    commit = "d" * 40
    main_compose, sandbox_compose = _write_compose_files(tmp_path)
    observed_urls: list[str] = []
    observed_heartbeat_paths: list[str] = []
    inspected_containers: list[str] = []
    worker_probe_targets: list[str] = []
    inspected_worker_id = "a" * 64
    replacement_worker_id = "b" * 64
    worker_name_binding = {"ai-platform-worker": inspected_worker_id}
    repository = AUTHORITATIVE_REPOSITORY

    monkeypatch.setattr("tools.release_authority.assert_clean_commit", lambda repo, requested: commit)
    monkeypatch.setattr("tools.release_authority._git", lambda repo, *args: repository + "\n")
    monkeypatch.setattr(
        "tools.release_authority._image_record",
        lambda docker, image: {
            "reference": image,
            "id": "sha256:frontend" if "frontend" in image else SANDBOX_IMAGE_ID,
            "labels": {
                "ai-platform.source-commit": commit,
                "org.opencontainers.image.revision": commit,
                "ai-platform.source-repository": repository,
                "ai-platform.build-dirty": "false",
                "ai-platform.release-role": "frontend" if "frontend" in image else "backend",
            },
        },
    )
    compose_dir = str((tmp_path / "deploy" / "ai-platform").resolve()).replace("\\", "/")
    common = {
        "ai-platform.source-commit": commit,
        "ai-platform.source-dirty": "false",
        "ai-platform.release-owner": "repo-local-compose",
        "com.docker.compose.project.working_dir": compose_dir,
        "com.docker.compose.project.config_files": _compose_config_value(
            main_compose,
            sandbox_compose,
        ),
        "com.docker.compose.project": "ai-platform-phaseb",
        "com.docker.compose.oneoff": "False",
        "com.docker.compose.config-hash": "config-hash",
    }
    def fake_docker_json(docker, *args):
        assert args[:2] == ("container", "inspect")
        name = args[2]
        inspected_containers.append(name)
        role = name.removeprefix("ai-platform-")
        payload = {
            "name": name,
            "Id": inspected_worker_id if name.endswith("worker") else "c" * 64,
            "Image": (
                "sha256:frontend" if name.endswith("frontend") else SANDBOX_IMAGE_ID
            ),
            "Config": {
                "Labels": {
                    **common,
                    "ai-platform.release-role": role,
                    "com.docker.compose.service": role,
                },
                "Env": (
                    [
                        "UNRELATED_ENV=private-marker",
                        "TMPDIR=/home/ai-platform/tmp",
                            f"SANDBOX_EXECUTOR_IMAGE={SANDBOX_IMAGE_ID}",
                    ]
                    if name.endswith("worker")
                    else [
                        "PATH=/usr/bin",
                            f"SANDBOX_EXECUTOR_IMAGE={SANDBOX_IMAGE_ID}",
                    ]
                ),
            },
            "State": {
                "Running": True,
                "Pid": 1234 if name.endswith("worker") else 4321,
                "Health": (
                    {"Status": "healthy"} if not name.endswith("worker") else {}
                ),
            },
            "NetworkSettings": {
                "Ports": {
                    "8080/tcp" if name.endswith("frontend") else "8020/tcp": [
                        {
                            "HostIp": "0.0.0.0",
                            "HostPort": (
                                "18001" if name.endswith("frontend") else "8020"
                            ),
                        },
                        {
                            "HostIp": "::",
                            "HostPort": (
                                "18001" if name.endswith("frontend") else "8020"
                            ),
                        },
                    ]
                }
            },
        }
        if name.endswith("worker"):
            worker_name_binding[name] = replacement_worker_id
        return [payload]

    monkeypatch.setattr("tools.release_authority._docker_json", fake_docker_json)

    def fake_container_json_file(docker, target, path):
        worker_probe_targets.append(target)
        assert worker_name_binding.get(target, target) in {
            inspected_worker_id,
            replacement_worker_id,
        }
        observed_heartbeat_paths.append(path)
        return {
            "schema_version": "ai-platform.worker-runtime-heartbeat.v1",
            "worker_id": "worker-a",
            "runtime_commit": commit,
            "pid": 1234,
            "observed_at": datetime.now(timezone.utc).isoformat(),
        }

    monkeypatch.setattr("tools.release_authority._container_json_file", fake_container_json_file)

    def fake_process_alive(docker, target, pid):
        worker_probe_targets.append(target)
        return (
            worker_name_binding.get(target, target)
            in {inspected_worker_id, replacement_worker_id}
            and pid == 1234
        )

    monkeypatch.setattr("tools.release_authority._container_process_alive", fake_process_alive)

    def fake_http_json(url: str):
        observed_urls.append(url)
        if url.endswith("/api/ai/health"):
            return {"status": "ok", "runtime_commit": commit}
        return {
            "schema_version": "ai-platform.frontend-build-provenance.v1",
            "frontend_path": "frontend/web",
            "git": {"commit": commit, "dirty": False},
        }

    monkeypatch.setattr("tools.release_authority._http_json", fake_http_json)

    report = collect_live_parity(
        tmp_path,
        commit,
        docker_cmd="docker",
        compose_files=[COMPOSE_RELATIVE_PATH, SANDBOX_COMPOSE_RELATIVE_PATH],
    )

    assert report["verified"] is True
    assert observed_urls == [
        "http://127.0.0.1:8020/api/ai/health",
        "http://127.0.0.1:18001/ai-platform-build-provenance.json",
    ]
    assert report["runtime"]["frontend_commit"] == commit
    assert report["runtime"]["api_health_status"] == "ok"
    assert report["runtime"]["worker_running"] is True
    assert report["containers"]["worker"]["name"] == "ai-platform-worker"
    assert observed_heartbeat_paths == [
        "/home/ai-platform/tmp/ai-platform-worker-runtime-heartbeat.json"
    ]
    assert inspected_containers == [
        "ai-platform-api",
        "ai-platform-worker",
        "ai-platform-frontend",
    ]
    assert worker_name_binding["ai-platform-worker"] == replacement_worker_id
    assert worker_probe_targets == [inspected_worker_id, inspected_worker_id]
    serialized_report = json.dumps(report)
    assert "private-marker" not in serialized_report
    assert inspected_worker_id not in serialized_report
    assert replacement_worker_id not in serialized_report


def test_collect_live_parity_rejects_stale_worker_heartbeat(monkeypatch, tmp_path):
    commit = "9" * 40
    _write_compose_files(tmp_path)
    container_id = "c" * 64
    repository = AUTHORITATIVE_REPOSITORY
    compose_dir = str((tmp_path / "deploy" / "ai-platform").resolve()).replace("\\", "/")
    common = {
        "ai-platform.source-commit": commit,
        "ai-platform.source-dirty": "false",
        "ai-platform.release-owner": "repo-local-compose",
        "com.docker.compose.project.working_dir": compose_dir,
        "com.docker.compose.project.config_files": f"{compose_dir}/docker-compose.yml",
        "com.docker.compose.project": "ai-platform-phaseb",
        "com.docker.compose.oneoff": "False",
        "com.docker.compose.config-hash": "config-hash",
    }
    monkeypatch.setattr("tools.release_authority.assert_clean_commit", lambda repo, requested: commit)
    monkeypatch.setattr("tools.release_authority._git", lambda repo, *args: repository + "\n")
    monkeypatch.setattr(
        "tools.release_authority._image_record",
        lambda docker, image: {
            "id": "sha256:frontend" if "frontend" in image else SANDBOX_IMAGE_ID,
            "labels": {
                "ai-platform.source-commit": commit,
                "org.opencontainers.image.revision": commit,
                "ai-platform.source-repository": repository,
                "ai-platform.build-dirty": "false",
                "ai-platform.release-role": "frontend" if "frontend" in image else "backend",
            },
        },
    )
    def fake_container_record(docker, name):
        return {
            "name": name,
            "image_id": "sha256:frontend" if name.endswith("frontend") else SANDBOX_IMAGE_ID,
            "labels": {**common, "ai-platform.release-role": name.removeprefix("ai-platform-"), "com.docker.compose.service": name.removeprefix("ai-platform-")},
            "running": True,
            "pid": 2222 if name.endswith("worker") else 1111,
            "health": "healthy",
            "ports": {
                "8080/tcp" if name.endswith("frontend") else "8020/tcp": [
                    {"HostIp": "0.0.0.0", "HostPort": "18001" if name.endswith("frontend") else "8020"}
                ]
            },
        }

    monkeypatch.setattr(
        "tools.release_authority._container_record",
        fake_container_record,
    )
    monkeypatch.setattr(
        "tools.release_authority._container_inspect_record",
        lambda docker, name: (
            fake_container_record(docker, name),
            {"Id": container_id, "Config": {"Env": ["PATH=/usr/bin"]}},
        ),
    )
    monkeypatch.setattr(
        "tools.release_authority._http_json",
        lambda url: (
            {"status": "ok", "runtime_commit": commit}
            if url.endswith("/api/ai/health")
            else {
                "schema_version": "ai-platform.frontend-build-provenance.v1",
                "frontend_path": "frontend/web",
                "git": {"commit": commit, "dirty": False},
            }
        ),
    )
    monkeypatch.setattr(
        "tools.release_authority._container_json_file",
        lambda docker, name, path: {
            "schema_version": "ai-platform.worker-runtime-heartbeat.v1",
            "worker_id": "worker-a",
            "runtime_commit": commit,
            "pid": 1111,
            "observed_at": (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat(),
        },
    )
    monkeypatch.setattr("tools.release_authority._container_process_alive", lambda docker, name, pid: False)

    try:
        collect_live_parity(tmp_path, commit, docker_cmd="docker")
    except ReleaseAuthorityError as exc:
        assert "worker runtime heartbeat" in str(exc)
        assert container_id not in str(exc)
    else:
        raise AssertionError("stale or wrong-process worker heartbeat must be rejected")


def test_published_url_supports_ipv6_only_binding():
    from tools.release_authority import _published_loopback_url

    url = _published_loopback_url(
        {"ports": {"8080/tcp": [{"HostIp": "::", "HostPort": "18001"}]}},
        "8080/tcp",
        "/healthz",
    )

    assert url == "http://[::1]:18001/healthz"


def test_container_process_alive_uses_shell_builtin_kill(monkeypatch):
    from tools.release_authority import _container_process_alive

    observed: list[str] = []

    def fake_run(command, **kwargs):
        observed.extend(command)
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr("tools.release_authority._run", fake_run)

    container_id = "a" * 64
    assert _container_process_alive(["sudo", "-n", "docker"], container_id, 1234) is True
    assert observed == [
        "sudo",
        "-n",
        "docker",
        "exec",
        container_id,
        "/bin/sh",
        "-c",
        'kill -0 "$1"',
        "sh",
        "1234",
    ]


def test_container_process_alive_fails_closed_when_inspected_instance_disappears(
    monkeypatch,
):
    container_id = "a" * 64
    failure = subprocess.CalledProcessError(
        1,
        ["docker", "exec", container_id, "/bin/sh", "-c", "kill"],
    )
    monkeypatch.setattr(
        "tools.release_authority._run",
        lambda *args, **kwargs: (_ for _ in ()).throw(failure),
    )

    assert release_authority._container_process_alive(
        ["docker"],
        container_id,
        1234,
    ) is False


def test_deploy_rejects_unexpected_manual_frontend_identity(monkeypatch, tmp_path):
    commit = "e" * 40
    _write_compose_files(tmp_path)
    removed: list[list[str]] = []

    monkeypatch.setattr("tools.release_authority.assert_clean_commit", lambda repo, requested: commit)
    monkeypatch.setattr("tools.release_authority._git", lambda repo, *args: AUTHORITATIVE_REPOSITORY + "\n")
    monkeypatch.setattr(
        "tools.release_authority._image_record",
        lambda docker, image: {
            "id": "sha256:image",
            "labels": {
                "ai-platform.source-commit": commit,
                "org.opencontainers.image.revision": commit,
                "ai-platform.source-repository": AUTHORITATIVE_REPOSITORY,
                "ai-platform.build-dirty": "false",
                "ai-platform.release-role": "frontend" if "frontend" in image else "backend",
            },
        },
    )

    def fake_run(command, **kwargs):
        if command[-3:] == ["container", "inspect", "ai-platform-frontend"]:
            return subprocess.CompletedProcess(
                command,
                0,
                stdout=json.dumps([{"Config": {"Image": "ai-platform-frontend:unexpected", "Labels": {}}}]),
                stderr="",
            )
        if command[-2:] in (["inspect", "ai-platform-api"], ["inspect", "ai-platform-worker"]):
            return subprocess.CompletedProcess(command, 1, stdout="", stderr="not found")
        if "rm" in command:
            removed.append(list(command))
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr("tools.release_authority._run", fake_run)

    try:
        deploy_clean_commit(
            tmp_path,
            commit,
            docker_cmd="docker",
            env_file=tmp_path / ".env",
            replace_known_manual_frontend=True,
            expected_manual_frontend_image="ai-platform-frontend:d189877-20260709-main",
            expected_manual_frontend_image_id="sha256:f2476f83d139f721cf3adb3e7664dd431082aea459b6205bcc9d35f04a524e25",
        )
    except ReleaseAuthorityError as exc:
        assert "manual frontend identity mismatch" in str(exc)
    else:
        raise AssertionError("an unexpected manual frontend must not be removed")

    assert removed == []


def test_deploy_rejects_same_name_manual_frontend_replacement_before_removal(
    monkeypatch,
    tmp_path,
):
    commit = "f" * 40
    _write_compose_files(tmp_path)
    expected_image = "ai-platform-frontend:manual"
    expected_image_id = "sha256:" + "1" * 64
    original_container_id = "a" * 64
    replacement_container_id = "b" * 64
    frontend_inspects = 0
    removed: list[list[str]] = []

    monkeypatch.setattr("tools.release_authority.assert_clean_commit", lambda repo, requested: commit)
    monkeypatch.setattr(
        "tools.release_authority._git",
        lambda repo, *args: AUTHORITATIVE_REPOSITORY + "\n",
    )
    monkeypatch.setattr(
        "tools.release_authority._image_record",
        lambda docker, image: {
            "id": "sha256:frontend" if "frontend" in image else SANDBOX_IMAGE_ID,
            "labels": {
                "ai-platform.source-commit": commit,
                "org.opencontainers.image.revision": commit,
                "ai-platform.source-repository": AUTHORITATIVE_REPOSITORY,
                "ai-platform.build-dirty": "false",
                "ai-platform.release-role": "frontend" if "frontend" in image else "backend",
            },
        },
    )

    def fake_run(command, **kwargs):
        nonlocal frontend_inspects
        if command[-2:] in (["inspect", "ai-platform-api"], ["inspect", "ai-platform-worker"]):
            return subprocess.CompletedProcess(command, 1, stdout="", stderr="not found")
        if command[-3:] == ["container", "inspect", "ai-platform-frontend"]:
            frontend_inspects += 1
            container_id = (
                original_container_id if frontend_inspects == 1 else replacement_container_id
            )
            return subprocess.CompletedProcess(
                command,
                0,
                stdout=json.dumps(
                    [
                        {
                            "Id": container_id,
                            "Image": expected_image_id,
                            "Config": {"Image": expected_image, "Labels": {}},
                        }
                    ]
                ),
                stderr="",
            )
        if "rm" in command:
            removed.append(list(command))
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr("tools.release_authority._run", fake_run)

    with pytest.raises(ReleaseAuthorityError):
        deploy_clean_commit(
            tmp_path,
            commit,
            docker_cmd="docker",
            env_file=tmp_path / ".env",
            replace_known_manual_frontend=True,
            expected_manual_frontend_image=expected_image,
            expected_manual_frontend_image_id=expected_image_id,
        )

    assert frontend_inspects == 2
    assert removed == []


def test_deploy_removes_revalidated_manual_frontend_by_immutable_id(monkeypatch, tmp_path):
    commit = "f" * 40
    _write_compose_files(tmp_path)
    expected_image = "ai-platform-frontend:manual"
    expected_image_id = "sha256:" + "1" * 64
    container_id = "c" * 64
    frontend_inspects = 0
    removed: list[list[str]] = []

    monkeypatch.setattr("tools.release_authority.assert_clean_commit", lambda repo, requested: commit)
    monkeypatch.setattr(
        "tools.release_authority._git",
        lambda repo, *args: AUTHORITATIVE_REPOSITORY + "\n",
    )
    monkeypatch.setattr(
        "tools.release_authority._image_record",
        lambda docker, image: {
            "id": "sha256:frontend" if "frontend" in image else SANDBOX_IMAGE_ID,
            "labels": {
                "ai-platform.source-commit": commit,
                "org.opencontainers.image.revision": commit,
                "ai-platform.source-repository": AUTHORITATIVE_REPOSITORY,
                "ai-platform.build-dirty": "false",
                "ai-platform.release-role": "frontend" if "frontend" in image else "backend",
            },
        },
    )

    def fake_run(command, **kwargs):
        nonlocal frontend_inspects
        if command[-2:] in (["inspect", "ai-platform-api"], ["inspect", "ai-platform-worker"]):
            return subprocess.CompletedProcess(command, 1, stdout="", stderr="not found")
        if command[-3:] == ["container", "inspect", "ai-platform-frontend"]:
            frontend_inspects += 1
            return subprocess.CompletedProcess(
                command,
                0,
                stdout=json.dumps(
                    [
                        {
                            "Id": container_id,
                            "Image": expected_image_id,
                            "Config": {"Image": expected_image, "Labels": {}},
                        }
                    ]
                ),
                stderr="",
            )
        if "rm" in command:
            removed.append(list(command))
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr("tools.release_authority._run", fake_run)

    deploy_clean_commit(
        tmp_path,
        commit,
        docker_cmd="docker",
        env_file=tmp_path / ".env",
        replace_known_manual_frontend=True,
        expected_manual_frontend_image=expected_image,
        expected_manual_frontend_image_id=expected_image_id,
    )

    assert frontend_inspects == 2
    assert removed == [["docker", "container", "rm", "-f", container_id]]


@pytest.mark.parametrize("second_inspect", ["missing", "malformed_id"])
def test_deploy_rejects_missing_or_malformed_manual_frontend_before_removal(
    monkeypatch,
    tmp_path,
    second_inspect,
):
    commit = "f" * 40
    _write_compose_files(tmp_path)
    expected_image = "ai-platform-frontend:manual"
    expected_image_id = "sha256:" + "1" * 64
    container_id = "d" * 64
    frontend_inspects = 0
    removed: list[list[str]] = []

    monkeypatch.setattr("tools.release_authority.assert_clean_commit", lambda repo, requested: commit)
    monkeypatch.setattr(
        "tools.release_authority._git",
        lambda repo, *args: AUTHORITATIVE_REPOSITORY + "\n",
    )
    monkeypatch.setattr(
        "tools.release_authority._image_record",
        lambda docker, image: {
            "id": "sha256:frontend" if "frontend" in image else SANDBOX_IMAGE_ID,
            "labels": {
                "ai-platform.source-commit": commit,
                "org.opencontainers.image.revision": commit,
                "ai-platform.source-repository": AUTHORITATIVE_REPOSITORY,
                "ai-platform.build-dirty": "false",
                "ai-platform.release-role": "frontend" if "frontend" in image else "backend",
            },
        },
    )

    def fake_run(command, **kwargs):
        nonlocal frontend_inspects
        if command[-2:] in (["inspect", "ai-platform-api"], ["inspect", "ai-platform-worker"]):
            return subprocess.CompletedProcess(command, 1, stdout="", stderr="not found")
        if command[-3:] == ["container", "inspect", "ai-platform-frontend"]:
            frontend_inspects += 1
            if frontend_inspects == 2 and second_inspect == "missing":
                return subprocess.CompletedProcess(command, 1, stdout="", stderr="not found")
            observed_id = "invalid" if frontend_inspects == 2 else container_id
            return subprocess.CompletedProcess(
                command,
                0,
                stdout=json.dumps(
                    [
                        {
                            "Id": observed_id,
                            "Image": expected_image_id,
                            "Config": {"Image": expected_image, "Labels": {}},
                        }
                    ]
                ),
                stderr="",
            )
        if "rm" in command:
            removed.append(list(command))
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr("tools.release_authority._run", fake_run)

    with pytest.raises(ReleaseAuthorityError):
        deploy_clean_commit(
            tmp_path,
            commit,
            docker_cmd="docker",
            env_file=tmp_path / ".env",
            replace_known_manual_frontend=True,
            expected_manual_frontend_image=expected_image,
            expected_manual_frontend_image_id=expected_image_id,
        )

    assert frontend_inspects == 2
    assert removed == []


def test_deploy_reuses_valid_existing_commit_tag_without_rebuilding(monkeypatch, tmp_path):
    commit = "1" * 40
    _write_compose_files(tmp_path)
    build_commands: list[list[str]] = []
    repository = AUTHORITATIVE_REPOSITORY

    monkeypatch.setattr("tools.release_authority.assert_clean_commit", lambda repo, requested: commit)
    monkeypatch.setattr("tools.release_authority._git", lambda repo, *args: repository + "\n")
    monkeypatch.setattr(
        "tools.release_authority._image_record",
        lambda docker, image: {
            "id": "sha256:frontend" if "frontend" in image else SANDBOX_IMAGE_ID,
            "labels": {
                "ai-platform.source-commit": commit,
                "org.opencontainers.image.revision": commit,
                "ai-platform.source-repository": repository,
                "ai-platform.build-dirty": "false",
                "ai-platform.release-role": "frontend" if "frontend" in image else "backend",
            },
        },
    )

    def fake_run(command, **kwargs):
        if len(command) >= 3 and command[-3:-1] == ["container", "inspect"]:
            return subprocess.CompletedProcess(command, 1, stdout="", stderr="not found")
        if "build" in command:
            build_commands.append(list(command))
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr("tools.release_authority._run", fake_run)

    deploy_clean_commit(
        tmp_path,
        commit,
        docker_cmd="docker",
        env_file=tmp_path / ".env",
        replace_known_manual_frontend=False,
    )

    assert build_commands == []


def test_sandbox_executor_preflight_requires_exact_clean_backend_image(monkeypatch):
    commit = "7" * 40
    reference = f"ai-platform:{commit}"
    valid_labels = {
        "ai-platform.source-commit": commit,
        "org.opencontainers.image.revision": commit,
        "ai-platform.source-repository": AUTHORITATIVE_REPOSITORY,
        "ai-platform.build-dirty": "false",
        "ai-platform.release-role": "backend",
    }

    with pytest.raises(ReleaseAuthorityError, match="sandbox executor image is missing"):
        monkeypatch.setattr(
            "tools.release_authority._image_record",
            lambda docker, image: (_ for _ in ()).throw(
                subprocess.CalledProcessError(1, [*docker, "image", "inspect", image])
            ),
        )
        release_authority._require_sandbox_executor_image(
            ["docker"],
            reference,
            commit=commit,
            repository=AUTHORITATIVE_REPOSITORY,
        )

    with pytest.raises(ReleaseAuthorityError, match="exact immutable backend reference"):
        release_authority._require_sandbox_executor_image(
            ["docker"],
            "ai-platform:local",
            commit=commit,
            repository=AUTHORITATIVE_REPOSITORY,
        )

    for stale_labels in (
        {**valid_labels, "ai-platform.source-commit": "8" * 40},
        {**valid_labels, "ai-platform.build-dirty": "true"},
        {**valid_labels, "ai-platform.release-role": "frontend"},
        {**valid_labels, "ai-platform.source-repository": "https://example.invalid/fork.git"},
    ):
        monkeypatch.setattr(
            "tools.release_authority._image_record",
            lambda docker, image, labels=stale_labels: {"id": SANDBOX_IMAGE_ID, "labels": labels},
        )
        with pytest.raises(ReleaseAuthorityError, match="sandbox executor image provenance mismatch"):
            release_authority._require_sandbox_executor_image(
                ["docker"],
                reference,
                commit=commit,
                repository=AUTHORITATIVE_REPOSITORY,
            )


@pytest.mark.parametrize("image_id", ("ai-platform:" + "7" * 40, "sha256:not-a-digest", ""))
def test_governed_sandbox_executor_handoff_requires_a_local_immutable_image_id(image_id):
    assert release_authority._immutable_sandbox_executor_reference({"id": SANDBOX_IMAGE_ID}) == SANDBOX_IMAGE_ID

    with pytest.raises(ReleaseAuthorityError, match="not immutable"):
        release_authority._immutable_sandbox_executor_reference({"id": image_id})


def test_deploy_rejects_executor_preflight_without_compose_mutation(monkeypatch, tmp_path):
    commit = "6" * 40
    _write_compose_files(tmp_path)
    commands: list[list[str]] = []
    env_file = tmp_path / ".env"
    backend_inspections = 0

    monkeypatch.setattr("tools.release_authority.assert_clean_commit", lambda repo, requested: commit)
    monkeypatch.setattr("tools.release_authority._git", lambda repo, *args: AUTHORITATIVE_REPOSITORY + "\n")

    def fake_image_record(docker, image):
        nonlocal backend_inspections
        if image == f"ai-platform:{commit}":
            backend_inspections += 1
            if backend_inspections == 2:
                raise subprocess.CalledProcessError(1, [*docker, "image", "inspect", image])
        return {
            "id": "sha256:frontend" if "frontend" in image else SANDBOX_IMAGE_ID,
            "labels": {
                "ai-platform.source-commit": commit,
                "org.opencontainers.image.revision": commit,
                "ai-platform.source-repository": AUTHORITATIVE_REPOSITORY,
                "ai-platform.build-dirty": "false",
                "ai-platform.release-role": "frontend" if "frontend" in image else "backend",
            },
        }

    monkeypatch.setattr("tools.release_authority._image_record", fake_image_record)

    def fake_run(command, **kwargs):
        commands.append(list(command))
        if len(command) >= 3 and command[-3:-1] == ["container", "inspect"]:
            return subprocess.CompletedProcess(command, 1, stdout="", stderr="not found")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(
        "tools.release_authority._run",
        fake_run,
    )

    with pytest.raises(ReleaseAuthorityError, match="sandbox executor image is missing"):
        deploy_clean_commit(
            tmp_path,
            commit,
            docker_cmd="docker",
            env_file=env_file,
            replace_known_manual_frontend=False,
        )

    assert not env_file.exists()
    assert not any("compose" in command for command in commands)


def test_deploy_rejects_existing_commit_tag_with_wrong_provenance(monkeypatch, tmp_path):
    commit = "3" * 40
    _write_compose_files(tmp_path)
    repository = AUTHORITATIVE_REPOSITORY
    build_commands: list[list[str]] = []

    monkeypatch.setattr("tools.release_authority.assert_clean_commit", lambda repo, requested: commit)
    monkeypatch.setattr("tools.release_authority._git", lambda repo, *args: repository + "\n")
    monkeypatch.setattr(
        "tools.release_authority._image_record",
        lambda docker, image: {
            "id": "sha256:wrong",
            "labels": {
                "ai-platform.source-commit": "4" * 40,
                "org.opencontainers.image.revision": "4" * 40,
                "ai-platform.source-repository": repository,
                "ai-platform.build-dirty": "false",
                "ai-platform.release-role": "frontend" if "frontend" in image else "backend",
            },
        },
    )

    def fake_run(command, **kwargs):
        if len(command) >= 3 and command[-3:-1] == ["container", "inspect"]:
            return subprocess.CompletedProcess(command, 1, stdout="", stderr="not found")
        if "build" in command:
            build_commands.append(list(command))
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr("tools.release_authority._run", fake_run)

    try:
        deploy_clean_commit(
            tmp_path,
            commit,
            docker_cmd="docker",
            env_file=tmp_path / ".env",
            replace_known_manual_frontend=False,
        )
    except ReleaseAuthorityError as exc:
        assert "backend image label mismatch" in str(exc)
    else:
        raise AssertionError("an existing commit tag with different provenance must be rejected")

    assert build_commands == []


def test_deploy_rejects_spoofed_repo_owned_frontend(monkeypatch, tmp_path):
    commit = "2" * 40
    _write_compose_files(tmp_path)
    repository = AUTHORITATIVE_REPOSITORY

    monkeypatch.setattr("tools.release_authority.assert_clean_commit", lambda repo, requested: commit)
    monkeypatch.setattr("tools.release_authority._git", lambda repo, *args: repository + "\n")
    monkeypatch.setattr(
        "tools.release_authority._image_record",
        lambda docker, image: {
            "id": "sha256:image",
            "labels": {
                "ai-platform.source-commit": commit,
                "org.opencontainers.image.revision": commit,
                "ai-platform.source-repository": repository,
                "ai-platform.build-dirty": "false",
                "ai-platform.release-role": "frontend" if "frontend" in image else "backend",
            },
        },
    )

    def fake_run(command, **kwargs):
        if command[-3:] == ["container", "inspect", "ai-platform-frontend"]:
            return subprocess.CompletedProcess(
                command,
                0,
                stdout=json.dumps(
                    [
                        {
                            "Image": "sha256:old",
                            "Config": {
                                "Image": "ai-platform-frontend:old",
                                "Labels": {
                                    "ai-platform.release-owner": "repo-local-compose",
                                    "com.docker.compose.project.working_dir": "/legacy/deploy/ai-platform",
                                    "com.docker.compose.project.config_files": "/legacy/deploy/ai-platform/docker-compose.yml",
                                },
                            },
                        }
                    ]
                ),
                stderr="",
            )
        if command[-2:] in (["inspect", "ai-platform-api"], ["inspect", "ai-platform-worker"]):
            return subprocess.CompletedProcess(command, 1, stdout="", stderr="not found")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr("tools.release_authority._run", fake_run)

    try:
        deploy_clean_commit(
            tmp_path,
            commit,
            docker_cmd="docker",
            env_file=tmp_path / ".env",
            replace_known_manual_frontend=False,
        )
    except ReleaseAuthorityError as exc:
        assert "frontend compose ownership mismatch" in str(exc)
    else:
        raise AssertionError("spoofed repo-local ownership must be rejected")


def test_release_authority_cli_exposes_preserve_deploy_and_verify_commands():
    result = subprocess.run(
        [sys.executable, "tools/release_authority.py", "--help"],
        check=True,
        capture_output=True,
        text=True,
    )

    assert "preserve-dirty" in result.stdout
    assert "deploy" in result.stdout
    assert "verify" in result.stdout

    deploy_help = subprocess.run(
        [sys.executable, "tools/release_authority.py", "deploy", "--help"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    assert "--expected-manual-frontend-image" in deploy_help
    assert "--expected-manual-frontend-image-id" in deploy_help
    assert "--compose-file" in deploy_help

    verify_help = subprocess.run(
        [sys.executable, "tools/release_authority.py", "verify", "--help"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    assert "--compose-dir" not in verify_help
    assert "--frontend-provenance-url" not in verify_help
    assert "--compose-file" in verify_help


def test_release_authority_cli_redacts_low_level_command_failures(monkeypatch, capsys, tmp_path):
    def fail_without_leaking(*args, **kwargs):
        raise subprocess.CalledProcessError(
            1,
            ["docker", "private-marker-command"],
            output="private-marker-output",
            stderr="private-marker-secret",
        )

    monkeypatch.setattr("tools.release_authority.collect_live_parity", fail_without_leaking)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "release_authority.py",
            "verify",
            "--repo-root",
            str(tmp_path),
            "--commit",
            "d" * 40,
        ],
    )

    assert release_authority.main() == 2
    report = json.loads(capsys.readouterr().out)
    assert report == {
        "command": "verify",
        "error": "release authority command failed",
        "verified": False,
    }
    assert "private-marker" not in json.dumps(report)


def test_deploy_uses_211_sudo_env_compose_command(monkeypatch, tmp_path):
    commit = "5" * 40
    _write_compose_files(tmp_path)
    repository = AUTHORITATIVE_REPOSITORY
    commands: list[list[str]] = []
    image_records = {
        f"ai-platform:{commit}": {
            "id": SANDBOX_IMAGE_ID,
            "labels": {
                "ai-platform.source-commit": commit,
                "org.opencontainers.image.revision": commit,
                "ai-platform.source-repository": repository,
                "ai-platform.build-dirty": "false",
                "ai-platform.release-role": "backend",
            },
        },
        f"ai-platform-frontend:{commit}": {
            "id": "sha256:frontend",
            "labels": {
                "ai-platform.source-commit": commit,
                "org.opencontainers.image.revision": commit,
                "ai-platform.source-repository": repository,
                "ai-platform.build-dirty": "false",
                "ai-platform.release-role": "frontend",
            },
        },
    }

    monkeypatch.setattr("tools.release_authority.assert_clean_commit", lambda repo, requested: commit)
    monkeypatch.setattr("tools.release_authority._git", lambda repo, *args: repository + "\n")
    monkeypatch.setattr("tools.release_authority._image_record", lambda docker, image: image_records[image])

    def fake_run(command, **kwargs):
        commands.append(list(command))
        if len(command) >= 3 and command[-3:-1] == ["container", "inspect"]:
            return subprocess.CompletedProcess(command, 1, stdout="", stderr="not found")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr("tools.release_authority._run", fake_run)

    env_file = tmp_path / ".env"
    deploy_clean_commit(
        tmp_path,
        commit,
        docker_cmd="sudo -n docker",
        env_file=env_file,
        replace_known_manual_frontend=False,
    )

    compose = next(command for command in commands if "compose" in command)
    assert compose[:3] == ["sudo", "-n", "env"]
    assert f"AI_PLATFORM_IMAGE=ai-platform:{commit}" in compose
    assert f"AI_PLATFORM_FRONTEND_IMAGE=ai-platform-frontend:{commit}" in compose
    assert f"SANDBOX_EXECUTOR_IMAGE={SANDBOX_IMAGE_ID}" in compose
    assert compose[compose.index("compose") :] == [
        "compose",
        "-p",
        "ai-platform-phaseb",
        "--env-file",
        str(env_file.resolve()),
        "-f",
        str((tmp_path / "deploy" / "ai-platform" / "docker-compose.yml").resolve()),
        "up",
        "-d",
        "--no-build",
    ]


def test_deploy_preserves_exact_two_file_ownership_and_compose_command(monkeypatch, tmp_path):
    commit = "7" * 40
    main_compose, sandbox_compose = _write_compose_files(tmp_path)
    compose_dir = main_compose.parent
    config_files = _compose_config_value(main_compose, sandbox_compose)
    events: list[str] = []
    commands: list[list[str]] = []
    image_records = {
        f"ai-platform:{commit}": {
            "id": SANDBOX_IMAGE_ID,
            "labels": {
                "ai-platform.source-commit": commit,
                "org.opencontainers.image.revision": commit,
                "ai-platform.source-repository": AUTHORITATIVE_REPOSITORY,
                "ai-platform.build-dirty": "false",
                "ai-platform.release-role": "backend",
            },
        },
        f"ai-platform-frontend:{commit}": {
            "id": "sha256:frontend",
            "labels": {
                "ai-platform.source-commit": commit,
                "org.opencontainers.image.revision": commit,
                "ai-platform.source-repository": AUTHORITATIVE_REPOSITORY,
                "ai-platform.build-dirty": "false",
                "ai-platform.release-role": "frontend",
            },
        },
    }
    monkeypatch.setattr("tools.release_authority.assert_clean_commit", lambda repo, requested: commit)
    monkeypatch.setattr(
        "tools.release_authority._git",
        lambda repo, *args: AUTHORITATIVE_REPOSITORY + "\n",
    )

    def fake_image_record(docker, image):
        events.append(f"image:{image}")
        return image_records[image]

    monkeypatch.setattr("tools.release_authority._image_record", fake_image_record)

    def fake_run(command, **kwargs):
        commands.append(list(command))
        if len(command) >= 3 and command[-3:-1] == ["container", "inspect"]:
            role = command[-1].removeprefix("ai-platform-")
            events.append(f"container:{role}")
            return subprocess.CompletedProcess(
                command,
                0,
                stdout=json.dumps(_owned_container_payload(role, compose_dir, config_files)),
                stderr="",
            )
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr("tools.release_authority._run", fake_run)

    deployment = deploy_clean_commit(
        tmp_path,
        commit,
        docker_cmd="sudo -n docker",
        env_file=tmp_path / ".env",
        replace_known_manual_frontend=False,
        compose_files=[COMPOSE_RELATIVE_PATH, SANDBOX_COMPOSE_RELATIVE_PATH],
    )

    assert events[:3] == ["container:api", "container:worker", "container:frontend"]
    assert events[3].startswith("image:")
    compose = next(command for command in commands if "compose" in command)
    assert compose[compose.index("compose") :] == [
        "compose",
        "-p",
        "ai-platform-phaseb",
        "--env-file",
        str((tmp_path / ".env").resolve()),
        "-f",
        str(main_compose.resolve()),
        "-f",
        str(sandbox_compose.resolve()),
        "up",
        "-d",
        "--no-build",
    ]
    assert deployment["compose_files"] == [
        str(main_compose.resolve()),
        str(sandbox_compose.resolve()),
    ]


@pytest.mark.parametrize(
    ("prior_overlay_relative", "target_overlay_relative"),
    [
        (SANDBOX_COMPOSE_RELATIVE_PATH, OPENSANDBOX_COMPOSE_RELATIVE_PATH),
        (OPENSANDBOX_COMPOSE_RELATIVE_PATH, SANDBOX_COMPOSE_RELATIVE_PATH),
    ],
)
def test_deploy_accepts_exact_provider_overlay_transition_and_rollback(
    monkeypatch,
    tmp_path,
    prior_overlay_relative,
    target_overlay_relative,
):
    commit = "7" * 40
    prior_commit = "678d3c46"
    release_root = tmp_path / "releases"
    target = release_root / commit
    prior = release_root / prior_commit
    target_main, target_sandbox, target_opensandbox = _write_provider_compose_files(target)
    prior_main, prior_sandbox, prior_opensandbox = _write_provider_compose_files(prior)
    target_overlays = {
        SANDBOX_COMPOSE_RELATIVE_PATH: target_sandbox,
        OPENSANDBOX_COMPOSE_RELATIVE_PATH: target_opensandbox,
    }
    prior_overlays = {
        SANDBOX_COMPOSE_RELATIVE_PATH: prior_sandbox,
        OPENSANDBOX_COMPOSE_RELATIVE_PATH: prior_opensandbox,
    }
    target_overlay = target_overlays[target_overlay_relative]
    prior_overlay = prior_overlays[prior_overlay_relative]
    assert not (prior / ".git").exists()
    prior_config = _compose_config_value(prior_main, prior_overlay)
    events: list[str] = []
    commands: list[list[str]] = []
    image_records = {
        f"ai-platform:{commit}": {
            "id": SANDBOX_IMAGE_ID,
            "labels": {
                "ai-platform.source-commit": commit,
                "org.opencontainers.image.revision": commit,
                "ai-platform.source-repository": AUTHORITATIVE_REPOSITORY,
                "ai-platform.build-dirty": "false",
                "ai-platform.release-role": "backend",
            },
        },
        f"ai-platform-frontend:{commit}": {
            "id": "sha256:frontend",
            "labels": {
                "ai-platform.source-commit": commit,
                "org.opencontainers.image.revision": commit,
                "ai-platform.source-repository": AUTHORITATIVE_REPOSITORY,
                "ai-platform.build-dirty": "false",
                "ai-platform.release-role": "frontend",
            },
        },
    }
    monkeypatch.setattr("tools.release_authority.assert_clean_commit", lambda repo, requested: commit)
    monkeypatch.setattr(
        "tools.release_authority._git",
        lambda repo, *args: AUTHORITATIVE_REPOSITORY + "\n",
    )

    def fake_image_record(docker, image):
        events.append(f"image:{image}")
        return image_records[image]

    monkeypatch.setattr("tools.release_authority._image_record", fake_image_record)

    def fake_run(command, **kwargs):
        commands.append(list(command))
        if len(command) >= 3 and command[-3:-1] == ["container", "inspect"]:
            role = command[-1].removeprefix("ai-platform-")
            events.append(f"container:{role}")
            return subprocess.CompletedProcess(
                command,
                0,
                stdout=json.dumps(
                    _owned_container_payload(role, prior_main.parent, prior_config)
                ),
                stderr="",
            )
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr("tools.release_authority._run", fake_run)

    deploy_clean_commit(
        target,
        commit,
        docker_cmd="docker",
        env_file=tmp_path / ".env",
        replace_known_manual_frontend=False,
        compose_files=[COMPOSE_RELATIVE_PATH, target_overlay_relative],
    )

    assert events[:3] == ["container:api", "container:worker", "container:frontend"]
    assert events[3].startswith("image:")
    compose = next(command for command in commands if "compose" in command)
    assert compose[compose.index("compose") :] == [
        "compose",
        "-p",
        "ai-platform-phaseb",
        "--env-file",
        str((tmp_path / ".env").resolve()),
        "-f",
        str(target_main.resolve()),
        "-f",
        str(target_overlay.resolve()),
        "up",
        "-d",
        "--no-build",
    ]


def test_verified_current_runtime_uses_label_derived_historical_provider_selection(
    monkeypatch,
    tmp_path,
):
    current_commit = "6" * 40
    target_commit = "7" * 40
    release_root = tmp_path / "releases"
    target = release_root / target_commit
    prior = release_root / "678d3c46"
    target_main, _, target_opensandbox = _write_provider_compose_files(target)
    prior_main, prior_sandbox, _ = _write_provider_compose_files(prior)
    target_selection = release_authority.resolve_compose_files(
        target,
        [COMPOSE_RELATIVE_PATH, OPENSANDBOX_COMPOSE_RELATIVE_PATH],
    )
    prior_config = _compose_config_value(prior_main, prior_sandbox)
    parity_calls: list[tuple[Path, str, tuple[str, ...]]] = []

    def fake_container_inspect(docker, name):
        role = name.removeprefix("ai-platform-")
        payload = _owned_container_payload(role, prior_main.parent, prior_config)[0]
        labels = payload["Config"]["Labels"]
        labels["ai-platform.source-commit"] = current_commit
        labels["ai-platform.source-dirty"] = "false"
        return {"labels": labels}, payload

    def fake_parity(repo_root, commit, **kwargs):
        parity_calls.append((repo_root, commit, tuple(kwargs["compose_files"])))
        return {"verified": True}

    monkeypatch.setattr(
        "tools.release_authority._container_inspect_record",
        fake_container_inspect,
    )
    monkeypatch.setattr("tools.release_authority.collect_live_parity", fake_parity)

    current = release_authority._verified_current_runtime(
        ["docker"],
        target_selection,
        docker_cmd="docker",
    )

    assert target_main.is_file() and target_opensandbox.is_file()
    assert current["commit"] == current_commit
    assert parity_calls == [
        (
            prior.resolve(),
            current_commit,
            (COMPOSE_RELATIVE_PATH, SANDBOX_COMPOSE_RELATIVE_PATH),
        )
    ]


@pytest.mark.parametrize(
    "invalid_selection",
    [
        "base_only_observed",
        "arbitrary_observed",
        "reordered_observed",
        "extra_observed",
        "missing_observed",
        "duplicate_observed",
        "escaped_observed",
        "linked_observed",
        "non_sibling_observed",
        "target_base_only",
        "caller_selected_target",
    ],
)
def test_provider_overlay_transition_rejects_non_allowlisted_selections(
    monkeypatch,
    tmp_path,
    invalid_selection,
):
    commit = "7" * 40
    release_root = tmp_path / "releases"
    target = release_root / commit
    prior = release_root / "678d3c46"
    target_main, _, target_opensandbox = _write_provider_compose_files(target)
    prior_main, prior_sandbox, prior_opensandbox = _write_provider_compose_files(prior)
    target_arbitrary = target_main.with_name("docker-compose.caller-selected.yml")
    target_arbitrary.write_text("services: {}\n", encoding="utf-8")
    prior_arbitrary = prior_main.with_name("docker-compose.arbitrary.yml")
    prior_arbitrary.write_text("services: {}\n", encoding="utf-8")
    other_root = tmp_path / "other-releases" / "abcdef12"
    other_main, _, other_opensandbox = _write_provider_compose_files(other_root)

    target_paths = [COMPOSE_RELATIVE_PATH, OPENSANDBOX_COMPOSE_RELATIVE_PATH]
    observed_paths = [prior_main, prior_sandbox]
    if invalid_selection == "base_only_observed":
        observed_paths = [prior_main]
    elif invalid_selection == "arbitrary_observed":
        observed_paths = [prior_main, prior_arbitrary]
    elif invalid_selection == "reordered_observed":
        observed_paths = [prior_sandbox, prior_main]
    elif invalid_selection == "extra_observed":
        observed_paths = [prior_main, prior_sandbox, prior_opensandbox]
    elif invalid_selection == "missing_observed":
        observed_paths = [prior_main, prior_main.with_name("docker-compose.missing.yml")]
    elif invalid_selection == "duplicate_observed":
        observed_paths = [prior_main, prior_main]
    elif invalid_selection == "escaped_observed":
        observed_paths = [prior_main, other_opensandbox]
    elif invalid_selection == "non_sibling_observed":
        observed_paths = [other_main, other_opensandbox]
    elif invalid_selection == "target_base_only":
        target_paths = [COMPOSE_RELATIVE_PATH]
    elif invalid_selection == "caller_selected_target":
        target_paths = [
            COMPOSE_RELATIVE_PATH,
            "deploy/ai-platform/docker-compose.caller-selected.yml",
        ]

    target_selection = release_authority.resolve_compose_files(target, target_paths)
    labels = _owned_container_payload(
        "api",
        observed_paths[0].parent,
        _compose_config_value(*observed_paths),
    )[0]["Config"]["Labels"]
    if invalid_selection == "linked_observed":
        original = release_authority._is_link_or_junction
        monkeypatch.setattr(
            "tools.release_authority._is_link_or_junction",
            lambda path: Path(path) == prior_sandbox or original(Path(path)),
        )

    assert target_opensandbox.is_file()
    assert release_authority._compose_ownership_selection(labels, target_selection) is None


@pytest.mark.parametrize(
    "identity_mismatch",
    ["project", "release_role", "compose_service", "manual_api"],
)
def test_provider_overlay_transition_rejects_project_role_or_manual_identity(
    monkeypatch,
    tmp_path,
    identity_mismatch,
):
    commit = "7" * 40
    release_root = tmp_path / "releases"
    target = release_root / commit
    prior = release_root / "678d3c46"
    _write_provider_compose_files(target)
    prior_main, prior_sandbox, _ = _write_provider_compose_files(prior)
    selection = release_authority.resolve_compose_files(
        target,
        [COMPOSE_RELATIVE_PATH, OPENSANDBOX_COMPOSE_RELATIVE_PATH],
    )
    inspected = _owned_container_payload(
        "api",
        prior_main.parent,
        _compose_config_value(prior_main, prior_sandbox),
    )[0]
    labels = inspected["Config"]["Labels"]
    if identity_mismatch == "project":
        labels["com.docker.compose.project"] = "other-project"
    elif identity_mismatch == "release_role":
        labels["ai-platform.release-role"] = "worker"
    elif identity_mismatch == "compose_service":
        labels["com.docker.compose.service"] = "worker"
    else:
        labels["ai-platform.release-owner"] = "manual"

    monkeypatch.setattr(
        "tools.release_authority._inspect_optional_container",
        lambda docker, name: inspected if name == "ai-platform-api" else None,
    )

    with pytest.raises(ReleaseAuthorityError, match="^api compose ownership mismatch$"):
        release_authority._preflight_managed_container_ownership(
            ["docker"],
            selection,
            replace_known_manual_frontend=False,
            expected_manual_frontend_image=None,
            expected_manual_frontend_image_id=None,
        )


def test_provider_overlay_transition_requires_one_owned_selection_for_all_roles(
    monkeypatch,
    tmp_path,
):
    commit = "7" * 40
    release_root = tmp_path / "releases"
    target = release_root / commit
    prior = release_root / "678d3c46"
    _write_provider_compose_files(target)
    prior_main, prior_sandbox, prior_opensandbox = _write_provider_compose_files(prior)
    selection = release_authority.resolve_compose_files(
        target,
        [COMPOSE_RELATIVE_PATH, OPENSANDBOX_COMPOSE_RELATIVE_PATH],
    )
    configs = {
        "api": _compose_config_value(prior_main, prior_sandbox),
        "worker": _compose_config_value(prior_main, prior_opensandbox),
        "frontend": _compose_config_value(prior_main, prior_sandbox),
    }

    def fake_inspect(docker, name):
        role = name.removeprefix("ai-platform-")
        return _owned_container_payload(role, prior_main.parent, configs[role])[0]

    monkeypatch.setattr("tools.release_authority._inspect_optional_container", fake_inspect)

    with pytest.raises(ReleaseAuthorityError, match="^worker compose ownership mismatch$"):
        release_authority._preflight_managed_container_ownership(
            ["docker"],
            selection,
            replace_known_manual_frontend=False,
            expected_manual_frontend_image=None,
            expected_manual_frontend_image_id=None,
        )


@pytest.mark.parametrize("missing_role", ["api", "worker", "frontend"])
def test_provider_overlay_transition_requires_all_three_compose_owned_roles(
    monkeypatch,
    tmp_path,
    missing_role,
):
    commit = "7" * 40
    release_root = tmp_path / "releases"
    target = release_root / commit
    prior = release_root / "678d3c46"
    _write_provider_compose_files(target)
    prior_main, prior_sandbox, _ = _write_provider_compose_files(prior)
    selection = release_authority.resolve_compose_files(
        target,
        [COMPOSE_RELATIVE_PATH, OPENSANDBOX_COMPOSE_RELATIVE_PATH],
    )
    prior_config = _compose_config_value(prior_main, prior_sandbox)

    def fake_inspect(docker, name):
        role = name.removeprefix("ai-platform-")
        if role == missing_role:
            return None
        return _owned_container_payload(role, prior_main.parent, prior_config)[0]

    monkeypatch.setattr("tools.release_authority._inspect_optional_container", fake_inspect)

    with pytest.raises(
        ReleaseAuthorityError,
        match=rf"^{missing_role} compose ownership mismatch$",
    ):
        release_authority._preflight_managed_container_ownership(
            ["docker"],
            selection,
            replace_known_manual_frontend=False,
            expected_manual_frontend_image=None,
            expected_manual_frontend_image_id=None,
        )


def test_provider_overlay_transition_forbids_manual_frontend_replacement(
    monkeypatch,
    tmp_path,
):
    commit = "7" * 40
    release_root = tmp_path / "releases"
    target = release_root / commit
    prior = release_root / "678d3c46"
    _write_provider_compose_files(target)
    prior_main, prior_sandbox, _ = _write_provider_compose_files(prior)
    selection = release_authority.resolve_compose_files(
        target,
        [COMPOSE_RELATIVE_PATH, OPENSANDBOX_COMPOSE_RELATIVE_PATH],
    )
    prior_config = _compose_config_value(prior_main, prior_sandbox)
    manual_image = "ai-platform-frontend:manual"
    manual_image_id = "sha256:" + "1" * 64
    manual_container_id = "a" * 64

    def fake_inspect(docker, name):
        role = name.removeprefix("ai-platform-")
        if role != "frontend":
            return _owned_container_payload(role, prior_main.parent, prior_config)[0]
        return {
            "Id": manual_container_id,
            "Image": manual_image_id,
            "Config": {"Image": manual_image, "Labels": {}},
        }

    monkeypatch.setattr("tools.release_authority._inspect_optional_container", fake_inspect)

    with pytest.raises(ReleaseAuthorityError, match="^frontend compose ownership mismatch$"):
        release_authority._preflight_managed_container_ownership(
            ["docker"],
            selection,
            replace_known_manual_frontend=True,
            expected_manual_frontend_image=manual_image,
            expected_manual_frontend_image_id=manual_image_id,
        )


def test_deploy_rejects_provider_ownership_change_during_preflight_revalidation(
    monkeypatch,
    tmp_path,
):
    commit = "8" * 40
    release_root = tmp_path / "releases"
    target = release_root / commit
    prior = release_root / "678d3c46"
    _write_provider_compose_files(target)
    prior_main, prior_sandbox, prior_opensandbox = _write_provider_compose_files(prior)
    prior_configs = (
        _compose_config_value(prior_main, prior_sandbox),
        _compose_config_value(prior_main, prior_opensandbox),
    )
    inspect_count = 0
    commands: list[list[str]] = []
    image_records = {
        f"ai-platform:{commit}": {
            "id": SANDBOX_IMAGE_ID,
            "labels": _published_image_labels(commit, "backend"),
        },
        f"ai-platform-frontend:{commit}": {
            "id": "sha256:frontend",
            "labels": _published_image_labels(commit, "frontend"),
        },
    }
    monkeypatch.setattr("tools.release_authority.assert_clean_commit", lambda repo, requested: commit)
    monkeypatch.setattr(
        "tools.release_authority._git",
        lambda repo, *args: AUTHORITATIVE_REPOSITORY + "\n",
    )
    monkeypatch.setattr(
        "tools.release_authority._image_record",
        lambda docker, image: image_records[image],
    )

    def fake_run(command, **kwargs):
        nonlocal inspect_count
        command = list(command)
        commands.append(command)
        if len(command) >= 3 and command[-3:-1] == ["container", "inspect"]:
            role = command[-1].removeprefix("ai-platform-")
            preflight_round = inspect_count // 3
            inspect_count += 1
            return subprocess.CompletedProcess(
                command,
                0,
                stdout=json.dumps(
                    _owned_container_payload(
                        role,
                        prior_main.parent,
                        prior_configs[preflight_round],
                    )
                ),
                stderr="",
            )
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr("tools.release_authority._run", fake_run)

    with pytest.raises(
        ReleaseAuthorityError,
        match="^managed container ownership changed during release preflight$",
    ):
        deploy_clean_commit(
            target,
            commit,
            docker_cmd="docker",
            env_file=tmp_path / ".env",
            replace_known_manual_frontend=False,
            compose_files=[COMPOSE_RELATIVE_PATH, OPENSANDBOX_COMPOSE_RELATIVE_PATH],
        )

    assert inspect_count == 6
    assert not any("compose" in command for command in commands)


@pytest.mark.parametrize(
    "invalid_prior",
    ["non_commit", "non_sibling", "linked_file", "wrong_order"],
)
def test_prior_release_ownership_rejects_untrusted_root_or_relative_set(
    monkeypatch,
    tmp_path,
    invalid_prior,
):
    commit = "7" * 40
    release_root = tmp_path / "releases"
    target = release_root / commit
    _write_compose_files(target)
    selection = release_authority.resolve_compose_files(
        target,
        [COMPOSE_RELATIVE_PATH, SANDBOX_COMPOSE_RELATIVE_PATH],
    )
    if invalid_prior == "non_commit":
        prior = release_root / "not-a-commit"
    elif invalid_prior == "non_sibling":
        prior = tmp_path / "other-releases" / "678d3c46"
    else:
        prior = release_root / "678d3c46"
    prior_main, prior_sandbox = _write_compose_files(prior)
    assert not (prior / ".git").exists()
    config_files = _compose_config_value(prior_main, prior_sandbox)
    if invalid_prior == "wrong_order":
        config_files = _compose_config_value(prior_sandbox, prior_main)
    if invalid_prior == "linked_file":
        original = release_authority._is_link_or_junction
        monkeypatch.setattr(
            "tools.release_authority._is_link_or_junction",
            lambda path: Path(path) == prior_sandbox or original(Path(path)),
        )
    labels = _owned_container_payload("api", prior_main.parent, config_files)[0][
        "Config"
    ]["Labels"]

    assert release_authority._compose_ownership_selection(labels, selection) is None


def test_deploy_rejects_prior_release_root_split_before_image_lookup(
    monkeypatch,
    tmp_path,
):
    commit = "7" * 40
    release_root = tmp_path / "releases"
    target = release_root / commit
    _write_compose_files(target)
    prior_api = release_root / "678d3c46"
    prior_worker = release_root / "abcdef12"
    api_main, api_sandbox = _write_compose_files(prior_api)
    worker_main, worker_sandbox = _write_compose_files(prior_worker)
    image_lookups: list[str] = []

    monkeypatch.setattr("tools.release_authority.assert_clean_commit", lambda repo, requested: commit)
    monkeypatch.setattr(
        "tools.release_authority._git",
        lambda repo, *args: AUTHORITATIVE_REPOSITORY + "\n",
    )

    def forbidden_image_lookup(docker, image):
        image_lookups.append(image)
        raise AssertionError("split prior ownership must fail before image lookup")

    monkeypatch.setattr("tools.release_authority._image_record", forbidden_image_lookup)

    def fake_run(command, **kwargs):
        if len(command) >= 3 and command[-3:-1] == ["container", "inspect"]:
            role = command[-1].removeprefix("ai-platform-")
            if role == "worker":
                compose_dir = worker_main.parent
                config_files = _compose_config_value(worker_main, worker_sandbox)
            else:
                compose_dir = api_main.parent
                config_files = _compose_config_value(api_main, api_sandbox)
            return subprocess.CompletedProcess(
                command,
                0,
                stdout=json.dumps(_owned_container_payload(role, compose_dir, config_files)),
                stderr="",
            )
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr("tools.release_authority._run", fake_run)

    with pytest.raises(ReleaseAuthorityError) as exc_info:
        deploy_clean_commit(
            target,
            commit,
            docker_cmd="docker",
            env_file=tmp_path / ".env",
            replace_known_manual_frontend=False,
            compose_files=[COMPOSE_RELATIVE_PATH, SANDBOX_COMPOSE_RELATIVE_PATH],
        )

    assert str(exc_info.value) == "worker compose ownership mismatch"
    assert image_lookups == []


@pytest.mark.parametrize("mismatched_role", ["api", "worker", "frontend"])
def test_deploy_rejects_ordered_compose_ownership_mismatch_before_image_lookup(
    monkeypatch,
    tmp_path,
    mismatched_role,
):
    commit = "8" * 40
    main_compose, sandbox_compose = _write_compose_files(tmp_path)
    compose_dir = main_compose.parent
    exact_config = _compose_config_value(main_compose, sandbox_compose)
    reversed_config = _compose_config_value(sandbox_compose, main_compose)
    image_lookups: list[str] = []
    monkeypatch.setattr("tools.release_authority.assert_clean_commit", lambda repo, requested: commit)
    monkeypatch.setattr(
        "tools.release_authority._git",
        lambda repo, *args: AUTHORITATIVE_REPOSITORY + "\n",
    )

    def forbidden_image_lookup(docker, image):
        image_lookups.append(image)
        raise AssertionError("image lookup must follow ownership validation")

    monkeypatch.setattr("tools.release_authority._image_record", forbidden_image_lookup)

    def fake_run(command, **kwargs):
        if len(command) >= 3 and command[-3:-1] == ["container", "inspect"]:
            role = command[-1].removeprefix("ai-platform-")
            config = reversed_config if role == mismatched_role else exact_config
            return subprocess.CompletedProcess(
                command,
                0,
                stdout=json.dumps(_owned_container_payload(role, compose_dir, config)),
                stderr="",
            )
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr("tools.release_authority._run", fake_run)

    with pytest.raises(ReleaseAuthorityError) as exc_info:
        deploy_clean_commit(
            tmp_path,
            commit,
            docker_cmd="docker",
            env_file=tmp_path / ".env",
            replace_known_manual_frontend=False,
            compose_files=[COMPOSE_RELATIVE_PATH, SANDBOX_COMPOSE_RELATIVE_PATH],
        )

    assert str(exc_info.value) == f"{mismatched_role} compose ownership mismatch"
    assert image_lookups == []


def _install_checkout_git_runner(
    monkeypatch,
    *,
    commit: str,
    origin: str = AUTHORITATIVE_REPOSITORY,
    head: str | None = None,
    status: str = "",
    ancestor_returncode: int = 0,
    ignored_paths: tuple[str, ...] = (),
):
    commands: list[tuple[list[str], Path | None, bool]] = []

    def fake_run(command, *, cwd=None, check=True, text=True, env=None):
        command = list(command)
        cwd_path = Path(cwd) if cwd is not None else None
        commands.append((command, cwd_path, check))
        stdout = ""
        returncode = 0
        if command[:2] == ["git", "init"]:
            assert cwd_path is not None
            (cwd_path / ".git").mkdir()
        elif command[1:4] == ["config", "--get", "remote.origin.url"]:
            stdout = origin + "\n"
        elif command[1:3] == ["rev-parse", "HEAD"]:
            stdout = (head or commit) + "\n"
        elif command[1:3] == ["status", "--porcelain"]:
            stdout = status
        elif command[1:5] == ["ls-files", "--others", "--ignored", "--exclude-standard"]:
            stdout = "\0".join(ignored_paths) + ("\0" if ignored_paths else "")
        elif command[1:3] == ["merge-base", "--is-ancestor"]:
            returncode = ancestor_returncode
        if not text:
            stdout = stdout.encode("utf-8")
            stderr = b""
        else:
            stderr = ""
        result = subprocess.CompletedProcess(command, returncode, stdout=stdout, stderr=stderr)
        if check and returncode:
            raise subprocess.CalledProcessError(returncode, command, output=stdout, stderr="")
        return result

    monkeypatch.setattr("tools.release_authority._run", fake_run)
    monkeypatch.setattr(
        release_authority,
        "_posix_owner_mode",
        lambda path: (1000, 0o755 if Path(path).is_dir() else 0o644),
    )
    return commands


def test_materialize_main_checkout_fetches_explicit_refspec_into_isolated_commit_dir(
    monkeypatch,
    tmp_path,
):
    commit = "6" * 40
    commands = _install_checkout_git_runner(monkeypatch, commit=commit)
    release_root = tmp_path / "releases"

    checkout = release_authority.materialize_main_checkout(release_root, commit)

    assert checkout == release_root / commit
    assert checkout.is_dir()
    assert (checkout / ".git").is_dir()
    git_commands = [command for command, _, _ in commands if command[0] == "git"]
    assert ["git", "fetch", "--no-tags", "origin", "main:refs/remotes/origin/main"] in git_commands
    assert ["git", "cat-file", "-e", f"{commit}^{{commit}}"] in git_commands
    assert ["git", "merge-base", "--is-ancestor", commit, "refs/remotes/origin/main"] in git_commands
    assert ["git", "checkout", "--detach", commit] in git_commands
    assert not any(command[1] in {"archive", "worktree"} for command in git_commands)


def test_materialize_main_checkout_reuses_only_clean_matching_checkout(monkeypatch, tmp_path):
    commit = "7" * 40
    release_root = tmp_path / "releases"
    checkout = release_root / commit
    (checkout / ".git").mkdir(parents=True)
    commands = _install_checkout_git_runner(monkeypatch, commit=commit)

    assert release_authority.materialize_main_checkout(release_root, commit) == checkout

    git_commands = [command for command, _, _ in commands]
    fetch_command = ["git", "fetch", "--no-tags", "origin", "main:refs/remotes/origin/main"]
    assert git_commands.count(fetch_command) == 1
    fetch_index = git_commands.index(fetch_command)
    assert git_commands[:fetch_index].count(
        ["git", "status", "--porcelain", "--untracked-files=all"]
    ) == 1
    assert ["git", "init"] not in git_commands
    assert ["git", "checkout", "--detach", commit] not in git_commands


@pytest.mark.parametrize(
    ("unsafe_subject", "unsafe_metadata", "expected_gate"),
    [
        ("checkout", (1001, 0o755), "target-checkout-authority-ownership"),
        ("checkout", (1000, 0o775), "target-checkout-authority-mode"),
        ("tracked-file", (1001, 0o644), "target-checkout-authority-ownership"),
        ("tracked-file", (1000, 0o666), "target-checkout-authority-mode"),
        ("git-config", (1001, 0o644), "target-checkout-authority-ownership"),
        ("git-object", (1000, 0o666), "target-checkout-authority-mode"),
    ],
)
def test_materialize_main_checkout_rejects_unsafe_existing_tree_before_fetch(
    monkeypatch,
    tmp_path,
    unsafe_subject,
    unsafe_metadata,
    expected_gate,
):
    commit = "4" * 40
    release_root = tmp_path / "releases"
    checkout = release_root / commit
    (checkout / ".git").mkdir(parents=True)
    tracked = checkout / "tracked.txt"
    tracked.write_text("tracked\n", encoding="utf-8")
    git_config = checkout / ".git" / "config"
    git_config.write_text("[core]\n\trepositoryformatversion = 0\n", encoding="utf-8")
    git_object = checkout / ".git" / "objects" / "aa" / ("b" * 38)
    git_object.parent.mkdir(parents=True)
    git_object.write_bytes(b"opaque-object")
    commands = _install_checkout_git_runner(monkeypatch, commit=commit)
    unsafe_paths = {
        "checkout": checkout,
        "tracked-file": tracked,
        "git-config": git_config,
        "git-object": git_object,
    }
    unsafe_path = unsafe_paths[unsafe_subject]
    unsafe_metadata_reads: list[Path] = []

    def owner_mode(path):
        candidate = Path(path)
        if candidate == unsafe_path:
            unsafe_metadata_reads.append(candidate)
            return unsafe_metadata
        return (1000, 0o755 if candidate.is_dir() else 0o644)

    monkeypatch.setattr(release_authority, "_posix_owner_mode", owner_mode)

    with pytest.raises(ReleaseAuthorityError, match=rf"^{expected_gate} gate failed:"):
        release_authority.materialize_main_checkout(release_root, commit)

    assert unsafe_metadata_reads == [unsafe_path]
    assert commands == []


def test_materialize_main_checkout_rejects_dirty_or_non_main_reuse(monkeypatch, tmp_path):
    commit = "8" * 40
    release_root = tmp_path / "releases"
    checkout = release_root / commit
    (checkout / ".git").mkdir(parents=True)
    _install_checkout_git_runner(monkeypatch, commit=commit, status=" M tracked.txt\n")

    try:
        release_authority.materialize_main_checkout(release_root, commit)
    except ReleaseAuthorityError as exc:
        assert str(exc).startswith("target-checkout-cleanliness gate failed:")
    else:
        raise AssertionError("a dirty versioned checkout must not be reused")

    monkeypatch.undo()
    _install_checkout_git_runner(monkeypatch, commit=commit, ancestor_returncode=1)
    try:
        release_authority.materialize_main_checkout(release_root, commit)
    except ReleaseAuthorityError as exc:
        assert "fetched main" in str(exc)
    else:
        raise AssertionError("a commit outside fetched main must be rejected")


def test_materialize_main_checkout_rejects_checkout_head_mismatch(monkeypatch, tmp_path):
    commit = "f" * 40
    release_root = tmp_path / "releases"
    checkout = release_root / commit
    (checkout / ".git").mkdir(parents=True)
    _install_checkout_git_runner(monkeypatch, commit=commit, head="0" * 40)

    try:
        release_authority.materialize_main_checkout(release_root, commit)
    except ReleaseAuthorityError as exc:
        assert "does not match requested commit" in str(exc)
    else:
        raise AssertionError("a version directory with a mismatched HEAD must fail closed")


def test_materialize_main_checkout_rejects_ignored_file_before_reuse(monkeypatch, tmp_path):
    commit = "3" * 40
    release_root = tmp_path / "releases"
    checkout = release_root / commit
    (checkout / ".git").mkdir(parents=True)
    commands = _install_checkout_git_runner(
        monkeypatch,
        commit=commit,
        ignored_paths=("ignored-build-input.bin",),
    )

    try:
        release_authority.materialize_main_checkout(release_root, commit)
    except ReleaseAuthorityError as exc:
        assert str(exc).startswith("target-checkout-ignored-content gate failed:")
    else:
        raise AssertionError("a reused checkout with ignored files must fail closed")

    assert ["git", "fetch", "--no-tags", "origin", "main:refs/remotes/origin/main"] not in [
        command for command, _, _ in commands
    ]


def test_materialize_main_checkout_rejects_residue_invalid_commit_and_path_escape(
    monkeypatch,
    tmp_path,
):
    commit = "9" * 40
    release_root = tmp_path / "releases"
    release_root.mkdir()
    (release_root / f".{commit}.incoming").mkdir()
    monkeypatch.setattr(
        "tools.release_authority._run",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("Git must not run")),
    )

    for root, requested, expected in (
        (release_root, commit, "interrupted"),
        (tmp_path / "uncreated", "../main; touch owned", "full 40-character"),
        (tmp_path / "releases" / ".." / "escape", commit, "normalized absolute"),
    ):
        try:
            release_authority.materialize_main_checkout(root, requested)
        except ReleaseAuthorityError as exc:
            assert expected in str(exc)
        else:
            raise AssertionError("unsafe release materialization must fail closed")


def test_materialize_main_checkout_rejects_symlink_release_root(monkeypatch, tmp_path):
    commit = "a" * 40
    actual = tmp_path / "actual"
    actual.mkdir()
    linked = tmp_path / "linked"
    try:
        linked.symlink_to(actual, target_is_directory=True)
    except OSError:
        linked = actual
        monkeypatch.setattr(
            "tools.release_authority._is_link_or_junction",
            lambda path: Path(path) == linked,
        )
    monkeypatch.setattr(
        "tools.release_authority._run",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("Git must not run")),
    )

    try:
        release_authority.materialize_main_checkout(linked, commit)
    except ReleaseAuthorityError as exc:
        assert "symlink" in str(exc)
    else:
        raise AssertionError("a symlink release root must fail closed")


def test_deploy_main_commit_keeps_target_provider_selection_for_final_parity(monkeypatch, tmp_path):
    commit = "b" * 40
    checkout = tmp_path / "releases" / commit
    _, release_root, env_file = _prepare_managed_release_layout(monkeypatch, tmp_path)
    checkout = release_root / commit
    calls: list[tuple[str, Path, str, tuple[str, ...]]] = []
    compose_files = (COMPOSE_RELATIVE_PATH, OPENSANDBOX_COMPOSE_RELATIVE_PATH)
    monkeypatch.setattr(
        "tools.release_authority.materialize_main_checkout",
        lambda root, requested: checkout,
    )

    def fake_deploy(repo_root, requested, **kwargs):
        calls.append(("deploy", repo_root, requested, tuple(kwargs["compose_files"])))
        return {"commit": requested}

    def fake_parity(repo_root, requested, **kwargs):
        calls.append(("parity", repo_root, requested, tuple(kwargs["compose_files"])))
        return {"verified": True, "mismatches": []}

    monkeypatch.setattr("tools.release_authority.deploy_clean_commit", fake_deploy)
    monkeypatch.setattr("tools.release_authority.collect_live_parity", fake_parity)

    result = release_authority.deploy_main_commit(
        release_root,
        commit,
        docker_cmd="sudo -n docker",
        env_file=env_file,
        replace_known_manual_frontend=False,
        compose_files=compose_files,
    )

    assert calls == [
        ("deploy", checkout, commit, compose_files),
        ("parity", checkout, commit, compose_files),
    ]
    assert result["checkout"] == str(checkout)
    assert result["parity"]["verified"] is True


def test_deploy_main_commit_fails_closed_when_live_parity_does_not_verify(monkeypatch, tmp_path):
    commit = "c" * 40
    _, release_root, env_file = _prepare_managed_release_layout(monkeypatch, tmp_path)
    checkout = release_root / commit
    monkeypatch.setattr("tools.release_authority.materialize_main_checkout", lambda root, requested: checkout)
    monkeypatch.setattr("tools.release_authority.deploy_clean_commit", lambda *args, **kwargs: {})
    monkeypatch.setattr(
        "tools.release_authority.collect_live_parity",
        lambda *args, **kwargs: {"verified": False, "mismatches": ["api_runtime_commit_mismatch"]},
    )

    try:
        release_authority.deploy_main_commit(
            release_root,
            commit,
            docker_cmd="docker",
            env_file=env_file,
            replace_known_manual_frontend=False,
        )
    except ReleaseAuthorityError as exc:
        assert "api_runtime_commit_mismatch" in str(exc)
    else:
        raise AssertionError("a non-verifying rollout must fail closed")


def test_image_validation_rejects_stale_underscore_compatibility_aliases():
    commit = "d" * 40
    canonical = {
        "ai-platform.source-commit": commit,
        "org.opencontainers.image.revision": commit,
        "ai-platform.source-repository": AUTHORITATIVE_REPOSITORY,
        "ai-platform.build-dirty": "false",
        "ai-platform.release-role": "backend",
    }
    aliases = (
        "ai-platform.source_revision",
        "ai-platform.source_commit",
        "ai-platform.runtime_subject",
        "ai-platform.source_tree_commit",
        "ai_platform_source_revision",
        "ai_platform_source_commit",
        "ai_platform_runtime_subject",
        "ai_platform_source_tree_commit",
    )

    for alias in aliases:
        image = {"labels": {**canonical, alias: "e" * 40}}
        try:
            release_authority._validate_release_image(
                image,
                commit=commit,
                repository=AUTHORITATIVE_REPOSITORY,
                role="backend",
            )
        except ReleaseAuthorityError as exc:
            assert alias in str(exc)
        else:
            raise AssertionError(f"stale compatibility label {alias} must fail closed")


def _published_image_labels(commit: str, role: str) -> dict[str, str]:
    labels = {
        "org.opencontainers.image.revision": commit,
        "ai-platform.source-revision": commit,
        "ai-platform.source-commit": commit,
        "ai-platform.build-dirty": "false",
        "ai-platform.source-repository": AUTHORITATIVE_REPOSITORY,
        "ai-platform.release-role": role,
    }
    if role == "backend":
        labels.update(
            {
                "ai-platform.runtime-subject": commit,
                "ai-platform.source_revision": commit,
                "ai-platform.source_commit": commit,
                "ai-platform.runtime_subject": commit,
                "ai-platform.source_tree_commit": commit,
            }
        )
    return labels


def test_backend_actual_published_compatibility_labels_are_validated():
    commit = "4" * 40
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "LABEL ai-platform.runtime-subject=$AI_PLATFORM_BUILD_COMMIT" in dockerfile
    labels = _published_image_labels(commit, "backend")

    release_authority._validate_release_image(
        {"labels": labels},
        commit=commit,
        repository=AUTHORITATIVE_REPOSITORY,
        role="backend",
    )
    labels["ai-platform.runtime-subject"] = "5" * 40
    try:
        release_authority._validate_release_image(
            {"labels": labels},
            commit=commit,
            repository=AUTHORITATIVE_REPOSITORY,
            role="backend",
        )
    except ReleaseAuthorityError as exc:
        assert "ai-platform.runtime-subject" in str(exc)
    else:
        raise AssertionError("a stale published backend runtime subject must fail closed")


def test_frontend_actual_published_compatibility_labels_are_validated():
    commit = "6" * 40
    dockerfile = (ROOT / "frontend" / "web" / "Dockerfile").read_text(encoding="utf-8")
    assert "LABEL ai-platform.source-revision=$AI_PLATFORM_BUILD_COMMIT" in dockerfile
    labels = _published_image_labels(commit, "frontend")

    release_authority._validate_release_image(
        {"labels": labels},
        commit=commit,
        repository=AUTHORITATIVE_REPOSITORY,
        role="frontend",
    )
    labels["ai-platform.source-revision"] = "7" * 40
    try:
        release_authority._validate_release_image(
            {"labels": labels},
            commit=commit,
            repository=AUTHORITATIVE_REPOSITORY,
            role="frontend",
        )
    except ReleaseAuthorityError as exc:
        assert "ai-platform.source-revision" in str(exc)
    else:
        raise AssertionError("a stale published frontend source revision must fail closed")


def test_parity_report_rejects_stale_published_compatibility_labels():
    commit = "8" * 40
    backend_labels = _published_image_labels(commit, "backend")
    frontend_labels = _published_image_labels(commit, "frontend")
    backend_labels["ai-platform.runtime-subject"] = "9" * 40
    frontend_labels["ai-platform.source-revision"] = "a" * 40

    report = build_parity_report(
        expected_commit=commit,
        source={"commit": commit, "dirty": False},
        images={
            "backend": {"labels": backend_labels},
            "frontend": {"labels": frontend_labels},
        },
        containers={},
        runtime={},
        expected_compose_dir="/srv/ai-platform/releases/commit/deploy/ai-platform",
        expected_repository=AUTHORITATIVE_REPOSITORY,
    )

    assert "backend_image_compatibility_commit_mismatch" in report["mismatches"]
    assert "frontend_image_compatibility_commit_mismatch" in report["mismatches"]


def test_parity_report_records_stale_underscore_compatibility_alias():
    commit = "1" * 40
    report = build_parity_report(
        expected_commit=commit,
        source={"commit": commit, "dirty": False},
        images={
            "backend": {
                "labels": {
                    "ai-platform.source-commit": commit,
                    "org.opencontainers.image.revision": commit,
                    "ai-platform.source-repository": AUTHORITATIVE_REPOSITORY,
                    "ai-platform.build-dirty": "false",
                    "ai-platform.release-role": "backend",
                    "ai-platform.source_revision": "2" * 40,
                }
            },
            "frontend": {"labels": {}},
        },
        containers={},
        runtime={},
        expected_compose_dir="/srv/ai-platform/releases/commit/deploy/ai-platform",
        expected_repository=AUTHORITATIVE_REPOSITORY,
    )

    assert "backend_image_compatibility_commit_mismatch" in report["mismatches"]


def test_release_authority_cli_exposes_git_native_main_commit_deploy():
    help_text = subprocess.run(
        [sys.executable, "tools/release_authority.py", "deploy-main-commit", "--help"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout

    assert "--release-root" in help_text
    assert "--repo-root" not in help_text
    assert "--commit" in help_text
    assert "--compose-file" in help_text
    assert "--strategy" in help_text
    assert "--canonical-build-timeout-seconds SECONDS" in help_text
    assert "default: 1800" in help_text


@pytest.mark.parametrize(
    ("paths", "backend", "frontend", "no_runtime_change"),
    [
        (["pyproject.toml"], ("dependency", "canonical-build"), ("unchanged", "promote"), False),
        (["app/main.py"], ("source", "runtime-rebuild"), ("unchanged", "promote"), False),
        (["frontend/web/pnpm-lock.yaml"], ("unchanged", "promote"), ("dependency", "canonical-build"), False),
        (["frontend/web/src/App.tsx"], ("unchanged", "promote"), ("source", "source-build"), False),
        (["deploy/ai-platform/docker-compose.yml"], ("unchanged", "promote"), ("unchanged", "promote"), True),
    ],
)
def test_auto_strategy_classifies_role_changes_and_no_runtime_plan(
    paths,
    backend,
    frontend,
    no_runtime_change,
):
    plan = release_authority.build_auto_release_plan(
        "1" * 40,
        "2" * 40,
        release_authority.classify_runtime_changes(paths),
    )

    assert [(item.change_kind, item.action) for item in plan.roles] == [backend, frontend]
    assert plan.no_runtime_change is no_runtime_change


def test_dockerfiles_install_dependencies_before_source_and_provenance_layers():
    backend = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    frontend = (ROOT / "frontend" / "web" / "Dockerfile").read_text(encoding="utf-8")

    assert backend.index("COPY pyproject.toml") < backend.index("pip install --no-cache-dir -r")
    assert backend.index("pip install --no-cache-dir -r") < backend.index("COPY app /app/app")
    assert backend.index("COPY app /app/app") < backend.index("LABEL ai-platform.source-commit")
    assert frontend.index("pnpm install --frozen-lockfile") < frontend.index("COPY frontend/web/src")
    assert frontend.index("COPY frontend/web/src") < frontend.index("corepack pnpm run ci:verify")


def _configure_auto_deploy(monkeypatch, tmp_path, *, current, target, target_present=False):
    _write_compose_files(tmp_path)
    commands: list[tuple[list[str], dict]] = []
    built: set[str] = set()
    current_refs = build_image_references(current)
    target_refs = build_image_references(target)

    monkeypatch.setattr("tools.release_authority.assert_clean_commit", lambda repo, requested: target)
    monkeypatch.setattr("tools.release_authority._git", lambda repo, *args: AUTHORITATIVE_REPOSITORY + "\n")

    def fake_image_record(docker, image):
        if image in target_refs.values() and not (target_present or image in built):
            raise subprocess.CalledProcessError(1, [*docker, "image", "inspect", image])
        if image in target_refs.values():
            commit = target
        elif image in current_refs.values():
            commit = current
        else:
            raise AssertionError(f"unexpected image lookup: {image}")
        role = "frontend" if "frontend" in image else "backend"
        return {
            "id": "sha256:frontend" if role == "frontend" else SANDBOX_IMAGE_ID,
            "labels": _published_image_labels(commit, role),
        }

    def fake_run(command, **kwargs):
        command = list(command)
        commands.append((command, kwargs))
        if len(command) >= 3 and command[-3:-1] == ["container", "inspect"]:
            return subprocess.CompletedProcess(command, 1, stdout="", stderr="not found")
        if "build" in command:
            built.add(command[command.index("-t") + 1])
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr("tools.release_authority._image_record", fake_image_record)
    monkeypatch.setattr("tools.release_authority._run", fake_run)
    return commands, current_refs


def test_auto_backend_source_only_uses_runtime_rebuild_without_dependency_commands(monkeypatch, tmp_path):
    current = "1" * 40
    target = "2" * 40
    commands, current_refs = _configure_auto_deploy(
        monkeypatch, tmp_path, current=current, target=target
    )
    plan = release_authority.build_auto_release_plan(
        current,
        target,
        release_authority.classify_runtime_changes(["app/main.py"]),
    )

    deployment = deploy_clean_commit(
        tmp_path,
        target,
        docker_cmd="docker",
        env_file=tmp_path / ".env",
        replace_known_manual_frontend=False,
        strategy="auto",
        auto_plan=plan,
        current_references=current_refs,
    )

    builds = [(command, kwargs) for command, kwargs in commands if "build" in command]
    backend_runtime = next(kwargs["input"] for command, kwargs in builds if "COPY app /app/app" in kwargs.get("input", ""))
    assert not any(token in backend_runtime.lower() for token in ("apt", "pip", "pnpm"))
    assert all("frontend/web/Dockerfile" not in command for command, _ in builds)
    assert deployment["plan"]["roles"][0]["action"] == "runtime-rebuild"
    assert any(event["action"] == "runtime-rebuild" for event in deployment["stages"])


def test_auto_dependency_change_builds_only_the_affected_role(monkeypatch, tmp_path):
    current = "3" * 40
    target = "4" * 40
    commands, current_refs = _configure_auto_deploy(
        monkeypatch, tmp_path, current=current, target=target
    )
    plan = release_authority.build_auto_release_plan(
        current,
        target,
        release_authority.classify_runtime_changes(["pyproject.toml"]),
    )

    deploy_clean_commit(
        tmp_path,
        target,
        docker_cmd="docker",
        env_file=tmp_path / ".env",
        replace_known_manual_frontend=False,
        strategy="auto",
        auto_plan=plan,
        current_references=current_refs,
    )

    builds = [(command, kwargs) for command, kwargs in commands if "build" in command]
    assert any(command[command.index("-f") + 1] == "Dockerfile" for command, _ in builds)
    assert all("frontend/web/Dockerfile" not in command for command, _ in builds)
    promoted_frontend = next(kwargs["input"] for command, kwargs in builds if "frontend" in command[command.index("-t") + 1])
    assert "ai-platform-build-provenance.json" in promoted_frontend


def test_auto_no_runtime_change_promotes_roles_without_role_builds(monkeypatch, tmp_path):
    current = "5" * 40
    target = "6" * 40
    commands, current_refs = _configure_auto_deploy(
        monkeypatch, tmp_path, current=current, target=target
    )
    plan = release_authority.build_auto_release_plan(
        current,
        target,
        release_authority.classify_runtime_changes(["docs/operations/runbook.md"]),
    )

    deployment = deploy_clean_commit(
        tmp_path,
        target,
        docker_cmd="docker",
        env_file=tmp_path / ".env",
        replace_known_manual_frontend=False,
        strategy="auto",
        auto_plan=plan,
        current_references=current_refs,
    )

    builds = [(command, kwargs) for command, kwargs in commands if "build" in command]
    assert all(command[command.index("-f") + 1] == "-" for command, _ in builds)
    assert {event["action"] for event in deployment["stages"] if event["stage"].endswith("-image")} == {"promote"}
    assert deployment["plan"]["no_runtime_change"] is True


def test_auto_promote_rewrites_target_labels_and_embedded_markers():
    backend = release_authority._promotion_dockerfile("backend")
    frontend = release_authority._promotion_dockerfile("frontend")

    for label in (
        "ai-platform.source-commit=$AI_PLATFORM_BUILD_COMMIT",
        "org.opencontainers.image.revision=$AI_PLATFORM_BUILD_COMMIT",
        "ai-platform.source-revision=$AI_PLATFORM_BUILD_COMMIT",
    ):
        assert label in backend
        assert label in frontend
    assert "/app/.ai-platform-source-revision" in backend
    assert "/app/.ai-platform-source-snapshot.json" in backend
    assert "ai-platform-build-provenance.json" in frontend
    assert "${AI_PLATFORM_BUILD_COMMIT}" in frontend


def test_invalid_current_runtime_provenance_fails_before_mutation(monkeypatch, tmp_path):
    current = "7" * 40
    main, _ = _write_compose_files(tmp_path)
    selection = release_authority.resolve_compose_files(tmp_path, [COMPOSE_RELATIVE_PATH])
    commands: list[list[str]] = []
    config_files = _compose_config_value(main)

    def fake_run(command, **kwargs):
        command = list(command)
        commands.append(command)
        role = command[-1].removeprefix("ai-platform-")
        payload = _owned_container_payload(role, main.parent, config_files)
        payload[0]["State"] = {"Running": True}
        payload[0]["Config"]["Labels"].update(
            {
                "ai-platform.source-commit": current,
                "ai-platform.source-dirty": "false",
            }
        )
        return subprocess.CompletedProcess(command, 0, stdout=json.dumps(payload), stderr="")

    monkeypatch.setattr("tools.release_authority._run", fake_run)
    monkeypatch.setattr("tools.release_authority.collect_live_parity", lambda *args, **kwargs: {"verified": False})

    with pytest.raises(ReleaseAuthorityError, match="current runtime provenance is invalid"):
        release_authority._verified_current_runtime(["docker"], selection, docker_cmd="docker")

    assert all("build" not in command and "compose" not in command for command in commands)


def test_timeout_stage_is_bounded_and_redacted(monkeypatch, tmp_path):
    seen: dict[str, object] = {}
    events: list[dict] = []

    def timeout_run(command, **kwargs):
        seen["timeout"] = kwargs["timeout"]
        raise subprocess.TimeoutExpired(
            ["docker", "private-marker"],
            kwargs["timeout"],
            output="private-marker",
            stderr="private-marker",
        )

    monkeypatch.setattr("tools.release_authority._run", timeout_run)
    with pytest.raises(ReleaseAuthorityError, match="^release stage failed: backend-image$") as exc_info:
        release_authority._stage(
            events,
            name="backend-image",
            strategy="auto",
            action="runtime-rebuild",
            operation=lambda: release_authority._canonical_or_source_build(
                ["docker"],
                repo_root=tmp_path,
                reference="ai-platform:" + "8" * 40,
                commit="8" * 40,
                repository=AUTHORITATIVE_REPOSITORY,
                role="backend",
                source_only=True,
            ),
        )

    assert seen["timeout"] == release_authority.BACKEND_STAGE_TIMEOUT_SECONDS
    assert exc_info.value.stage_events[-1]["status"] == "failed"
    assert "private-marker" not in str(exc_info.value)
    formatted = "".join(traceback.format_exception(exc_info.type, exc_info.value, exc_info.tb))
    assert "private-marker" not in formatted
    assert exc_info.value.__cause__ is None
    assert exc_info.value.__context__ is None


@pytest.mark.parametrize(
    ("stderr", "expected"),
    [
        ("", {"stderr_status": "empty"}),
        (
            "BuildKit: failed to solve: no space left on device",
            {"stderr_status": "recognized", "stderr_summary": "no space left on device"},
        ),
        ("authorization=private-marker", {"stderr_status": "redacted"}),
        (
            "authorization=private-marker; no space left on device",
            {"stderr_status": "redacted"},
        ),
    ],
)
def test_canonical_timeout_stage_records_bounded_redacted_diagnostic(stderr, expected):
    events: list[dict] = []

    def fail():
        raise subprocess.TimeoutExpired(
            ["docker", "build", "--secret", "private-marker"],
            1800,
            output="private-marker-output",
            stderr=stderr,
        )

    with pytest.raises(ReleaseAuthorityError) as exc_info:
        release_authority._stage(
            events,
            name="backend-image",
            strategy="auto",
            action="canonical-build",
            timeout_seconds=1800,
            operation=fail,
        )

    event = exc_info.value.stage_events[-1]
    assert event == {
        "stage": "backend-image",
        "strategy": "auto",
        "action": "canonical-build",
        "status": "failed",
        "wall_time_seconds": event["wall_time_seconds"],
        "failure_kind": "timeout",
        "timeout_seconds": 1800,
        **expected,
    }
    serialized = json.dumps(event)
    assert "private-marker" not in serialized
    assert "--secret" not in serialized


@pytest.mark.parametrize(
    ("stderr", "expected"),
    [
        (
            "BuildKit: failed to solve: permission denied",
            {
                "failure_kind": "nonzero-exit",
                "exit_code": 17,
                "stderr_status": "recognized",
                "stderr_summary": "permission denied",
            },
        ),
        (
            "registry token private-marker",
            {
                "failure_kind": "nonzero-exit",
                "exit_code": 17,
                "stderr_status": "redacted",
            },
        ),
    ],
)
def test_canonical_failure_stage_preserves_safe_exit_diagnostic(stderr, expected):
    events: list[dict] = []

    with pytest.raises(ReleaseAuthorityError) as exc_info:
        release_authority._stage(
            events,
            name="backend-image",
            strategy="auto",
            action="canonical-build",
            timeout_seconds=1800,
            operation=lambda: (_ for _ in ()).throw(
                subprocess.CalledProcessError(
                    17,
                    ["docker", "private-marker"],
                    output="private-marker-output",
                    stderr=stderr,
                )
            ),
        )

    event = exc_info.value.stage_events[-1]
    assert {key: event[key] for key in expected} == expected
    assert event["timeout_seconds"] == 1800
    assert "private-marker" not in json.dumps(event)


def test_auto_rerun_reuses_verified_target_images_without_rebuild(monkeypatch, tmp_path):
    target = "9" * 40
    commands, references = _configure_auto_deploy(
        monkeypatch,
        tmp_path,
        current=target,
        target=target,
        target_present=True,
    )
    plan = release_authority.build_auto_release_plan(
        target,
        target,
        release_authority.classify_runtime_changes([]),
    )

    for _ in range(2):
        deploy_clean_commit(
            tmp_path,
            target,
            docker_cmd="docker",
            env_file=tmp_path / ".env",
            replace_known_manual_frontend=False,
            strategy="auto",
            auto_plan=plan,
            current_references=references,
        )

    assert not any("build" in command for command, _ in commands)
    assert sum("compose" in command for command, _ in commands) == 2


def test_legacy_deploy_cli_dispatch_does_not_read_auto_strategy(monkeypatch, capsys, tmp_path):
    observed = {}

    def fake_deploy(repo_root, commit, **kwargs):
        observed["repo_root"] = repo_root
        observed["commit"] = commit
        observed["kwargs"] = kwargs
        return {"commit": commit}

    monkeypatch.setattr("tools.release_authority.deploy_clean_commit", fake_deploy)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "release_authority.py",
            "deploy",
            "--repo-root",
            str(tmp_path),
            "--commit",
            "a" * 40,
            "--env-file",
            str(tmp_path / ".env"),
        ],
    )

    assert release_authority.main() == 0
    assert observed["repo_root"] == tmp_path
    assert observed["commit"] == "a" * 40
    assert "strategy" not in observed["kwargs"]
    assert json.loads(capsys.readouterr().out) == {"commit": "a" * 40}


def test_deploy_main_cli_forwards_explicit_auto_strategy(monkeypatch, capsys, tmp_path):
    observed = {}

    def fake_deploy_main(release_root, commit, **kwargs):
        observed["release_root"] = release_root
        observed["commit"] = commit
        observed["kwargs"] = kwargs
        return {"commit": commit}

    monkeypatch.setattr("tools.release_authority.deploy_main_commit", fake_deploy_main)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "release_authority.py",
            "deploy-main-commit",
            "--release-root",
            str(tmp_path / "releases"),
            "--commit",
            "b" * 40,
            "--strategy",
            "auto",
            "--canonical-build-timeout-seconds",
            "2400",
        ],
    )

    assert release_authority.main() == 0
    assert observed["release_root"] == tmp_path / "releases"
    assert observed["commit"] == "b" * 40
    assert observed["kwargs"]["strategy"] == "auto"
    assert observed["kwargs"]["canonical_dependency_build_timeout_seconds"] == 2400
    assert observed["kwargs"]["env_file"] is None
    assert observed["kwargs"]["coordination_source"] == Path.cwd()
    assert json.loads(capsys.readouterr().out) == {"commit": "b" * 40}


def test_deploy_main_cli_forwards_default_canonical_build_timeout(monkeypatch, capsys, tmp_path):
    observed = {}

    def fake_deploy_main(release_root, commit, **kwargs):
        observed.update(kwargs)
        return {"commit": commit}

    monkeypatch.setattr("tools.release_authority.deploy_main_commit", fake_deploy_main)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "release_authority.py",
            "deploy-main-commit",
            "--release-root",
            str(tmp_path / "releases"),
            "--commit",
            "b" * 40,
        ],
    )

    assert release_authority.main() == 0
    assert observed["canonical_dependency_build_timeout_seconds"] == 1800
    assert json.loads(capsys.readouterr().out) == {"commit": "b" * 40}


def test_backend_runtime_rebuild_clears_current_subjects_before_target_copies():
    dockerfile = release_authority._backend_runtime_dockerfile()

    cleanup = "RUN rm -rf /app/app /app/tools /app/scripts /app/skills /app/docs/release-evidence"
    assert cleanup in dockerfile
    assert dockerfile.index(cleanup) < dockerfile.index("COPY app /app/app")
    assert dockerfile.index(cleanup) < dockerfile.index("COPY tools /app/tools")
    assert dockerfile.index(cleanup) < dockerfile.index("COPY scripts /app/scripts")
    assert dockerfile.index(cleanup) < dockerfile.index("COPY skills /app/skills")
    assert dockerfile.index(cleanup) < dockerfile.index("COPY docs/release-evidence /app/docs/release-evidence")
    assert "/app/docker-entrypoint.sh" in dockerfile.split("COPY app /app/app", 1)[0]
    assert "/app/.ai-platform-source-snapshot.json" in dockerfile.split("COPY app /app/app", 1)[0]
    assert not any(token in dockerfile.lower() for token in ("apt", "pip", "pnpm"))


def _wait_for_owned_test_process_exit(pid: int, *, timeout_seconds: float = 2.0) -> bool:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if os.name == "nt":
            tasklist = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                check=False,
                capture_output=True,
                text=True,
            )
            if str(pid) not in tasklist.stdout:
                return True
            time.sleep(0.05)
            continue
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        except PermissionError:
            return False
        time.sleep(0.05)
    return False


def test_run_timeout_terminates_owned_descendant_and_bounds_pipe_wait(tmp_path):
    pid_file = tmp_path / "owned-descendant.pid"
    child_code = "import time; time.sleep(60)"
    parent_code = (
        "import pathlib, subprocess, sys, time; "
        f"child = subprocess.Popen([sys.executable, '-c', {child_code!r}]); "
        f"pathlib.Path({str(pid_file)!r}).write_text(str(child.pid), encoding='utf-8'); "
        "time.sleep(60)"
    )
    started = time.monotonic()
    child_pid: int | None = None
    try:
        with pytest.raises(subprocess.TimeoutExpired) as exc_info:
            release_authority._run(
                [sys.executable, "-c", parent_code],
                timeout=0.25,
            )
        elapsed = time.monotonic() - started
        assert elapsed < 3.0
        assert exc_info.value.output is None
        assert exc_info.value.stderr is None
        child_pid = int(pid_file.read_text(encoding="utf-8"))
        assert _wait_for_owned_test_process_exit(child_pid)
    finally:
        if child_pid is not None and not _wait_for_owned_test_process_exit(child_pid, timeout_seconds=0.05):
            if os.name == "posix":
                try:
                    os.kill(child_pid, 9)
                except ProcessLookupError:
                    pass
            else:
                subprocess.run(
                    ["taskkill", "/PID", str(child_pid), "/T", "/F"],
                    check=False,
                    capture_output=True,
                    text=True,
                )


@pytest.mark.skipif(os.name != "nt", reason="Windows Job Object regression")
def test_run_timeout_windows_job_kills_pipe_holder_after_direct_parent_exit(monkeypatch, tmp_path):
    parent_pid_file = tmp_path / "exited-parent.pid"
    child_pid_file = tmp_path / "orphaned-pipe-holder.pid"
    child_ready_file = tmp_path / "orphaned-pipe-holder.ready"
    parent_done_file = tmp_path / "exited-parent.done"
    child_code = (
        "import pathlib, time; "
        f"pathlib.Path({str(child_ready_file)!r}).write_text('ready', encoding='utf-8'); "
        "time.sleep(30)"
    )
    parent_code = (
        "import os, pathlib, subprocess, sys, time; "
        f"child = subprocess.Popen([sys.executable, '-c', {child_code!r}]); "
        f"ready = pathlib.Path({str(child_ready_file)!r}); "
        "deadline = time.monotonic() + 2; "
        "exec(\"while not ready.exists() and time.monotonic() < deadline:\\n time.sleep(0.01)\"); "
        "assert ready.exists(); "
        f"pathlib.Path({str(parent_pid_file)!r}).write_text(str(os.getpid()), encoding='utf-8'); "
        f"pathlib.Path({str(child_pid_file)!r}).write_text(str(child.pid), encoding='utf-8'); "
        f"pathlib.Path({str(parent_done_file)!r}).write_text('done', encoding='utf-8')"
    )
    started = time.monotonic()
    child_pid: int | None = None
    parent_returncodes: list[int | None] = []
    original_terminate = release_authority._terminate_owned_process_tree

    def observe_parent_exit(process, **kwargs):
        parent_returncodes.append(process.poll())
        return original_terminate(process, **kwargs)

    monkeypatch.setattr(release_authority, "_terminate_owned_process_tree", observe_parent_exit)
    try:
        with pytest.raises(subprocess.TimeoutExpired):
            release_authority._run(
                [sys.executable, "-c", parent_code],
                timeout=0.5,
            )
        elapsed = time.monotonic() - started
        assert parent_done_file.read_text(encoding="utf-8") == "done"
        parent_pid = int(parent_pid_file.read_text(encoding="utf-8"))
        child_pid = int(child_pid_file.read_text(encoding="utf-8"))
        assert elapsed < 3.0
        assert parent_returncodes[0] == 0
        assert _wait_for_owned_test_process_exit(parent_pid)
        assert _wait_for_owned_test_process_exit(child_pid)
    finally:
        if child_pid is not None and not _wait_for_owned_test_process_exit(child_pid, timeout_seconds=0.05):
            subprocess.run(
                ["taskkill", "/PID", str(child_pid), "/T", "/F"],
                check=False,
                capture_output=True,
                text=True,
            )


@pytest.mark.skipif(os.name != "nt", reason="Windows Job Object regression")
def test_run_success_closes_job_without_killing_detached_descendant(tmp_path):
    child_pid_file = tmp_path / "successful-detached-child.pid"
    child_code = "import time; time.sleep(30)"
    parent_code = (
        "import pathlib, subprocess, sys; "
        f"child = subprocess.Popen([sys.executable, '-c', {child_code!r}], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL); "
        f"pathlib.Path({str(child_pid_file)!r}).write_text(str(child.pid), encoding='utf-8')"
    )
    child_pid: int | None = None
    try:
        result = release_authority._run([sys.executable, "-c", parent_code], timeout=2)
        assert result.returncode == 0
        child_pid = int(child_pid_file.read_text(encoding="utf-8"))
        assert not _wait_for_owned_test_process_exit(child_pid, timeout_seconds=0.2)
    finally:
        if child_pid is not None and not _wait_for_owned_test_process_exit(child_pid, timeout_seconds=0.05):
            subprocess.run(
                ["taskkill", "/PID", str(child_pid), "/T", "/F"],
                check=False,
                capture_output=True,
                text=True,
            )


def test_run_non_timeout_communicate_error_cleans_tree_and_preserves_exception(monkeypatch, tmp_path):
    pid_file = tmp_path / "non-timeout-error-child.pid"
    child_code = (
        "import os, pathlib, time; "
        f"pathlib.Path({str(pid_file)!r}).write_text(str(os.getpid()), encoding='utf-8'); "
        "time.sleep(60)"
    )
    original_communicate = subprocess.Popen.communicate
    failure = TypeError("write() argument must be str, not bytes")
    injected = False

    def communicate_once(process, *args, **kwargs):
        nonlocal injected
        if not injected:
            deadline = time.monotonic() + 2
            while not pid_file.exists() and time.monotonic() < deadline:
                time.sleep(0.01)
            if not pid_file.exists():
                pytest.fail("controlled child did not start before communicate failure")
            injected = True
            raise failure
        return original_communicate(process, *args, **kwargs)

    monkeypatch.setattr(subprocess.Popen, "communicate", communicate_once)
    child_pid: int | None = None
    try:
        with pytest.raises(TypeError) as exc_info:
            release_authority._run(
                [sys.executable, "-c", child_code],
                text=True,
                input="text-input",
                timeout=5,
            )
        assert exc_info.value is failure
        child_pid = int(pid_file.read_text(encoding="utf-8"))
        assert _wait_for_owned_test_process_exit(child_pid)
    finally:
        if child_pid is not None and not _wait_for_owned_test_process_exit(child_pid, timeout_seconds=0.05):
            if os.name == "posix":
                try:
                    os.kill(child_pid, 9)
                except ProcessLookupError:
                    pass
            else:
                subprocess.run(
                    ["taskkill", "/PID", str(child_pid), "/T", "/F"],
                    check=False,
                    capture_output=True,
                    text=True,
                )


def test_run_timeout_redacts_captured_output_from_error():
    environment = os.environ.copy()
    environment["RELEASE_AUTHORITY_TEST_SECRET"] = "private-marker"
    with pytest.raises(subprocess.TimeoutExpired) as exc_info:
        release_authority._run(
            [
                sys.executable,
                "-c",
                "import os, sys, time; secret = os.environ['RELEASE_AUTHORITY_TEST_SECRET']; print(secret); print(secret, file=sys.stderr); time.sleep(60)",
            ],
            env=environment,
            timeout=0.1,
        )

    assert "private-marker" not in str(exc_info.value)
    assert exc_info.value.output is None
    assert exc_info.value.stderr is None


def test_run_timeout_retains_only_classified_stderr_diagnostic():
    with pytest.raises(subprocess.TimeoutExpired) as exc_info:
        release_authority._run(
            [
                sys.executable,
                "-c",
                "import sys, time; print('failed to solve: no space left on device', file=sys.stderr, flush=True); time.sleep(60)",
            ],
            timeout=0.1,
        )

    assert exc_info.value.stderr is None
    assert exc_info.value.output is None
    assert exc_info.value.safe_stderr_diagnostic == {
        "stderr_status": "recognized",
        "stderr_summary": "no space left on device",
    }


def test_run_returns_normal_completed_process_without_terminating_successful_command(monkeypatch):
    monkeypatch.setattr(
        "tools.release_authority._terminate_owned_process_tree",
        lambda *_args, **_kwargs: pytest.fail("successful command must not be terminated"),
    )
    result = release_authority._run(
        [
            sys.executable,
            "-c",
            "import sys; print('complete'); print('diagnostic', file=sys.stderr)",
        ],
        timeout=2,
    )

    assert result.returncode == 0
    assert result.stdout == "complete\n"
    assert result.stderr == "diagnostic\n"


def test_run_preserves_text_binary_environment_cwd_and_check_contract(tmp_path):
    environment = os.environ.copy()
    environment["RELEASE_AUTHORITY_CONTRACT_VALUE"] = "contract-value"
    text_result = release_authority._run(
        [
            sys.executable,
            "-c",
            "import json, os, pathlib, sys; print(json.dumps({'cwd': str(pathlib.Path.cwd()), 'env': os.environ['RELEASE_AUTHORITY_CONTRACT_VALUE'], 'input': sys.stdin.read()}))",
        ],
        cwd=tmp_path,
        env=environment,
        input="text-input",
    )
    assert json.loads(text_result.stdout) == {
        "cwd": str(tmp_path),
        "env": "contract-value",
        "input": "text-input",
    }

    binary_payload = b"\x00binary-input\xff"
    binary_result = release_authority._run(
        [sys.executable, "-c", "import sys; sys.stdout.buffer.write(sys.stdin.buffer.read())"],
        text=False,
        input=binary_payload,
    )
    assert binary_result.stdout == binary_payload
    assert binary_result.stderr == b""

    unchecked = release_authority._run(
        [sys.executable, "-c", "import sys; print('failed-output'); sys.exit(7)"],
        check=False,
    )
    assert unchecked.returncode == 7
    assert unchecked.stdout == "failed-output\n"
    with pytest.raises(subprocess.CalledProcessError) as exc_info:
        release_authority._run(
            [sys.executable, "-c", "import sys; print('failed-error', file=sys.stderr); sys.exit(8)"],
        )
    assert exc_info.value.returncode == 8
    assert exc_info.value.stderr == "failed-error\n"


@pytest.mark.parametrize("invalid_input", [b"bytes", bytearray(b"bytearray"), memoryview(b"memoryview")])
def test_run_rejects_bytes_like_text_input_before_popen(monkeypatch, invalid_input):
    popen_calls = 0

    def fail_popen(*args, **kwargs):
        nonlocal popen_calls
        popen_calls += 1
        pytest.fail("Popen must not run for invalid text-mode input")

    monkeypatch.setattr(subprocess, "Popen", fail_popen)
    with pytest.raises(TypeError) as exc_info:
        release_authority._run(
            ["private-command", "private-argument"],
            text=True,
            input=invalid_input,
        )

    assert type(exc_info.value) is TypeError
    assert str(exc_info.value) == "text mode input must be str, not bytes-like"
    assert popen_calls == 0


def test_role_timeouts_distinguish_canonical_dependency_from_source_only(monkeypatch, tmp_path):
    observed: list[int] = []

    def fake_run(command, **kwargs):
        observed.append(kwargs["timeout"])
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr("tools.release_authority._run", fake_run)
    common = {
        "docker": ["docker"],
        "repo_root": tmp_path,
        "reference": "ai-platform:" + "a" * 40,
        "commit": "a" * 40,
        "repository": AUTHORITATIVE_REPOSITORY,
    }
    release_authority._canonical_or_source_build(
        **common,
        role="backend",
        source_only=False,
        canonical_dependency_build_timeout_seconds=2400,
    )
    release_authority._canonical_or_source_build(
        **common,
        role="backend",
        source_only=True,
    )
    release_authority._canonical_or_source_build(
        **common,
        role="frontend",
        source_only=True,
    )
    release_authority._build_from_verified_role_image(
        **common,
        base_reference="ai-platform:" + "b" * 40,
        role="backend",
        dockerfile="FROM scratch\n",
    )

    assert observed == [
        2400,
        release_authority.BACKEND_STAGE_TIMEOUT_SECONDS,
        release_authority.FRONTEND_STAGE_TIMEOUT_SECONDS,
        release_authority.BACKEND_STAGE_TIMEOUT_SECONDS,
    ]


@pytest.mark.parametrize(
    ("value", "accepted"),
    [
        (release_authority.MIN_CANONICAL_DEPENDENCY_BUILD_TIMEOUT_SECONDS, True),
        (release_authority.MAX_CANONICAL_DEPENDENCY_BUILD_TIMEOUT_SECONDS, True),
        (release_authority.MIN_CANONICAL_DEPENDENCY_BUILD_TIMEOUT_SECONDS - 1, False),
        (release_authority.MAX_CANONICAL_DEPENDENCY_BUILD_TIMEOUT_SECONDS + 1, False),
    ],
)
def test_canonical_build_timeout_range_is_finite_and_fail_closed(value, accepted):
    if accepted:
        assert release_authority._validate_canonical_dependency_build_timeout(value) == value
    else:
        with pytest.raises(ReleaseAuthorityError, match="between 300 and 3600 seconds"):
            release_authority._validate_canonical_dependency_build_timeout(value)


@pytest.mark.parametrize("value", ["299", "3601", "inf", "1.5"])
def test_deploy_main_cli_rejects_invalid_canonical_build_timeout(value):
    result = subprocess.run(
        [
            sys.executable,
            "tools/release_authority.py",
            "deploy-main-commit",
            "--release-root",
            "managed/releases",
            "--commit",
            "a" * 40,
            "--canonical-build-timeout-seconds",
            value,
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 2
    assert "canonical-build-timeout-seconds" in result.stderr
    assert result.stdout == ""


def test_canonical_build_timeout_default_and_override_are_recorded_in_plan_and_stage(
    monkeypatch,
    tmp_path,
):
    current = "c" * 40
    target = "d" * 40
    commands, current_refs = _configure_auto_deploy(
        monkeypatch,
        tmp_path,
        current=current,
        target=target,
    )
    plan = release_authority.build_auto_release_plan(
        current,
        target,
        release_authority.classify_runtime_changes(["pyproject.toml"]),
    )

    deployment = deploy_clean_commit(
        tmp_path,
        target,
        docker_cmd="docker",
        env_file=tmp_path / ".env",
        replace_known_manual_frontend=False,
        strategy="auto",
        auto_plan=plan,
        current_references=current_refs,
        canonical_dependency_build_timeout_seconds=2400,
    )

    backend_build = next(
        kwargs
        for command, kwargs in commands
        if "build" in command and command[command.index("-f") + 1] == "Dockerfile"
    )
    backend_stage = next(
        event
        for event in deployment["stages"]
        if event["stage"] == "backend-image" and event["action"] == "canonical-build"
    )
    assert backend_build["timeout"] == 2400
    assert backend_stage["timeout_seconds"] == 2400
    assert deployment["plan"]["canonical_dependency_build_timeout_seconds"] == 2400
    assert (
        release_authority._plan_as_dict(plan)["canonical_dependency_build_timeout_seconds"]
        == release_authority.CANONICAL_DEPENDENCY_BUILD_TIMEOUT_SECONDS
    )
