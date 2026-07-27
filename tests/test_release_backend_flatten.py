import io
import json
from pathlib import Path
import subprocess
import sys
import tarfile

import pytest

import tools.release_authority as release_authority
from tools.release_backend_flatten import (
    BACKEND_LAYER_FLATTEN_MIN_LAYERS,
    BackendFlattenError,
    flattened_backend_base,
)


COMMIT = "a" * 40
REPOSITORY = "https://github.com/demonsxxxxxx/ai-platform.git"
CURRENT_REFERENCE = f"ai-platform:{COMMIT}"


def _labels():
    return {
        "ai-platform.source-commit": COMMIT,
        "org.opencontainers.image.revision": COMMIT,
        "ai-platform.source-repository": REPOSITORY,
        "ai-platform.build-dirty": "false",
        "ai-platform.release-role": "backend",
    }


def _image_payload(*, layers=BACKEND_LAYER_FLATTEN_MIN_LAYERS, labels=None, config=None):
    environment = [
        "PATH=/usr/local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
        "PYTHONDONTWRITEBYTECODE=1",
        "PYTHONUNBUFFERED=1",
        "PIP_DISABLE_PIP_VERSION_CHECK=1",
        "APP_MODULE=app.main:create_app",
        "APP_PORT=8020",
        "HOME=/home/ai-platform",
        "TMPDIR=/home/ai-platform/tmp",
        "XDG_CACHE_HOME=/home/ai-platform/.cache",
        "XDG_CONFIG_HOME=/home/ai-platform/.config",
        "XDG_DATA_HOME=/home/ai-platform/.local/share",
    ]
    image_config = {
        "User": "10001:10001",
        "WorkingDir": "/app",
        "Entrypoint": ["/app/docker-entrypoint.sh"],
        "Cmd": ["uvicorn"],
        "Env": environment,
        "ExposedPorts": {"8020/tcp": {}},
        "Labels": _labels() if labels is None else labels,
    }
    if config:
        image_config.update(config)
    return {
        "Id": "sha256:" + "f" * 64,
        "RootFS": {"Layers": [f"sha256:{index:064x}" for index in range(layers)]},
        "Config": image_config,
    }


def _tar_member(archive, name, content, mode=0o644):
    data = content.encode("utf-8")
    member = tarfile.TarInfo(name)
    member.size = len(data)
    member.mode = mode
    archive.addfile(member, io.BytesIO(data))


def _write_rootfs_archive(path):
    snapshot = {
        "schema_version": "ai-platform.source-snapshot.v1",
        "source_tree_commit_sha": COMMIT,
        "runtime_subject_commit_sha": COMMIT,
        "source_tree_dirty": False,
    }
    with tarfile.open(path, "w") as archive:
        _tar_member(archive, "app/.ai-platform-source-revision", f"{COMMIT}\n")
        _tar_member(archive, "app/.codex-source-revision", f"{COMMIT}\n")
        _tar_member(archive, "app/.source-commit", f"{COMMIT}\n")
        _tar_member(
            archive,
            "app/.ai-platform-source-snapshot.json",
            json.dumps(snapshot),
        )
        _tar_member(archive, "app/docker-entrypoint.sh", "#!/bin/sh\nexec \"$@\"\n", 0o755)
        _tar_member(archive, "usr/local/bin/python", "python", 0o755)
        _tar_member(archive, "usr/local/bin/uvicorn", "uvicorn", 0o755)
        _tar_member(archive, "etc/passwd", "ai-platform:x:10001:10001::/home/ai-platform:/usr/sbin/nologin\n")
        _tar_member(archive, "etc/group", "ai-platform:x:10001:\n")


