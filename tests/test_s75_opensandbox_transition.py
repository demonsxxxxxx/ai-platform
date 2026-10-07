from __future__ import annotations

import hashlib
import ipaddress
import json
import stat
import subprocess
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

import tools.production_bootstrap as production_bootstrap
import tools.release_authority as release_authority
import tools.s75_opensandbox_transition as transition


ROOT = Path(__file__).resolve().parents[1]
COMPOSE_DIR = ROOT / "deploy" / "ai-platform"
COMMIT = "a" * 40
DEFAULT_TOPOLOGY = release_authority.DIRECT_OPENSANDBOX_DEFAULT_TOPOLOGY
CUSTOM_TOPOLOGY = release_authority.validate_direct_opensandbox_topology(
    "ai-platform-osb-custom",
    "br-osb-custom",
    "172.30.240.0/27",
    "172.30.240.2",
)
APPLICATION_API_KEY = "test-opensandbox-api-key"


def _fake_host_config(
    *,
    topology=DEFAULT_TOPOLOGY,
    workspace_root=Path("/srv/ai-platform/configured-workspaces"),
):
    return SimpleNamespace(
        lifecycle_address="10.56.1.75",
        topology=topology,
        workspace_root=Path(workspace_root),
        api_key_sha256=hashlib.sha256(APPLICATION_API_KEY.encode("utf-8")).hexdigest(),
    )


