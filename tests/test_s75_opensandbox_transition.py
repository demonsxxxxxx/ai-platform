from __future__ import annotations

import stat
import subprocess
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

import tools.release_authority as release_authority
import tools.s75_opensandbox_transition as transition


ROOT = Path(__file__).resolve().parents[1]
COMPOSE_DIR = ROOT / "deploy" / "ai-platform"
COMMIT = "a" * 40


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


def _legacy_containers(selection, commit=COMMIT):
    config_files = ",".join(str(path) for path in selection.absolute_paths)
    working_dir = str(selection.absolute_paths[0].parent)
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
        if service in transition.EXPECTED_WORKSPACE_BINDS:
            destination, source = transition.EXPECTED_WORKSPACE_BINDS[service]
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
                    [f"SANDBOX_WORKSPACE_ROOT={transition.S75_WORKSPACE_ROOT}"]
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
    containers = _legacy_containers(selection)

    monkeypatch.setattr(release_authority, "assert_managed_target_checkout", lambda root, commit, release_root: COMMIT)
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
    assert runtime.commit == COMMIT
    assert runtime.backend_image == "ai-platform:old"
    assert runtime.frontend_image == "ai-platform-frontend:old"
    assert runtime.executor_image == "ai-platform:old"

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
        f"SANDBOX_WORKSPACE_ROOT={transition.S75_WORKSPACE_ROOT}"
    )

    containers["postgres"]["Mounts"][0]["Name"] = "wrong-volume"
    with pytest.raises(transition.TransitionError, match="volume identity mismatch"):
        transition._legacy_runtime(["docker"], tmp_path, COMMIT)


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
    events = []
    quiescence_calls = 0

    monkeypatch.setattr(transition.os, "name", "posix")
    monkeypatch.setattr(transition.os, "geteuid", lambda: 0, raising=False)
    monkeypatch.setattr(transition, "_require_safe_env_file", lambda path: path)
    monkeypatch.setattr(transition, "_require_workspace_root_env", lambda path: None)
    monkeypatch.setattr(transition, "_legacy_runtime", lambda *args: runtime)
    monkeypatch.setattr(transition, "_require_host_prerequisites", lambda: events.append("host"))

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
        events.append("down-legacy")
        if down_error is not None:
            raise down_error

    monkeypatch.setattr(transition, "_down", down)

    def deploy(*args, **kwargs):
        events.append("deploy-target")
        assert kwargs["replace_known_manual_frontend"] is False
        if deploy_error is not None:
            raise deploy_error

    monkeypatch.setattr(release_authority, "deploy_clean_commit", deploy)
    def target_runtime(*args, **kwargs):
        events.append("target-runtime")
        if parity_error is not None:
            raise parity_error
        return COMMIT, target_selection.absolute_paths

    monkeypatch.setattr(transition, "_require_target_runtime", target_runtime)
    monkeypatch.setattr(transition, "_rollback", lambda *args, **kwargs: events.append("rollback"))
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

    @contextmanager
    def unlocked():
        yield

    monkeypatch.setattr(transition.os, "name", "posix")
    monkeypatch.setattr(transition.os, "geteuid", lambda: 0, raising=False)
    monkeypatch.setattr(transition, "_transition_lock", unlocked)
    monkeypatch.setattr(transition, "_require_safe_env_file", lambda path: path)
    monkeypatch.setattr(transition, "_require_workspace_root_env", lambda path: None)

    def target_runtime(*args, **kwargs):
        fenced = transition.os.environ.get("AI_PLATFORM_FRONTEND_PORT") == "127.0.0.1:18001"
        assert transition.os.environ["SANDBOX_WORKSPACE_ROOT"] == transition.S75_WORKSPACE_ROOT
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

    @contextmanager
    def unlocked():
        yield

    monkeypatch.setattr(transition.os, "name", "posix")
    monkeypatch.setattr(transition.os, "geteuid", lambda: 0, raising=False)
    monkeypatch.setattr(transition, "_transition_lock", unlocked)
    monkeypatch.setattr(transition, "_require_safe_env_file", lambda path: path)
    monkeypatch.setattr(transition, "_require_workspace_root_env", lambda path: None)
    runtime_calls = 0

    def target_runtime(*args, **kwargs):
        nonlocal runtime_calls
        runtime_calls += 1
        if runtime_calls == 1:
            return COMMIT, ()
        fenced = transition.os.environ.get("AI_PLATFORM_FRONTEND_PORT") == "127.0.0.1:18001"
        assert transition.os.environ["SANDBOX_WORKSPACE_ROOT"] == transition.S75_WORKSPACE_ROOT
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