class _FakeDocker:
    def __init__(self, tmp_path, *, source=None, flat=None, fail_stage=None):
        self.tmp_path = tmp_path
        self.source = source or _image_payload()
        self.flat = flat or _image_payload(layers=1)
        self.fail_stage = fail_stage
        self.commands = []

    def __call__(self, command, **kwargs):
        command = list(command)
        self.commands.append((command, kwargs))
        is_source_inspect = command[:3] == ["docker", "image", "inspect"] and command[-1] == CURRENT_REFERENCE
        is_flat_inspect = command[:3] == ["docker", "image", "inspect"] and command[-1] != CURRENT_REFERENCE
        if is_source_inspect:
            return subprocess.CompletedProcess(command, 0, stdout=json.dumps([self.source]), stderr="")
        if is_flat_inspect:
            if self.fail_stage == "validate":
                raise subprocess.CalledProcessError(1, command)
            return subprocess.CompletedProcess(command, 0, stdout=json.dumps([self.flat]), stderr="")
        if command[:3] == ["docker", "container", "export"]:
            if self.fail_stage == "export":
                raise subprocess.CalledProcessError(1, command)
            archive = Path(command[command.index("--output") + 1])
            _write_rootfs_archive(archive)
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        if command[:3] == ["docker", "image", "import"]:
            if self.fail_stage == "import":
                raise subprocess.CalledProcessError(1, command)
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")


def _cleanup_commands(fake):
    return [command for command, _ in fake.commands if command[1:3] in (["container", "rm"], ["image", "rm"])]


def test_flattened_backend_base_exports_imports_and_passes_only_noncanonical_ref(tmp_path):
    docker = _FakeDocker(tmp_path)
    bases = []

    with flattened_backend_base(
        docker=["docker"],
        source_reference=CURRENT_REFERENCE,
        expected_commit=COMMIT,
        expected_repository=REPOSITORY,
        archive_root=tmp_path,
        runner=docker,
    ) as flattened:
        bases.append(flattened.reference)
        assert flattened.reference != CURRENT_REFERENCE
        assert flattened.reference.startswith("ai-platform:flatten-base-")
        assert flattened.source_layer_count == BACKEND_LAYER_FLATTEN_MIN_LAYERS
        assert flattened.flat_layer_count == 1

    assert len(bases) == 1
    imports = [command for command, _ in docker.commands if command[1:3] == ["image", "import"]]
    assert len(imports) == 1
    assert imports[0][-1] == bases[0]
    assert any("LABEL ai-platform.source-commit=" + COMMIT in value for value in imports[0])
    assert not any(command[1] == "tag" for command, _ in docker.commands)
    assert all(CURRENT_REFERENCE not in command for command in _cleanup_commands(docker))
    assert any(command[1:3] == ["container", "rm"] for command in _cleanup_commands(docker))
    assert any(command[1:3] == ["image", "rm"] and bases[0] in command for command in _cleanup_commands(docker))
    archive_paths = [Path(command[command.index("--output") + 1]) for command, _ in docker.commands if "--output" in command]
    assert archive_paths and all(not path.exists() for path in archive_paths)


@pytest.mark.parametrize("fail_stage", ["export", "import", "validate", "target-build"])
def test_flattened_backend_base_cleans_all_temporary_subjects_on_every_failure(tmp_path, fail_stage):
    docker = _FakeDocker(tmp_path, fail_stage=None if fail_stage == "target-build" else fail_stage)

    with pytest.raises((BackendFlattenError, RuntimeError)):
        with flattened_backend_base(
            docker=["docker"],
            source_reference=CURRENT_REFERENCE,
            expected_commit=COMMIT,
            expected_repository=REPOSITORY,
            archive_root=tmp_path,
            runner=docker,
        ):
            if fail_stage == "target-build":
                raise RuntimeError("target build failed")

    cleanup = _cleanup_commands(docker)
    assert any(command[1:3] == ["container", "rm"] for command in cleanup)
    assert all(CURRENT_REFERENCE not in command for command in cleanup)
    archive_paths = [Path(command[command.index("--output") + 1]) for command, _ in docker.commands if "--output" in command]
    assert all(not path.exists() for path in archive_paths)


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        (_image_payload(layers=BACKEND_LAYER_FLATTEN_MIN_LAYERS - 1), "layer threshold"),
        (_image_payload(labels={**_labels(), "ai-platform.build-dirty": "true"}), "provenance"),
        (_image_payload(config={"Env": ["API_TOKEN=private"]}), "unsafe"),
        (_image_payload(config={"Volumes": {"/runtime-data": {}}}), "mount"),
    ],
)
def test_flattened_backend_base_rejects_unverified_low_layer_or_unsafe_source_before_container_create(
    tmp_path, source, expected
):
    docker = _FakeDocker(tmp_path, source=source)

    with pytest.raises(BackendFlattenError, match=expected):
        with flattened_backend_base(
            docker=["docker"],
            source_reference=CURRENT_REFERENCE,
            expected_commit=COMMIT,
            expected_repository=REPOSITORY,
            archive_root=tmp_path,
            runner=docker,
        ):
            raise AssertionError("unsafe source must not be yielded")

    assert not any(command[1:3] == ["container", "create"] for command, _ in docker.commands)