def _completed(command=(), returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(command, returncode, stdout=stdout, stderr=stderr)


def _selection(root: Path, names: tuple[str, ...]):
    paths = tuple(root / name for name in names)
    for path in paths:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("services: {}\n", encoding="utf-8")
    return SimpleNamespace(
        checkout_root=root,
        relative_paths=names,
        absolute_paths=paths,
        working_dir=paths[0].parent,
    )


def _legacy_containers(
    commit=COMMIT,
    runtime_root: Path | None = None,
    workspace_root: Path | str = transition.LEGACY_WORKSPACE_ROOT,
):
    workspace_root = str(workspace_root)
    workspace_binds = {
        "workspace-init": ("/runtime-workspaces", workspace_root),
        "api": (workspace_root, workspace_root),
        "worker": (workspace_root, workspace_root),
    }
    runtime_root = runtime_root or transition.LEGACY_RUNTIME_RELEASE_ROOT / commit
    config_files = ",".join(
        str(runtime_root / path) for path in transition.LEGACY_SELECTION
    )
    working_dir = str((runtime_root / transition.LEGACY_SELECTION[0]).parent)
    containers = {}
    for service, name in transition.CONTAINERS.items():
        labels = {
            "com.docker.compose.project": transition.LEGACY_PROJECT,
            "com.docker.compose.service": service,
            "com.docker.compose.project.config_files": config_files,
            "com.docker.compose.project.working_dir": working_dir,
        }
        if service in {"api", "worker", "frontend"}:
            labels.update(
                {
                    "ai-platform.source-commit": commit,
                    "ai-platform.source-dirty": "false",
                }
            )
        mounts = []
        for _, (owner, destination, volume) in transition.EXPECTED_VOLUMES.items():
            if owner == service:
                mounts.append({"Type": "volume", "Name": volume, "Destination": destination})
        if service == "api":
            mounts.append(
                {
                    "Type": "volume",
                    "Name": transition.EXPECTED_VOLUMES["ai_platform_sandbox_workspaces"][2],
                    "Destination": "/tmp/ai-platform-sandbox-workspaces",
                }
            )
        if service in workspace_binds:
            destination, source = workspace_binds[service]
            mounts.append(
                {
                    "Type": "bind",
                    "Source": source,
                    "Destination": destination,
                }
            )
        containers[service] = {
            "Config": {
                "Labels": labels,
                "Image": "ai-platform-frontend:old" if service == "frontend" else "ai-platform:old",
                "Env": ["SANDBOX_EXECUTOR_IMAGE=ai-platform:old"]
                + (
                    [f"SANDBOX_WORKSPACE_ROOT={workspace_root}"]
                    if service in {"api", "worker"}
                    else []
                ),
            },
            "Mounts": mounts,
        }
    return containers


def _legacy_runtime(tmp_path: Path):
    selection = _selection(tmp_path, transition.LEGACY_SELECTION)
    return transition.LegacyRuntime(
        repo_root=tmp_path,
        compose_files=selection.absolute_paths,
        commit=COMMIT,
        backend_image="ai-platform:old",
        frontend_image="ai-platform-frontend:old",
        executor_image="ai-platform:old",
    )


def test_s75_target_reuses_the_legacy_compose_project_and_named_volumes():
    assert transition.LEGACY_PROJECT == release_authority.COMPOSE_PROJECT
    assert transition.TARGET_SELECTION == release_authority.DIRECT_OPENSANDBOX_SELECTION
    assert not (COMPOSE_DIR / "docker-compose.s75-migration.yml").exists()
    selection = release_authority.resolve_compose_files(ROOT, transition.TARGET_SELECTION)
    assert selection.relative_paths == transition.TARGET_SELECTION


def test_prepare_packaged_release_images_pulls_verifies_and_tags(monkeypatch):
    backend = "ghcr.io/example/backend@sha256:" + "1" * 64
    frontend = "ghcr.io/example/frontend@sha256:" + "2" * 64
    targets = {"backend": f"ai-platform:{COMMIT}", "frontend": f"ai-platform-frontend:{COMMIT}"}
    commands = []

    monkeypatch.setattr(release_authority, "_run", lambda command, **kwargs: commands.append(command) or _completed(command))
    monkeypatch.setattr(release_authority, "build_image_references", lambda commit: targets)
    monkeypatch.setattr(
        release_authority,
        "_image_record",
        lambda docker, image: {
            "reference": image,
            "id": "sha256:image-id",
            "labels": {
                "ai-platform.source-commit": COMMIT,
                "org.opencontainers.image.revision": COMMIT,
                "ai-platform.source-repository": release_authority.AUTHORITATIVE_REPOSITORY,
                "ai-platform.build-dirty": "false",
                "ai-platform.release-role": "frontend" if "frontend" in image else "backend",
            },
        },
    )

    assert release_authority.prepare_packaged_release_images(
        COMMIT,
        backend_image=backend,
        frontend_image=frontend,
    ) == targets
    assert commands == [
        ["docker", "pull", backend],
        ["docker", "tag", backend, targets["backend"]],
        ["docker", "pull", frontend],
        ["docker", "tag", frontend, targets["frontend"]],
    ]


def test_prepare_packaged_release_images_rejects_mutable_reference(monkeypatch):
    monkeypatch.setattr(release_authority, "_run", lambda *args, **kwargs: pytest.fail("mutable input must fail before Docker"))

    with pytest.raises(release_authority.ReleaseAuthorityError, match="not immutable"):
        release_authority.prepare_packaged_release_images(
            COMMIT,
            backend_image="ghcr.io/example/backend:latest",
            frontend_image="ghcr.io/example/frontend@sha256:" + "2" * 64,
        )


def test_legacy_runtime_binds_compose_provenance_and_volume_identity(monkeypatch, tmp_path):
    selection = _selection(tmp_path, transition.LEGACY_SELECTION)
    containers = _legacy_containers()

    monkeypatch.setattr(transition, "_assert_root_owned_checkout", lambda root, commit: COMMIT)
    monkeypatch.setattr(release_authority, "resolve_compose_files", lambda root, names: selection)
    monkeypatch.setattr(transition, "_inspect_container", lambda docker, name: containers[next(service for service, expected in transition.CONTAINERS.items() if expected == name)])

    def docker_json(docker, *args):
        assert args[:2] == ("volume", "inspect")
        name = args[2]
        logical = next(logical for logical, (_, _, expected) in transition.EXPECTED_VOLUMES.items() if expected == name)
        return [{"Labels": {"com.docker.compose.project": transition.LEGACY_PROJECT, "com.docker.compose.volume": logical}}]

    monkeypatch.setattr(transition, "_docker_json", docker_json)
    volume_consumers = {
        logical: set(expected)
        for logical, expected in transition.EXPECTED_VOLUME_CONSUMERS.items()
    }
    workspace_consumers = volume_consumers["ai_platform_sandbox_workspaces"]

    def run(command, **kwargs):
        if "com.docker.compose.project=" in " ".join(command):
            return _completed(command, stdout="\n".join(
                f"{name}|{service}" for service, name in transition.CONTAINERS.items()
            ))
        volume = next((part.split("=", 1)[1] for part in command if part.startswith("volume=")), None)
        if volume:
            logical = next(
                logical for logical, (_, _, expected) in transition.EXPECTED_VOLUMES.items()
                if expected == volume
            )
            return _completed(command, stdout="\n".join(sorted(volume_consumers[logical])))
        raise AssertionError(command)

    monkeypatch.setattr(transition, "_run", run)

    runtime = transition._legacy_runtime(["docker"], tmp_path, COMMIT)
    assert runtime.repo_root == tmp_path.resolve()
    assert runtime.compose_files == selection.absolute_paths
    assert runtime.commit == COMMIT
    assert runtime.backend_image == "ai-platform:old"
    assert runtime.frontend_image == "ai-platform-frontend:old"
    assert runtime.executor_image == "ai-platform:old"

    containers = _legacy_containers(runtime_root=tmp_path)
    assert transition._legacy_runtime(["docker"], tmp_path, COMMIT).repo_root == tmp_path.resolve()
    containers["api"]["Config"]["Labels"] = _legacy_containers()["api"]["Config"]["Labels"]
    with pytest.raises(transition.TransitionError, match="legacy Compose ownership mismatch"):
        transition._legacy_runtime(["docker"], tmp_path, COMMIT)

    containers = _legacy_containers()
    api_labels = containers["api"]["Config"]["Labels"]
    for label, invalid in (
        (
            "com.docker.compose.project.config_files",
            api_labels["com.docker.compose.project.config_files"].replace(
                str(transition.LEGACY_RUNTIME_RELEASE_ROOT), "/wrong-release-root"
            ),
        ),
        (
            "com.docker.compose.project.config_files",
            api_labels["com.docker.compose.project.config_files"].replace(COMMIT, "b" * 40),
        ),
        (
            "com.docker.compose.project.config_files",
            api_labels["com.docker.compose.project.config_files"] + ",/unexpected.yml",
        ),
        (
            "com.docker.compose.project.working_dir",
            "/wrong-release-root/deploy/ai-platform",
        ),
    ):
        original = api_labels[label]
        api_labels[label] = invalid
        with pytest.raises(transition.TransitionError, match="legacy Compose ownership mismatch"):
            transition._legacy_runtime(["docker"], tmp_path, COMMIT)
        api_labels[label] = original

    workspace_consumers.add(transition.CONTAINERS["workspace-init"])
    with pytest.raises(transition.TransitionError, match="volume consumer mismatch"):
        transition._legacy_runtime(["docker"], tmp_path, COMMIT)
    workspace_consumers.remove(transition.CONTAINERS["workspace-init"])

    workspace_consumers.remove(transition.CONTAINERS["worker"])
    with pytest.raises(transition.TransitionError, match="volume consumer mismatch"):
        transition._legacy_runtime(["docker"], tmp_path, COMMIT)
    workspace_consumers.add(transition.CONTAINERS["worker"])

    workspace_init_mounts = containers["workspace-init"]["Mounts"]
    workspace_init_bind = workspace_init_mounts[0]
    for field, invalid in (
        ("Type", "volume"),
        ("Destination", "/wrong-workspace"),
        ("Source", "/wrong-workspace"),
    ):
        original = workspace_init_bind[field]
        workspace_init_bind[field] = invalid
        with pytest.raises(transition.TransitionError, match="managed .*bind.*mismatch"):
            transition._legacy_runtime(["docker"], tmp_path, COMMIT)
        workspace_init_bind[field] = original
    workspace_init_mounts.clear()
    with pytest.raises(transition.TransitionError, match="managed bind mount mismatch"):
        transition._legacy_runtime(["docker"], tmp_path, COMMIT)
    workspace_init_mounts.append(workspace_init_bind)

    containers["api"]["Config"]["Env"][-1] = "SANDBOX_WORKSPACE_ROOT=/wrong-workspace"
    with pytest.raises(transition.TransitionError, match="managed workspace root mismatch"):
        transition._legacy_runtime(["docker"], tmp_path, COMMIT)
    containers["api"]["Config"]["Env"][-1] = (
        f"SANDBOX_WORKSPACE_ROOT={transition.LEGACY_WORKSPACE_ROOT}"
    )

    containers["postgres"]["Mounts"][0]["Name"] = "wrong-volume"
    with pytest.raises(transition.TransitionError, match="volume identity mismatch"):
        transition._legacy_runtime(["docker"], tmp_path, COMMIT)


def test_target_workspace_root_keeps_named_data_volume_identity_checks(monkeypatch):
    workspace_root = Path("/srv/ai-platform/custom-workspaces")
    containers = _legacy_containers(workspace_root=workspace_root)

    def docker_json(docker, *args):
        assert args[:2] == ("volume", "inspect")
        name = args[2]
        logical = next(
            key
            for key, (_, _, expected_name) in transition.EXPECTED_VOLUMES.items()
            if expected_name == name
        )
        return [{
            "Labels": {
                "com.docker.compose.project": transition.LEGACY_PROJECT,
                "com.docker.compose.volume": logical,
            }
        }]

    def run(command, **kwargs):
        volume = next(
            (part.split("=", 1)[1] for part in command if part.startswith("volume=")),
            None,
        )
        if volume is None:
            raise AssertionError(command)
        logical = next(
            key
            for key, (_, _, expected_name) in transition.EXPECTED_VOLUMES.items()
            if expected_name == volume
        )
        return _completed(
            command,
            stdout="\n".join(sorted(transition.EXPECTED_VOLUME_CONSUMERS[logical])),
        )

    monkeypatch.setattr(transition, "_docker_json", docker_json)
    monkeypatch.setattr(transition, "_run", run)
    transition._require_volume_identities(
        ["docker"], containers, workspace_root=workspace_root
    )

    containers["postgres"]["Mounts"][0]["Name"] = "wrong-volume"
    with pytest.raises(transition.TransitionError, match="volume identity mismatch"):
        transition._require_volume_identities(
            ["docker"], containers, workspace_root=workspace_root
        )


def test_legacy_rollback_authority_requires_root_owner(monkeypatch, tmp_path):
    monkeypatch.setattr(
        release_authority,
        "assert_managed_target_checkout",
        lambda root, commit, release_root: COMMIT,
    )
    monkeypatch.setattr(Path, "stat", lambda self, **kwargs: SimpleNamespace(st_uid=1001))
    monkeypatch.setattr(
        release_authority,
        "resolve_compose_files",
        lambda *args: pytest.fail("non-root checkout must fail before Compose resolution"),
    )

    for invocation in (
        lambda: transition._legacy_runtime(["docker"], tmp_path, COMMIT),
        lambda: transition._validated_rollback_runtime(
            ["docker"],
            legacy_repo_root=tmp_path,
            legacy_commit=COMMIT,
            backend_image="backend",
            frontend_image="frontend",
            executor_image="executor",
        ),
    ):
        with pytest.raises(transition.TransitionError, match="must be root-owned"):
            invocation()


def test_schema_compatibility_requires_identical_authoritative_objects(monkeypatch, tmp_path):
    calls = []

    def matching(command, **kwargs):
        calls.append(command[-1])
        return _completed(command, stdout=f"{command[-1].split(':', 1)[1]}-object\n")

    monkeypatch.setattr(transition, "_run", matching)
    transition._require_schema_compatibility(tmp_path, "1" * 40, "2" * 40)
    assert calls == [
        f"{'1' * 40}:app/schema.sql",
        f"{'2' * 40}:app/schema.sql",
        f"{'1' * 40}:app/schema_migrations.py",
        f"{'2' * 40}:app/schema_migrations.py",
    ]

    outputs = iter(("same\n", "different\n"))
    monkeypatch.setattr(
        transition,
        "_run",
        lambda command, **kwargs: _completed(command, stdout=next(outputs)),
    )
    with pytest.raises(transition.TransitionError, match="schema is not legacy-rollback compatible"):
        transition._require_schema_compatibility(tmp_path, "1" * 40, "2" * 40)


def test_quiescence_requires_terminal_database_state_and_no_sandbox_containers(monkeypatch):
    calls = []

    def clean_run(command, **kwargs):
        calls.append(command)
        if "exec" in command:
            return _completed(command, stdout="0|0|0\n")
        return _completed(command, stdout="")

    monkeypatch.setattr(transition, "_run", clean_run)
    transition._require_quiescent(["docker"])
    database_command = next(command for command in calls if "exec" in command)
    assert "from runs where status not in" in database_command[-1]
    assert "from run_attempts where status not in" in database_command[-1]
    assert "from sandbox_leases where status <> 'released'" in database_command[-1]
    assert [command[-1] for command in calls if "--filter" in command] == [
        "label=ai-platform.owner=sandbox-runtime",
        "label=ai-platform.owner=sandbox-native-tool",
    ]

    monkeypatch.setattr(transition, "_quiescence_counts", lambda docker: (1, 0, 0))
    with pytest.raises(transition.TransitionError, match="active run"):
        transition._require_quiescent(["docker"])

    monkeypatch.setattr(transition, "_quiescence_counts", lambda docker: (0, 0, 0))
    monkeypatch.setattr(transition, "_run", lambda command, **kwargs: _completed(command, stdout="sandbox-id\n"))
    with pytest.raises(transition.TransitionError, match="sandbox container"):
        transition._require_quiescent(["docker"])


def _stub_migration(
    monkeypatch,
    tmp_path,
    *,
    deploy_error=None,
    down_error=None,
    second_quiescence_error=None,
    parity_error=None,
):
    runtime = _legacy_runtime(tmp_path / "legacy")
    target_selection = _selection(tmp_path / "target", transition.TARGET_SELECTION)
    host_config = _fake_host_config()
    events = []
    quiescence_calls = 0

    monkeypatch.setattr(transition.os, "name", "posix")
    monkeypatch.setattr(transition.os, "geteuid", lambda: 0, raising=False)
    monkeypatch.setattr(transition, "_require_safe_env_file", lambda path: path)
    monkeypatch.setattr(transition, "_load_opensandbox_host_config", lambda: host_config)
    monkeypatch.setattr(
        transition,
        "_require_workspace_root_env",
        lambda path, config=None: config is host_config or pytest.fail("host config was not passed"),
    )
    monkeypatch.setattr(transition, "_legacy_runtime", lambda *args: runtime)
    monkeypatch.setattr(
        transition,
        "_require_host_prerequisites",
        lambda repo_root, docker, config=None: (
            config is host_config or pytest.fail("host prerequisites missed host config"),
            events.append("host"),
        ),
    )

    def quiescent(docker):
        nonlocal quiescence_calls
        quiescence_calls += 1
        events.append(f"quiescent-{quiescence_calls}")
        if quiescence_calls == 2 and second_quiescence_error is not None:
            raise second_quiescence_error

    monkeypatch.setattr(transition, "_require_quiescent", quiescent)
    monkeypatch.setattr(release_authority, "assert_managed_target_checkout", lambda root, commit, release_root: COMMIT)
    monkeypatch.setattr(transition, "_require_schema_compatibility", lambda *args: events.append("schema-compatible"))
    monkeypatch.setattr(release_authority, "resolve_compose_files", lambda root, names: target_selection)
    monkeypatch.setattr(release_authority, "prepare_packaged_release_images", lambda *args, **kwargs: events.append("images"))
    monkeypatch.setattr(release_authority, "_semantic_compose_config_preflight", lambda *args, **kwargs: events.append("compose-preflight"))
    monkeypatch.setattr(transition, "_stop_admission", lambda docker: events.append("stop-admission"))
    monkeypatch.setattr(transition, "_restore_admission", lambda docker: events.append("restore-admission"))
    def down(*args, **kwargs):
        assert kwargs["workspace_root"] == transition.LEGACY_WORKSPACE_ROOT
        events.append("down-legacy")
        if down_error is not None:
            raise down_error

    monkeypatch.setattr(transition, "_down", down)

    def deploy(*args, **kwargs):
        assert transition.os.environ["SANDBOX_WORKSPACE_ROOT"] == str(host_config.workspace_root)
        events.append("deploy-target")
        assert kwargs["replace_known_manual_frontend"] is False
        if deploy_error is not None:
            raise deploy_error

    monkeypatch.setattr(release_authority, "deploy_clean_commit", deploy)
    def target_runtime(*args, **kwargs):
        assert transition.os.environ["SANDBOX_WORKSPACE_ROOT"] == str(host_config.workspace_root)
        assert kwargs["host_config"] is host_config
        events.append("target-runtime")
        if parity_error is not None:
            raise parity_error
        return COMMIT, target_selection.absolute_paths

    monkeypatch.setattr(transition, "_require_target_runtime", target_runtime)
    def rollback(*args, **kwargs):
        assert kwargs["workspace_root"] == host_config.workspace_root
        events.append("rollback")

    monkeypatch.setattr(transition, "_rollback", rollback)
    return events


def test_migration_prepares_before_downtime_and_rechecks_after_stopping_admission(monkeypatch, tmp_path):
    events = _stub_migration(monkeypatch, tmp_path)

    result = transition._migrate_locked(
        target_repo_root=tmp_path / "target",
        target_commit=COMMIT,
        legacy_repo_root=tmp_path / "legacy",
        legacy_commit=COMMIT,
        env_file=tmp_path / ".env",
        backend_image="ghcr.io/example/backend@sha256:" + "1" * 64,
        frontend_image="ghcr.io/example/frontend@sha256:" + "2" * 64,
        docker_cmd="docker",
    )

    assert result["status"] == "migrated_acceptance_pending"
    assert events == [
        "host",
        "quiescent-1",
        "schema-compatible",
        "images",
        "compose-preflight",
        "stop-admission",
        "quiescent-2",
        "down-legacy",
        "deploy-target",
        "target-runtime",
    ]


def test_migration_rejects_inherited_topology_drift_before_stopping_admission(monkeypatch, tmp_path):
    validate_environment = transition._require_workspace_root_env
    events = _stub_migration(monkeypatch, tmp_path)
    config = transition._load_opensandbox_host_config()
    values = {
        "OPENSANDBOX_BASE_URL": f"http://{config.lifecycle_address}:8080",
        "OPENSANDBOX_API_KEY": APPLICATION_API_KEY,
        "OPENSANDBOX_EXPECTED_NETWORK_MODE": config.topology.network_name,
        "OPENSANDBOX_EGRESS_BRIDGE": config.topology.bridge_name,
        "OPENSANDBOX_EGRESS_SUBNET": config.topology.subnet,
        "OPENSANDBOX_EGRESS_PROXY_IPV4": config.topology.proxy_ipv4,
        "SANDBOX_WORKSPACE_ROOT": str(config.workspace_root),
    }
    env_file = tmp_path / "managed.env"
    env_file.write_text("".join(f"{key}={value}\n" for key, value in values.items()))
    for key in values:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("OPENSANDBOX_EGRESS_BRIDGE", "br-osb-other")
    monkeypatch.setattr(transition, "_require_workspace_root_env", validate_environment)
    monkeypatch.setattr(production_bootstrap, "_read_secure_text", lambda path, **kwargs: path.read_text())

    with pytest.raises(transition.TransitionError, match="environment override mismatch"):
        transition._migrate_locked(
            target_repo_root=tmp_path / "target", target_commit=COMMIT,
            legacy_repo_root=tmp_path / "legacy", legacy_commit=COMMIT,
            env_file=env_file, backend_image="backend:target", frontend_image="frontend:target",
            docker_cmd="docker",
        )
    assert "stop-admission" not in events
    assert "down-legacy" not in events
    assert "deploy-target" not in events


def test_migration_restores_admission_when_final_quiescence_fails(monkeypatch, tmp_path):
    events = _stub_migration(
        monkeypatch,
        tmp_path,
        second_quiescence_error=transition.TransitionError("active run"),
    )

    with pytest.raises(transition.TransitionError, match="active run"):
        transition._migrate_locked(
            target_repo_root=tmp_path / "target",
            target_commit=COMMIT,
            legacy_repo_root=tmp_path / "legacy",
            legacy_commit=COMMIT,
            env_file=tmp_path / ".env",
            backend_image="ghcr.io/example/backend@sha256:" + "1" * 64,
            frontend_image="ghcr.io/example/frontend@sha256:" + "2" * 64,
            docker_cmd="docker",
        )
    assert events[-1] == "restore-admission"
    assert "down-legacy" not in events


def test_migration_rolls_back_legacy_project_when_target_deploy_fails(monkeypatch, tmp_path):
    events = _stub_migration(
        monkeypatch,
        tmp_path,
        deploy_error=release_authority.ReleaseAuthorityError("target failed"),
    )

    with pytest.raises(transition.TransitionError, match="legacy runtime restored"):
        transition._migrate_locked(
            target_repo_root=tmp_path / "target",
            target_commit=COMMIT,
            legacy_repo_root=tmp_path / "legacy",
            legacy_commit=COMMIT,
            env_file=tmp_path / ".env",
            backend_image="ghcr.io/example/backend@sha256:" + "1" * 64,
            frontend_image="ghcr.io/example/frontend@sha256:" + "2" * 64,
            docker_cmd="docker",
        )
    assert events[-3:] == ["down-legacy", "deploy-target", "rollback"]


def test_migration_rolls_back_when_target_parity_fails(monkeypatch, tmp_path):
    events = _stub_migration(
        monkeypatch,
        tmp_path,
        parity_error=transition.TransitionError("target parity failed"),
    )

    with pytest.raises(transition.TransitionError, match="legacy runtime restored"):
        transition._migrate_locked(
            target_repo_root=tmp_path / "target",
            target_commit=COMMIT,
            legacy_repo_root=tmp_path / "legacy",
            legacy_commit=COMMIT,
            env_file=tmp_path / ".env",
            backend_image="ghcr.io/example/backend@sha256:" + "1" * 64,
            frontend_image="ghcr.io/example/frontend@sha256:" + "2" * 64,
            docker_cmd="docker",
        )
    assert events[-2:] == ["target-runtime", "rollback"]


def test_migration_rolls_back_after_partial_legacy_down_failure(monkeypatch, tmp_path):
    events = _stub_migration(
        monkeypatch,
        tmp_path,
        down_error=transition.TransitionError("legacy down failed"),
    )

    with pytest.raises(transition.TransitionError, match="legacy runtime restored"):
        transition._migrate_locked(
            target_repo_root=tmp_path / "target",
            target_commit=COMMIT,
            legacy_repo_root=tmp_path / "legacy",
            legacy_commit=COMMIT,
            env_file=tmp_path / ".env",
            backend_image="ghcr.io/example/backend@sha256:" + "1" * 64,
            frontend_image="ghcr.io/example/frontend@sha256:" + "2" * 64,
            docker_cmd="docker",
        )
    assert events[-2:] == ["down-legacy", "rollback"]


def test_finalize_releases_loopback_admission_only_after_acceptance(monkeypatch, tmp_path):
    events = []
    host_config = _fake_host_config()

    @contextmanager
    def unlocked():
        yield

    monkeypatch.setattr(transition.os, "name", "posix")
    monkeypatch.setattr(transition.os, "geteuid", lambda: 0, raising=False)
    monkeypatch.setattr(transition, "_transition_lock", unlocked)
    monkeypatch.setattr(transition, "_require_safe_env_file", lambda path: path)
    monkeypatch.setattr(transition, "_load_opensandbox_host_config", lambda: host_config)
    monkeypatch.setattr(
        transition,
        "_require_workspace_root_env",
        lambda path, config=None: config is host_config or pytest.fail("host config was not passed"),
    )

    def target_runtime(*args, **kwargs):
        fenced = transition.os.environ.get("AI_PLATFORM_FRONTEND_PORT") == "127.0.0.1:18001"
        assert transition.os.environ["SANDBOX_WORKSPACE_ROOT"] == str(host_config.workspace_root)
        assert kwargs["host_config"] is host_config
        events.append("acceptance-runtime" if fenced else "admitted-runtime")
        return COMMIT, ()

    monkeypatch.setattr(transition, "_require_target_runtime", target_runtime)
    monkeypatch.setattr(transition, "_require_quiescent", lambda docker: events.append("quiescent"))

    def deploy(*args, **kwargs):
        assert "AI_PLATFORM_FRONTEND_PORT" not in transition.os.environ
        assert kwargs["replace_known_manual_frontend"] is False
        events.append("deploy-admitted")

    monkeypatch.setattr(release_authority, "deploy_clean_commit", deploy)

    result = transition.finalize(
        target_repo_root=tmp_path / "target",
        target_commit=COMMIT,
        env_file=tmp_path / ".env",
        docker_cmd="docker",
    )

    assert result["status"] == "admitted"
    assert events == ["acceptance-runtime", "quiescent", "deploy-admitted", "admitted-runtime"]


def test_finalize_restores_loopback_fence_when_admitted_parity_fails(monkeypatch, tmp_path):
    events = []
    host_config = _fake_host_config()

    @contextmanager
    def unlocked():
        yield

    monkeypatch.setattr(transition.os, "name", "posix")
    monkeypatch.setattr(transition.os, "geteuid", lambda: 0, raising=False)
    monkeypatch.setattr(transition, "_transition_lock", unlocked)
    monkeypatch.setattr(transition, "_require_safe_env_file", lambda path: path)
    monkeypatch.setattr(transition, "_load_opensandbox_host_config", lambda: host_config)
    monkeypatch.setattr(
        transition,
        "_require_workspace_root_env",
        lambda path, config=None: config is host_config or pytest.fail("host config was not passed"),
    )
    runtime_calls = 0

    def target_runtime(*args, **kwargs):
        nonlocal runtime_calls
        runtime_calls += 1
        if runtime_calls == 1:
            return COMMIT, ()
        fenced = transition.os.environ.get("AI_PLATFORM_FRONTEND_PORT") == "127.0.0.1:18001"
        assert transition.os.environ["SANDBOX_WORKSPACE_ROOT"] == str(host_config.workspace_root)
        assert kwargs["host_config"] is host_config
        events.append("target-runtime-fenced" if fenced else "target-runtime-admitted")
        if runtime_calls == 2:
            raise transition.TransitionError("admitted target runtime failed")
        return COMMIT, ()

    monkeypatch.setattr(transition, "_require_target_runtime", target_runtime)
    monkeypatch.setattr(transition, "_require_quiescent", lambda docker: None)

    def deploy(*args, **kwargs):
        fenced = transition.os.environ.get("AI_PLATFORM_FRONTEND_PORT") == "127.0.0.1:18001"
        events.append("deploy-fenced" if fenced else "deploy-admitted")

    monkeypatch.setattr(release_authority, "deploy_clean_commit", deploy)

    with pytest.raises(
        transition.TransitionError,
        match="final admission failed; target runtime restored behind acceptance fence",
    ):
        transition.finalize(
            target_repo_root=tmp_path / "target",
            target_commit=COMMIT,
            env_file=tmp_path / ".env",
            docker_cmd="docker",
        )

    assert events == [
        "deploy-admitted",
        "target-runtime-admitted",
        "deploy-fenced",
        "target-runtime-fenced",
    ]


def test_rollback_waits_for_legacy_startup_convergence(monkeypatch, tmp_path):
    runtime = _legacy_runtime(tmp_path / "legacy")
    target_files = _selection(tmp_path / "target", transition.TARGET_SELECTION).absolute_paths
    attempt = 0
    host_config = _fake_host_config()
    down_calls = []
    up_calls = []

    monkeypatch.setattr(transition, "_down", lambda *args, **kwargs: down_calls.append(kwargs))
    monkeypatch.setattr(
        transition,
        "_run",
        lambda *args, **kwargs: up_calls.append(args[0]) or _completed(args[0]),
    )
    monkeypatch.setattr(transition, "_legacy_runtime", lambda *args: runtime)

    def inspect(docker, name):
        service = next(
            service for service, container in transition.CONTAINERS.items() if container == name
        )
        if service in {"migrate", "workspace-init"}:
            return {"State": {"Status": "exited", "Running": False, "ExitCode": 0}}
        health = "starting" if service == "api" and attempt == 1 else "healthy"
        return {
            "State": {"Status": "running", "Running": True, "Health": {"Status": health}}
        }

    monkeypatch.setattr(transition, "_inspect_container", inspect)

    def converge(collect, *, authority_error_type):
        nonlocal attempt
        assert authority_error_type is transition.TransitionError
        attempt = 1
        assert collect(45) == {"verified": False}
        attempt = 2
        assert collect(43) == {"verified": True}
        return {"verified": True}

    monkeypatch.setattr(release_authority, "converge_final_parity", converge)

    transition._rollback(
        ["docker"],
        runtime=runtime,
        target_files=target_files,
        env_file=tmp_path / ".env",
        workspace_root=host_config.workspace_root,
    )
    assert attempt == 2
    assert down_calls[0]["workspace_root"] == host_config.workspace_root
    assert any(
        f"SANDBOX_WORKSPACE_ROOT={transition.LEGACY_WORKSPACE_ROOT}" in command
        for command in up_calls
    )


@pytest.mark.parametrize(
    ("failed_service", "failed_state"),
    [
        ("api", {"Status": "running", "Running": True, "Health": {"Status": "unhealthy"}}),
        ("api", {"Status": "exited", "Running": True, "Health": {"Status": "starting"}}),
        ("migrate", {"Status": "running", "Running": False, "ExitCode": 1}),
        ("workspace-init", {"Status": "created", "Running": True, "ExitCode": 0}),
    ],
)
def test_legacy_convergence_rejects_hard_failures(
    monkeypatch, tmp_path, failed_service, failed_state
):
    runtime = _legacy_runtime(tmp_path / "legacy")
    monkeypatch.setattr(transition, "_legacy_runtime", lambda *args: runtime)

    def inspect(docker, name):
        service = next(
            service for service, container in transition.CONTAINERS.items() if container == name
        )
        if service == failed_service:
            return {"State": failed_state}
        if service in {"migrate", "workspace-init"}:
            return {"State": {"Status": "exited", "Running": False, "ExitCode": 0}}
        return {
            "State": {
                "Status": "running",
                "Running": True,
                "Health": {"Status": "healthy"},
            }
        }

    monkeypatch.setattr(transition, "_inspect_container", inspect)

    with pytest.raises(
        transition.TransitionError,
        match=rf"legacy rollback state mismatch: {failed_service}",
    ):
        transition._legacy_convergence_report(["docker"], runtime)


def test_explicit_rollback_requires_quiescence_and_restores_legacy_selection(monkeypatch, tmp_path):
    runtime = _legacy_runtime(tmp_path / "legacy")
    target_files = _selection(tmp_path / "target", transition.TARGET_SELECTION).absolute_paths
    host_config = _fake_host_config()
    events = []

    @contextmanager
    def unlocked():
        yield

    monkeypatch.setattr(transition.os, "name", "posix")
    monkeypatch.setattr(transition.os, "geteuid", lambda: 0, raising=False)
    monkeypatch.setattr(transition, "_transition_lock", unlocked)
    monkeypatch.setattr(transition, "_require_safe_env_file", lambda path: path)
    monkeypatch.setattr(transition, "_load_opensandbox_host_config", lambda: host_config)
    monkeypatch.setattr(
        transition,
        "_require_workspace_root_env",
        lambda path, config=None: config is host_config or pytest.fail("host config was not passed"),
    )
    monkeypatch.setattr(transition, "_validated_rollback_runtime", lambda *args, **kwargs: runtime)

    def target_runtime(*args, **kwargs):
        assert transition.os.environ["SANDBOX_WORKSPACE_ROOT"] == str(host_config.workspace_root)
        assert kwargs["host_config"] is host_config
        return COMMIT, target_files

    monkeypatch.setattr(transition, "_require_target_runtime", target_runtime)
    monkeypatch.setattr(transition, "_require_schema_compatibility", lambda *args: events.append("schema-compatible"))
    monkeypatch.setattr(transition, "_require_quiescent", lambda docker: events.append("quiescent"))
    monkeypatch.setattr(transition, "_stop_admission", lambda docker: events.append("stop-admission"))

    def rollback(*args, **kwargs):
        assert kwargs["workspace_root"] == host_config.workspace_root
        events.append("rollback")

    monkeypatch.setattr(transition, "_rollback", rollback)

    result = transition.rollback(
        target_repo_root=tmp_path / "target",
        target_commit=COMMIT,
        legacy_repo_root=tmp_path / "legacy",
        legacy_commit=COMMIT,
        env_file=tmp_path / ".env",
        legacy_backend_image=runtime.backend_image,
        legacy_frontend_image=runtime.frontend_image,
        legacy_executor_image=runtime.executor_image,
        docker_cmd="docker",
    )

    assert result["status"] == "rolled_back"
    assert events == ["schema-compatible", "quiescent", "stop-admission", "quiescent", "rollback"]


def test_explicit_rollback_restores_target_when_legacy_start_fails(monkeypatch, tmp_path):
    runtime = _legacy_runtime(tmp_path / "legacy")
    target_files = _selection(tmp_path / "target", transition.TARGET_SELECTION).absolute_paths
    host_config = _fake_host_config()
    events = []

    @contextmanager
    def unlocked():
        yield

    monkeypatch.setattr(transition.os, "name", "posix")
    monkeypatch.setattr(transition.os, "geteuid", lambda: 0, raising=False)
    monkeypatch.setattr(transition, "_transition_lock", unlocked)
    monkeypatch.setattr(transition, "_require_safe_env_file", lambda path: path)
    monkeypatch.setattr(transition, "_load_opensandbox_host_config", lambda: host_config)
    monkeypatch.setattr(
        transition,
        "_require_workspace_root_env",
        lambda path, config=None: config is host_config or pytest.fail("host config was not passed"),
    )
    monkeypatch.setattr(transition, "_validated_rollback_runtime", lambda *args, **kwargs: runtime)
    target_runtime_calls = 0

    def target_runtime(*args, **kwargs):
        nonlocal target_runtime_calls
        target_runtime_calls += 1
        assert kwargs["host_config"] is host_config
        if target_runtime_calls > 1:
            assert transition.os.environ.get("AI_PLATFORM_API_PORT") == "127.0.0.1:8020"
            assert transition.os.environ.get("AI_PLATFORM_FRONTEND_PORT") == "127.0.0.1:18001"
            assert transition.os.environ["SANDBOX_WORKSPACE_ROOT"] == str(host_config.workspace_root)
            events.append("target-runtime-fenced")
        return COMMIT, target_files

    monkeypatch.setattr(transition, "_require_target_runtime", target_runtime)
    monkeypatch.setattr(transition, "_require_schema_compatibility", lambda *args: None)
    monkeypatch.setattr(transition, "_require_quiescent", lambda docker: None)
    monkeypatch.setattr(transition, "_stop_admission", lambda docker: None)
    def failed_rollback(*args, **kwargs):
        assert kwargs["workspace_root"] == host_config.workspace_root
        raise transition.TransitionError("legacy start failed")

    monkeypatch.setattr(transition, "_rollback", failed_rollback)

    def down_partial_legacy(*args, **kwargs):
        assert kwargs["workspace_root"] == transition.LEGACY_WORKSPACE_ROOT
        events.append("down-partial-legacy")

    monkeypatch.setattr(transition, "_down", down_partial_legacy)
    def restore_target(*args, **kwargs):
        assert transition.os.environ.get("AI_PLATFORM_API_PORT") == "127.0.0.1:8020"
        assert transition.os.environ.get("AI_PLATFORM_FRONTEND_PORT") == "127.0.0.1:18001"
        assert transition.os.environ["SANDBOX_WORKSPACE_ROOT"] == str(host_config.workspace_root)
        events.append("restore-target-fenced")

    monkeypatch.setattr(release_authority, "deploy_clean_commit", restore_target)

    with pytest.raises(transition.TransitionError, match="target runtime restored"):
        transition.rollback(
            target_repo_root=tmp_path / "target",
            target_commit=COMMIT,
            legacy_repo_root=tmp_path / "legacy",
            legacy_commit=COMMIT,
            env_file=tmp_path / ".env",
            legacy_backend_image=runtime.backend_image,
            legacy_frontend_image=runtime.frontend_image,
            legacy_executor_image=runtime.executor_image,
            docker_cmd="docker",
        )
    assert events == ["down-partial-legacy", "restore-target-fenced", "target-runtime-fenced"]


def test_host_prerequisite_requires_server_and_network_guard(monkeypatch, tmp_path):
    commands = []
    checks = []
    host_config = _fake_host_config(topology=CUSTOM_TOPOLOGY)

    def run(command, **kwargs):
        commands.append(command)
        return _completed(command)

    monkeypatch.setattr(transition, "_run", run)
    monkeypatch.setattr(
        transition,
        "_require_opensandbox_server_profile",
        lambda host_config=None: checks.append(("server-profile", host_config)),
    )
    monkeypatch.setattr(
        transition,
        "_require_opensandbox_server_container",
        lambda docker: checks.append(("server-container", docker)),
    )
    monkeypatch.setattr(
        transition,
        "_require_network_guard",
        lambda repo_root, topology=None: checks.append(("guard", repo_root, topology)),
    )

    transition._require_host_prerequisites(tmp_path, ["docker"], host_config)

    assert commands == [
        ["systemctl", "is-active", "--quiet", "opensandbox.service"],
        [
            "systemctl",
            "is-active",
            "--quiet",
            transition.OPENSANDBOX_NETWORK_GUARD_SERVICE,
        ],
    ]
    assert checks == [
        ("server-profile", host_config),
        ("server-container", ["docker"]),
        ("guard", tmp_path, CUSTOM_TOPOLOGY),
    ]


def test_host_prerequisite_rejects_drifted_server_container_topology(monkeypatch):
    container = {
        "HostConfig": {"NetworkMode": "host", "PortBindings": {}},
        "NetworkSettings": {
            "Networks": {"host": {}},
            "Ports": {"8080/tcp": None},
        },
    }
    monkeypatch.setattr(transition, "_docker_json", lambda *args: [container])
    transition._require_opensandbox_server_container(["docker"])

    container["HostConfig"]["NetworkMode"] = "bridge"
    with pytest.raises(transition.TransitionError, match="container topology"):
        transition._require_opensandbox_server_container(["docker"])
    container["HostConfig"]["NetworkMode"] = "host"
    container["NetworkSettings"]["Ports"]["8080/tcp"] = [
        {"HostIp": "0.0.0.0", "HostPort": "8080"}
    ]
    with pytest.raises(transition.TransitionError, match="container topology"):
        transition._require_opensandbox_server_container(["docker"])


def test_opensandbox_server_profile_requires_root_owned_runsc_on_the_egress_network(
    monkeypatch,
    tmp_path,
):
    config = tmp_path / "server.toml"
    config.write_text(
        "[server]\nhost = \"10.56.1.75\"\nport = 8080\n"
        "[runtime]\ntype = \"docker\"\n"
        "[docker]\nnetwork_mode = \"ai-platform-opensandbox-egress-v2\"\n"
        "host_ip = \"10.56.1.75\"\n"
        "no_new_privileges = true\n"
        "[secure_runtime]\ntype = \"gvisor\"\ndocker_runtime = \"runsc\"\n",
        encoding="utf-8",
    )
    real_lstat = Path.lstat
    mode = [0o640]

    def root_owned_lstat(path):
        if path == config:
            return SimpleNamespace(st_mode=stat.S_IFREG | mode[0], st_uid=0)
        return real_lstat(path)

    monkeypatch.setattr(Path, "lstat", root_owned_lstat)
    host_config = _fake_host_config(topology=DEFAULT_TOPOLOGY)
    transition._require_opensandbox_server_profile(config, host_config)

    mode[0] = 0o660
    with pytest.raises(transition.TransitionError, match="configuration is invalid"):
        transition._require_opensandbox_server_profile(config, host_config)
    mode[0] = 0o640

    config.write_text(
        config.read_text(encoding="utf-8").replace(
            "ai-platform-opensandbox-egress-v2", "bridge"
        ),
        encoding="utf-8",
    )
    with pytest.raises(transition.TransitionError, match="isolation profile"):
        transition._require_opensandbox_server_profile(config, host_config)


@pytest.mark.parametrize("topology", [DEFAULT_TOPOLOGY, CUSTOM_TOPOLOGY])
def test_network_guard_requires_complete_ipv4_and_ipv6_host_rules(
    monkeypatch,
    tmp_path,
    topology,
):
    repo_root = ROOT
    source = repo_root / transition.OPENSANDBOX_NETWORK_GUARD_SOURCE
    installed = tmp_path / "installed.service"
    installed.write_bytes(source.read_bytes())
    real_lstat = Path.lstat

    def root_owned_lstat(path):
        if path == installed:
            return SimpleNamespace(st_mode=stat.S_IFREG | 0o644, st_uid=0)
        return real_lstat(path)

    monkeypatch.setattr(Path, "lstat", root_owned_lstat)
    bridge = topology.bridge_name
    subnet = topology.subnet
    proxy = f"{topology.proxy_ipv4}/32"
    port = release_authority.DIRECT_OPENSANDBOX_PROXY_PORT
    v4_forward = [
        f"-A AI_PLATFORM_OSB_FORWARD -i {bridge} ! -s {subnet} -j DROP",
        f"-A AI_PLATFORM_OSB_FORWARD -i {bridge} -o {bridge} -s {proxy} -p tcp -m tcp --sport {port} -m conntrack --ctstate ESTABLISHED -j ACCEPT",
        f"-A AI_PLATFORM_OSB_FORWARD -i {bridge} -o {bridge} -d {proxy} -p tcp -m tcp --dport {port} -m conntrack --ctstate NEW,ESTABLISHED -j ACCEPT",
        f"-A AI_PLATFORM_OSB_FORWARD -i {bridge} -o {bridge} -j DROP",
    ]
    v4_forward.extend(
        f"-A AI_PLATFORM_OSB_FORWARD -i {bridge} -d {destination} -j DROP"
        for destination in (
            "0.0.0.0/8", "10.0.0.0/8", "100.64.0.0/10", "127.0.0.0/8",
            "169.254.0.0/16", "172.16.0.0/12", "192.0.0.0/24", "192.0.2.0/24",
            "192.88.99.0/24", "192.168.0.0/16", "198.18.0.0/15", "198.51.100.0/24",
            "203.0.113.0/24", "224.0.0.0/4", "240.0.0.0/4",
        )
    )
    v4_forward.extend(
        (
            f"-A AI_PLATFORM_OSB_FORWARD -o {bridge} -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT",
            f"-A AI_PLATFORM_OSB_FORWARD -o {bridge} -j DROP",
            f"-A AI_PLATFORM_OSB_FORWARD -i {bridge} -j RETURN",
        )
    )
    valid_v4 = "\n".join(
        [
            f"-A INPUT -i {bridge} -j AI_PLATFORM_OPENSANDBOX",
            "-A AI_PLATFORM_OPENSANDBOX -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT",
            "-A AI_PLATFORM_OPENSANDBOX -j DROP",
            f"-A DOCKER-USER -i {bridge} -j AI_PLATFORM_OSB_FORWARD",
            f"-A DOCKER-USER -o {bridge} -j AI_PLATFORM_OSB_FORWARD",
            *v4_forward,
        ]
    ) + "\n"
    valid_v6 = "\n".join(
        [
            f"-A INPUT -i {bridge} -j AI_PLATFORM_OSB_IPV6",
            f"-A OUTPUT -o {bridge} -j AI_PLATFORM_OSB_IPV6",
            f"-A FORWARD -i {bridge} -j AI_PLATFORM_OSB_IPV6",
            f"-A FORWARD -o {bridge} -j AI_PLATFORM_OSB_IPV6",
            f"-A DOCKER-USER -i {bridge} -j AI_PLATFORM_OSB_IPV6",
            f"-A DOCKER-USER -o {bridge} -j AI_PLATFORM_OSB_IPV6",
            "-A AI_PLATFORM_OSB_IPV6 -j DROP",
        ]
    ) + "\n"

    def save(command):
        return _completed(command, stdout=valid_v4 if command[0] == "iptables-save" else valid_v6)

    monkeypatch.setattr(
        transition,
        "_run",
        save,
    )
    transition._require_network_guard(repo_root, installed, topology)

    # Kernel serialization moves source/destination matches before interfaces.
    canonical_v4 = valid_v4.replace(
        f"-i {bridge} -o {bridge} -s {proxy}",
        f"-s {proxy} -i {bridge} -o {bridge}",
    ).replace(
        f"-i {bridge} -o {bridge} -d {proxy}",
        f"-d {proxy} -i {bridge} -o {bridge}",
    ).replace(f"-i {bridge} ! -s {subnet}", f"! -s {subnet} -i {bridge}")
    monkeypatch.setattr(
        transition, "_run",
        lambda command: _completed(command, stdout=canonical_v4 if command[0] == "iptables-save" else valid_v6),
    )
    transition._require_network_guard(repo_root, installed, topology)

    installed.write_text("different\n", encoding="utf-8")
    with pytest.raises(transition.TransitionError, match="guard unit"):
        transition._require_network_guard(repo_root, installed, topology)
    installed.write_bytes(source.read_bytes())

    monkeypatch.setattr(
        transition,
        "_run",
        lambda command: _completed(
            command,
            stdout=(
                "-A INPUT -j ACCEPT\n" + valid_v4
                if command[0] == "iptables-save"
                else valid_v6
            ),
        ),
    )
    with pytest.raises(transition.TransitionError, match="network guard"):
        transition._require_network_guard(repo_root, installed, topology)

    # A failed refresh must not be mistaken for a usable public-egress guard.
    monkeypatch.setattr(
        transition, "_run",
        lambda command: _completed(command, stdout=(valid_v4 + f"-A DOCKER-USER -i {bridge} -j DROP\n") if command[0] == "iptables-save" else valid_v6),
    )
    with pytest.raises(transition.TransitionError, match="network guard"):
        transition._require_network_guard(repo_root, installed, topology)

    monkeypatch.setattr(
        transition,
        "_run",
        lambda command: _completed(
            command,
            stdout=(
                valid_v4.replace(
                    f"-A AI_PLATFORM_OSB_FORWARD -i {bridge} -d 169.254.0.0/16 -j DROP\n",
                    "",
                )
                if command[0] == "iptables-save"
                else valid_v6
            ),
        ),
    )
    with pytest.raises(transition.TransitionError, match="network guard"):
        transition._require_network_guard(repo_root, installed, topology)

    monkeypatch.setattr(
        transition,
        "_run",
        lambda command: _completed(
            command,
            stdout=valid_v4 if command[0] == "iptables-save" else valid_v6.replace(
                f"-A FORWARD -o {bridge} -j AI_PLATFORM_OSB_IPV6\n", ""
            ),
        ),
    )
    with pytest.raises(transition.TransitionError, match="network guard"):
        transition._require_network_guard(repo_root, installed, topology)


def test_target_network_requires_only_the_egress_proxy(monkeypatch):
    host_config = _fake_host_config(topology=CUSTOM_TOPOLOGY)
    topology = host_config.topology
    prefixlen = ipaddress.ip_network(topology.subnet).prefixlen
    inspected = []
    network = {
        "Name": topology.network_name,
        "Driver": "bridge",
        "Internal": False,
        "EnableIPv4": True,
        "EnableIPv6": False,
        "Options": {
            "com.docker.network.bridge.name": topology.bridge_name,
            "com.docker.network.bridge.enable_ip_masquerade": "true",
            "com.docker.network.bridge.enable_icc": "false",
        },
        "IPAM": {"Config": [{"Subnet": topology.subnet}]},
        "Labels": {
            "com.docker.compose.project": release_authority.COMPOSE_PROJECT,
            "com.docker.compose.network": release_authority.DIRECT_OPENSANDBOX_NETWORK_KEY,
        },
        "Containers": {
            "proxy": {
                "Name": transition.TARGET_BROKER_CONTAINER,
                "IPv4Address": f"{topology.proxy_ipv4}/{prefixlen}",
            },
        },
    }
    monkeypatch.setattr(
        transition,
        "_docker_json",
        lambda *args: inspected.append(args) or [network],
    )
    transition._require_target_network(["docker"], host_config)
    assert inspected[-1] == (["docker"], "network", "inspect", topology.network_name)

    network["Containers"]["proxy"]["IPv4Address"] = f"{topology.proxy_ipv4}/24"
    with pytest.raises(transition.TransitionError, match="network isolation"):
        transition._require_target_network(["docker"], host_config)
    network["Containers"]["proxy"]["IPv4Address"] = f"{topology.proxy_ipv4}/{prefixlen}"

    network["Containers"]["api"] = {"Name": "ai-platform-api"}
    with pytest.raises(transition.TransitionError, match="network isolation"):
        transition._require_target_network(["docker"], host_config)
    network["Containers"].pop("api")
    network["Options"]["com.docker.network.bridge.gateway_mode_ipv4"] = "isolated"
    with pytest.raises(transition.TransitionError, match="network isolation"):
        transition._require_target_network(["docker"], host_config)


def test_target_parity_waits_for_platform_and_broker_startup(monkeypatch, tmp_path):
    platform_reports = iter((False, True, True))
    broker_statuses = iter(("starting", "healthy"))
    host_config = _fake_host_config(topology=CUSTOM_TOPOLOGY)
    attempts = []
    checks = []

    monkeypatch.setattr(
        release_authority,
        "collect_live_parity",
        lambda *args, **kwargs: {"verified": next(platform_reports)},
    )

    def inspect(docker, name):
        assert name == transition.TARGET_BROKER_CONTAINER
        return {
            "Config": {
                "Labels": {
                    "com.docker.compose.project": release_authority.COMPOSE_PROJECT,
                    "com.docker.compose.service": "opensandbox-egress-proxy",
                    "ai-platform.source-commit": COMMIT,
                }
            },
            "State": {"Running": True, "Health": {"Status": next(broker_statuses)}},
        }

    def converge(collect, *, authority_error_type):
        assert authority_error_type is release_authority.ReleaseAuthorityError
        for _ in range(3):
            report = collect(45)
            attempts.append(report["verified"])
            if report["verified"]:
                return report
        raise AssertionError("target parity did not converge")

    monkeypatch.setattr(transition, "_inspect_container", inspect)
    monkeypatch.setattr(release_authority, "converge_final_parity", converge)

    def require_host(repo_root, docker, config):
        assert config is host_config
        checks.append("host")

    def require_network(docker, config):
        assert config is host_config
        checks.append("network")

    monkeypatch.setattr(
        transition,
        "_require_host_prerequisites",
        require_host,
    )
    monkeypatch.setattr(transition, "_require_target_network", require_network)
    monkeypatch.setattr(transition, "_require_target_executor", lambda docker: checks.append("executor"))
    monkeypatch.setattr(transition, "_require_target_lifecycle_reachable", lambda docker: checks.append("lifecycle"))

    transition._require_target_parity(
        ["docker"],
        tmp_path,
        COMMIT,
        docker_cmd="docker",
        host_config=host_config,
    )

    assert attempts == [False, False, True]
    assert checks == ["host", "network", "executor", "lifecycle"]


def test_target_broker_inspection_uses_parity_attempt_budget(monkeypatch):
    broker = {
        "Config": {
            "Labels": {
                "com.docker.compose.project": release_authority.COMPOSE_PROJECT,
                "com.docker.compose.service": "opensandbox-egress-proxy",
                "ai-platform.source-commit": COMMIT,
            }
        },
        "State": {"Running": True, "Health": {"Status": "healthy"}},
    }
    timeouts = []

    def run(command, **kwargs):
        timeouts.append(kwargs["timeout"])
        return _completed(command, stdout=json.dumps([broker]))

    monkeypatch.setattr(transition.subprocess, "run", run)

    report = release_authority.converge_final_parity(
        lambda _: transition._target_broker_parity(["docker"], COMMIT),
        authority_error_type=release_authority.ReleaseAuthorityError,
        timeout_seconds=1,
    )

    assert report == {"verified": True}
    assert len(timeouts) == 1
    assert 0 < timeouts[0] <= 1


def test_target_broker_parity_rejects_unhealthy_or_wrong_identity(monkeypatch):
    def record(
        *,
        project=release_authority.COMPOSE_PROJECT,
        service="opensandbox-egress-proxy",
        source_commit=COMMIT,
        running=True,
        health="healthy",
    ):
        state = {"Running": running}
        if health is not None:
            state["Health"] = {"Status": health} if isinstance(health, str) else health
        return {
            "Config": {
                "Labels": {
                    "com.docker.compose.project": project,
                    "com.docker.compose.service": service,
                    "ai-platform.source-commit": source_commit,
                }
            },
            "State": state,
        }

    current = [record(health="starting")]
    monkeypatch.setattr(transition, "_inspect_container", lambda docker, name: current[0])

    assert transition._target_broker_parity(["docker"], COMMIT) == {"verified": False}
    current[0] = record()
    assert transition._target_broker_parity(["docker"], COMMIT) == {"verified": True}

    for invalid in (
        record(project="wrong-project"),
        record(service="wrong-service"),
        record(source_commit="b" * 40),
        record(running=False),
        record(health=None),
        record(health=[]),
        record(health={}),
        record(health="unhealthy"),
        record(health="restarting"),
    ):
        current[0] = invalid
        with pytest.raises(transition.TransitionError, match="broker runtime is invalid"):
            transition._target_broker_parity(["docker"], COMMIT)


def test_target_lifecycle_is_reachable_from_api_and_worker(monkeypatch):
    environment = ["OPENSANDBOX_BASE_URL=http://172.19.0.1:8080"]
    containers = {
        transition.CONTAINERS[service]: {"Config": {"Env": list(environment)}}
        for service in ("api", "worker")
    }
    monkeypatch.setattr(
        transition,
        "_inspect_container",
        lambda docker, name: containers[name],
    )
    calls = []
    failing_service = {"name": ""}

    def run(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(
            returncode=int(command[2] == failing_service["name"]),
            stdout="",
        )

    monkeypatch.setattr(transition, "_run", run)

    transition._require_target_lifecycle_reachable(["docker"])
    assert [command[2] for command in calls] == [
        transition.CONTAINERS["api"],
        transition.CONTAINERS["worker"],
    ]
    assert all(command[-1] == "http://172.19.0.1:8080/health" for command in calls)

    failing_service["name"] = transition.CONTAINERS["worker"]
    with pytest.raises(transition.TransitionError, match="unreachable from worker"):
        transition._require_target_lifecycle_reachable(["docker"])


def test_target_executor_binding_matches_release_authority_image(monkeypatch):
    backend_id = "sha256:" + "a" * 64
    digest = "sha256:" + "1" * 64
    executor = f"{release_authority.PACKAGED_BACKEND_IMAGE_SUBJECT}@{digest}"
    environment = [
        f"SANDBOX_EXECUTOR_IMAGE={executor}",
        f"OPENSANDBOX_EXECUTOR_IMAGE={executor}",
        f"OPENSANDBOX_EXECUTOR_IMAGE_DIGEST={digest}",
    ]
    containers = {
        transition.CONTAINERS[service]: {
            "Config": {"Env": list(environment)},
            "Image": backend_id,
        }
        for service in ("api", "worker")
    }
    monkeypatch.setattr(transition, "_inspect_container", lambda docker, name: containers[name])
    monkeypatch.setattr(
        release_authority,
        "_image_record",
        lambda docker, reference: {
            "id": backend_id,
            "repo_digests": [executor],
        },
    )

    transition._require_target_executor(["docker"])
    containers[transition.CONTAINERS["worker"]]["Config"]["Env"][-1] = "OPENSANDBOX_EXECUTOR_IMAGE_DIGEST=sha256:" + "b" * 64
    with pytest.raises(transition.TransitionError, match="executor image mismatch"):
        transition._require_target_executor(["docker"])


def test_target_executor_binding_rejects_mutable_or_wrong_backend_image(monkeypatch):
    backend_id = "sha256:" + "a" * 64
    mutable = "ai-platform:latest"
    environment = [
        f"SANDBOX_EXECUTOR_IMAGE={mutable}",
        f"OPENSANDBOX_EXECUTOR_IMAGE={mutable}",
        f"OPENSANDBOX_EXECUTOR_IMAGE_DIGEST={mutable}",
    ]
    containers = {
        transition.CONTAINERS[service]: {
            "Config": {"Env": list(environment)},
            "Image": backend_id,
        }
        for service in ("api", "worker")
    }
    monkeypatch.setattr(transition, "_inspect_container", lambda docker, name: containers[name])
    monkeypatch.setattr(
        release_authority,
        "_image_record",
        lambda docker, reference: {"id": backend_id, "repo_digests": []},
    )

    with pytest.raises(transition.TransitionError, match="executor image mismatch"):
        transition._require_target_executor(["docker"])

    digest = "sha256:" + "1" * 64
    executor = f"{release_authority.PACKAGED_BACKEND_IMAGE_SUBJECT}@{digest}"
    for container in containers.values():
        container["Config"]["Env"] = [
            f"SANDBOX_EXECUTOR_IMAGE={executor}",
            f"OPENSANDBOX_EXECUTOR_IMAGE={executor}",
            f"OPENSANDBOX_EXECUTOR_IMAGE_DIGEST={digest}",
        ]
    monkeypatch.setattr(
        release_authority,
        "_image_record",
        lambda docker, reference: {
            "id": "sha256:" + "b" * 64,
            "repo_digests": [executor],
        },
    )
    with pytest.raises(transition.TransitionError, match="executor image mismatch"):
        transition._require_target_executor(["docker"])


def test_rollback_executor_reference_may_alias_verified_backend_image(monkeypatch, tmp_path):
    backend_id = "sha256:" + "a" * 64
    backend = "registry.example/backend@sha256:" + "1" * 64
    frontend = "registry.example/frontend@sha256:" + "2" * 64
    executor = backend_id
    selection = _selection(tmp_path, transition.LEGACY_SELECTION)
    monkeypatch.setattr(transition, "_assert_root_owned_checkout", lambda *args: COMMIT)
    monkeypatch.setattr(release_authority, "resolve_compose_files", lambda *args: selection)
    monkeypatch.setattr(release_authority, "authoritative_repository", lambda *args: release_authority.AUTHORITATIVE_REPOSITORY)
    records = {
        backend: {"id": backend_id},
        frontend: {"id": "sha256:" + "b" * 64},
        executor: {"id": backend_id},
    }
    monkeypatch.setattr(release_authority, "_image_record", lambda docker, reference: records[reference])
    monkeypatch.setattr(release_authority, "_validate_release_image", lambda *args, **kwargs: None)

    runtime = transition._validated_rollback_runtime(
        ["docker"],
        legacy_repo_root=tmp_path,
        legacy_commit=COMMIT,
        backend_image=backend,
        frontend_image=frontend,
        executor_image=executor,
    )
    assert runtime.executor_image == executor

    records[executor] = {"id": "sha256:" + "c" * 64}
    with pytest.raises(transition.TransitionError, match="verified backend image"):
        transition._validated_rollback_runtime(
            ["docker"],
            legacy_repo_root=tmp_path,
            legacy_commit=COMMIT,
            backend_image=backend,
            frontend_image=frontend,
            executor_image=executor,
        )


def test_managed_environment_file_metadata_fails_closed_without_reading_contents():
    class ManagedEnvironmentPath:
        def __init__(self, *, mode=stat.S_IFREG | 0o600, uid=0, symlink=False):
            self.metadata = SimpleNamespace(st_mode=mode, st_uid=uid)
            self.symlink = symlink

        def lstat(self):
            return self.metadata

        def is_symlink(self):
            return self.symlink

        def resolve(self, *, strict=False):
            return self

    valid = ManagedEnvironmentPath()
    assert transition._require_safe_env_file(valid) is valid

    for invalid in (
        ManagedEnvironmentPath(mode=stat.S_IFREG | 0o644),
        ManagedEnvironmentPath(uid=1000),
        ManagedEnvironmentPath(mode=stat.S_IFDIR | 0o600),
        ManagedEnvironmentPath(symlink=True),
    ):
        with pytest.raises(transition.TransitionError, match="metadata mismatch"):
            transition._require_safe_env_file(invalid)


def test_managed_host_application_contract_matches_config_and_rejects_single_field_drift(
    monkeypatch,
    tmp_path,
):
    host_config = _fake_host_config(topology=CUSTOM_TOPOLOGY)
    values = {
        "OPENSANDBOX_BASE_URL": f"http://{host_config.lifecycle_address}:8080",
        "OPENSANDBOX_API_KEY": APPLICATION_API_KEY,
        "OPENSANDBOX_EXPECTED_NETWORK_MODE": host_config.topology.network_name,
        "OPENSANDBOX_EGRESS_BRIDGE": host_config.topology.bridge_name,
        "OPENSANDBOX_EGRESS_SUBNET": host_config.topology.subnet,
        "OPENSANDBOX_EGRESS_PROXY_IPV4": host_config.topology.proxy_ipv4,
        "SANDBOX_WORKSPACE_ROOT": str(host_config.workspace_root),
    }
    env_file = tmp_path / "managed.env"
    monkeypatch.setattr(
        production_bootstrap,
        "_read_secure_text",
        lambda path, **kwargs: path.read_text(encoding="utf-8"),
    )

    def write_environment(overrides=None):
        current = dict(values)
        current.update(overrides or {})
        env_file.write_text(
            "UNRELATED=private-value\n"
            + "".join(f"{key}={value}\n" for key, value in current.items()),
            encoding="utf-8",
        )

    monkeypatch.delenv("SANDBOX_WORKSPACE_ROOT", raising=False)
    write_environment()
    transition._require_workspace_root_env(env_file, host_config)

    drift_cases = (
        {"OPENSANDBOX_EXPECTED_NETWORK_MODE": "ai-platform-osb-different"},
        {"OPENSANDBOX_EGRESS_BRIDGE": "br-osb-different"},
        {"OPENSANDBOX_EGRESS_SUBNET": "172.30.240.0/28"},
        {"OPENSANDBOX_EGRESS_PROXY_IPV4": "172.30.240.3"},
        {"SANDBOX_WORKSPACE_ROOT": "/srv/ai-platform/other-workspaces"},
        {"OPENSANDBOX_API_KEY": "different-application-key"},
        {"OPENSANDBOX_BASE_URL": "http://10.56.1.76:8080"},
    )
    for drift in drift_cases:
        write_environment(drift)
        with pytest.raises(
            transition.TransitionError,
            match="application host configuration mismatch",
        ):
            transition._require_workspace_root_env(env_file, host_config)

    write_environment()
    with env_file.open("a", encoding="utf-8") as stream:
        stream.write(f"SANDBOX_WORKSPACE_ROOT={host_config.workspace_root}\n")
    with pytest.raises(
        transition.TransitionError,
        match="application host configuration mismatch",
    ):
        transition._require_workspace_root_env(env_file, host_config)

    write_environment()
    for key, value in values.items():
        if key == "SANDBOX_WORKSPACE_ROOT":
            continue
        with monkeypatch.context() as overrides:
            overrides.setenv(key, "process-override")
            with pytest.raises(transition.TransitionError, match="environment override mismatch"):
                transition._require_workspace_root_env(env_file, host_config)

    monkeypatch.setenv("SANDBOX_WORKSPACE_ROOT", "/process-override")
    with pytest.raises(transition.TransitionError, match="workspace root configuration"):
        transition._require_workspace_root_env(env_file, host_config)


def test_transition_lock_rejects_unsafe_metadata(monkeypatch):
    closed = []
    fake_fcntl = SimpleNamespace(LOCK_EX=1, LOCK_NB=2, flock=lambda descriptor, flags: None)
    monkeypatch.setattr(transition, "fcntl", fake_fcntl)
    monkeypatch.setattr(transition.os, "open", lambda *args: 7)
    monkeypatch.setattr(
        transition.os,
        "fstat",
        lambda descriptor: SimpleNamespace(st_mode=stat.S_IFREG | 0o666, st_uid=0),
    )
    monkeypatch.setattr(transition.os, "close", closed.append)

    with pytest.raises(transition.TransitionError, match="lock metadata mismatch"):
        with transition._transition_lock():
            pytest.fail("unsafe lock must not be acquired")
    assert closed == [7]