def test_explicit_rollback_requires_quiescence_and_restores_legacy_selection(monkeypatch, tmp_path):
    runtime = _legacy_runtime(tmp_path / "legacy")
    target_files = _selection(tmp_path / "target", transition.TARGET_SELECTION).absolute_paths
    events = []

    @contextmanager
    def unlocked():
        yield

    monkeypatch.setattr(transition.os, "name", "posix")
    monkeypatch.setattr(transition.os, "geteuid", lambda: 0, raising=False)
    monkeypatch.setattr(transition, "_transition_lock", unlocked)
    monkeypatch.setattr(transition, "_require_safe_env_file", lambda path: path)
    monkeypatch.setattr(transition, "_require_workspace_root_env", lambda path: None)
    monkeypatch.setattr(transition, "_validated_rollback_runtime", lambda *args, **kwargs: runtime)
    monkeypatch.setattr(transition, "_require_target_runtime", lambda *args, **kwargs: (COMMIT, target_files))
    monkeypatch.setattr(transition, "_require_schema_compatibility", lambda *args: events.append("schema-compatible"))
    monkeypatch.setattr(transition, "_require_quiescent", lambda docker: events.append("quiescent"))
    monkeypatch.setattr(transition, "_stop_admission", lambda docker: events.append("stop-admission"))
    monkeypatch.setattr(transition, "_rollback", lambda *args, **kwargs: events.append("rollback"))

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
    events = []

    @contextmanager
    def unlocked():
        yield

    monkeypatch.setattr(transition.os, "name", "posix")
    monkeypatch.setattr(transition.os, "geteuid", lambda: 0, raising=False)
    monkeypatch.setattr(transition, "_transition_lock", unlocked)
    monkeypatch.setattr(transition, "_require_safe_env_file", lambda path: path)
    monkeypatch.setattr(transition, "_require_workspace_root_env", lambda path: None)
    monkeypatch.setattr(transition, "_validated_rollback_runtime", lambda *args, **kwargs: runtime)
    target_runtime_calls = 0

    def target_runtime(*args, **kwargs):
        nonlocal target_runtime_calls
        target_runtime_calls += 1
        if target_runtime_calls > 1:
            assert transition.os.environ.get("AI_PLATFORM_API_PORT") == "127.0.0.1:8020"
            assert transition.os.environ.get("AI_PLATFORM_FRONTEND_PORT") == "127.0.0.1:18001"
            assert transition.os.environ["SANDBOX_WORKSPACE_ROOT"] == transition.S75_WORKSPACE_ROOT
            events.append("target-runtime-fenced")
        return COMMIT, target_files

    monkeypatch.setattr(transition, "_require_target_runtime", target_runtime)
    monkeypatch.setattr(transition, "_require_schema_compatibility", lambda *args: None)
    monkeypatch.setattr(transition, "_require_quiescent", lambda docker: None)
    monkeypatch.setattr(transition, "_stop_admission", lambda docker: None)
    monkeypatch.setattr(transition, "_rollback", lambda *args, **kwargs: (_ for _ in ()).throw(transition.TransitionError("legacy start failed")))
    monkeypatch.setattr(transition, "_down", lambda *args, **kwargs: events.append("down-partial-legacy"))
    def restore_target(*args, **kwargs):
        assert transition.os.environ.get("AI_PLATFORM_API_PORT") == "127.0.0.1:8020"
        assert transition.os.environ.get("AI_PLATFORM_FRONTEND_PORT") == "127.0.0.1:18001"
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


def test_host_prerequisite_defers_health_to_target_container_parity(monkeypatch):
    commands = []

    def run(command, **kwargs):
        commands.append(command)
        return _completed(command)

    monkeypatch.setattr(transition, "_run", run)

    transition._require_host_prerequisites()

    assert commands == [["systemctl", "is-active", "--quiet", "opensandbox.service"]]


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
    environment = [
        "SANDBOX_EXECUTOR_IMAGE=sha256:" + "a" * 64,
        "OPENSANDBOX_EXECUTOR_IMAGE=sha256:" + "a" * 64,
        "OPENSANDBOX_EXECUTOR_IMAGE_DIGEST=sha256:" + "a" * 64,
    ]
    containers = {
        transition.CONTAINERS[service]: {"Config": {"Env": list(environment)}}
        for service in ("api", "worker")
    }
    monkeypatch.setattr(transition, "_inspect_container", lambda docker, name: containers[name])

    transition._require_target_executor(["docker"])
    containers[transition.CONTAINERS["worker"]]["Config"]["Env"][-1] = "OPENSANDBOX_EXECUTOR_IMAGE_DIGEST=sha256:" + "b" * 64
    with pytest.raises(transition.TransitionError, match="executor image mismatch"):
        transition._require_target_executor(["docker"])


def test_rollback_executor_reference_may_alias_verified_backend_image(monkeypatch, tmp_path):
    backend_id = "sha256:" + "a" * 64
    backend = "registry.example/backend@sha256:" + "1" * 64
    frontend = "registry.example/frontend@sha256:" + "2" * 64
    executor = backend_id
    selection = _selection(tmp_path, transition.LEGACY_SELECTION)
    monkeypatch.setattr(release_authority, "assert_managed_target_checkout", lambda *args: COMMIT)
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


def test_managed_workspace_root_configuration_fails_closed(monkeypatch, tmp_path):
    env_file = tmp_path / "managed.env"
    env_file.write_text(
        f"UNRELATED=private-value\nSANDBOX_WORKSPACE_ROOT={transition.S75_WORKSPACE_ROOT}\n",
        encoding="utf-8",
    )
    transition._require_workspace_root_env(env_file)

    for contents in (
        "UNRELATED=private-value\n",
        "SANDBOX_WORKSPACE_ROOT=/wrong-workspace\n",
        f"SANDBOX_WORKSPACE_ROOT={transition.S75_WORKSPACE_ROOT}\n"
        f"SANDBOX_WORKSPACE_ROOT={transition.S75_WORKSPACE_ROOT}\n",
    ):
        env_file.write_text(contents, encoding="utf-8")
        with pytest.raises(transition.TransitionError, match="workspace root configuration"):
            transition._require_workspace_root_env(env_file)

    env_file.write_text(
        f"SANDBOX_WORKSPACE_ROOT={transition.S75_WORKSPACE_ROOT}\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("SANDBOX_WORKSPACE_ROOT", "/process-override")
    with pytest.raises(transition.TransitionError, match="workspace root configuration"):
        transition._require_workspace_root_env(env_file)


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