def test_flattened_backend_base_rejects_flat_config_or_marker_identity_before_target_build(tmp_path):
    bad_flat = _image_payload(layers=1, config={"User": "root"})
    docker = _FakeDocker(tmp_path, flat=bad_flat)
    entered = False

    with pytest.raises(BackendFlattenError, match="flat image config"):
        with flattened_backend_base(
            docker=["docker"],
            source_reference=CURRENT_REFERENCE,
            expected_commit=COMMIT,
            expected_repository=REPOSITORY,
            archive_root=tmp_path,
            runner=docker,
        ):
            entered = True

    assert entered is False
    assert any(command[1:3] == ["image", "rm"] for command in _cleanup_commands(docker))


def test_flattened_backend_base_uses_no_runtime_container_env_or_mounts(tmp_path):
    docker = _FakeDocker(tmp_path)

    with flattened_backend_base(
        docker=["docker"],
        source_reference=CURRENT_REFERENCE,
        expected_commit=COMMIT,
        expected_repository=REPOSITORY,
        archive_root=tmp_path,
        runner=docker,
    ):
        pass

    for command, _ in docker.commands:
        assert "--env" not in command
        assert "--mount" not in command
        assert "--volume" not in command
        assert not (command[1:3] == ["container", "inspect"])


def test_rebuild_from_flattened_backend_passes_only_the_verified_flat_ref_to_target_build(tmp_path):
    docker = _FakeDocker(tmp_path)
    supplied = []

    release_authority.rebuild_from_flattened_backend(
        docker=["docker"],
        source_reference=CURRENT_REFERENCE,
        expected_commit=COMMIT,
        expected_repository=REPOSITORY,
        archive_root=tmp_path,
        runner=docker,
        target_build=supplied.append,
    )

    assert len(supplied) == 1
    assert supplied[0].startswith("ai-platform:flatten-base-")
    assert supplied[0] != CURRENT_REFERENCE
    events = []
    staged = []
    release_authority._stage(
        events,
        name="backend-layer-flatten-recovery",
        strategy="auto",
        action="flatten-recovery",
        operation=lambda: release_authority.rebuild_from_flattened_backend(
            docker=["docker"],
            source_reference=CURRENT_REFERENCE,
            expected_commit=COMMIT,
            expected_repository=REPOSITORY,
            archive_root=tmp_path,
            runner=docker,
            target_build=staged.append,
        ),
    )
    assert staged and staged[0] not in json.dumps(events)


@pytest.mark.parametrize(
    ("strategy", "backend_action", "message"),
    [
        ("canonical", "runtime-rebuild", "auto strategy"),
        ("auto", "promote", "runtime-rebuild"),
    ],
)
def test_flatten_recovery_request_rejects_any_non_auto_runtime_rebuild(strategy, backend_action, message):
    from tools.release_backend_flatten import validate_backend_layer_flatten_recovery_request

    with pytest.raises(BackendFlattenError, match=message):
        validate_backend_layer_flatten_recovery_request(
            enabled=True,
            strategy=strategy,
            backend_action=backend_action,
        )


@pytest.mark.parametrize("enabled", [False, True])
def test_deploy_main_cli_plumbs_default_off_and_explicit_flatten_opt_in(monkeypatch, capsys, tmp_path, enabled):
    observed = {}

    def fake_deploy_main(release_root, commit, **kwargs):
        observed.update(kwargs)
        return {"commit": commit}

    command = [
        "release_authority.py",
        "deploy-main-commit",
        "--release-root",
        str(tmp_path / "releases"),
        "--commit",
        COMMIT,
    ]
    if enabled:
        command.extend(("--strategy", "auto", "--allow-backend-layer-flatten-recovery"))
    monkeypatch.setattr("tools.release_authority.deploy_main_commit", fake_deploy_main)
    monkeypatch.setattr(sys, "argv", command)

    assert release_authority.main() == 0
    assert observed["allow_backend_layer_flatten_recovery"] is enabled
    assert json.loads(capsys.readouterr().out) == {"commit": COMMIT}
