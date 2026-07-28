import io
import json
import os
import stat
import subprocess
import sys
import tarfile

import pytest

import tools.release_authority as release_authority
import tools.release_backend_flatten as release_backend_flatten
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


def _write_rootfs_archive(target):
    snapshot = {
        "schema_version": "ai-platform.source-snapshot.v1",
        "source_tree_commit_sha": COMMIT,
        "runtime_subject_commit_sha": COMMIT,
        "source_tree_dirty": False,
    }
    if hasattr(target, "write"):
        archive = tarfile.open(fileobj=target, mode="w")
    else:
        archive = tarfile.open(target, "w")
    with archive:
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
    def __init__(self, tmp_path, *, source=None, flat=None, fail_stage=None, cleanup_nonzero=False):
        self.tmp_path = tmp_path
        self.source = source or _image_payload()
        self.flat = flat or _image_payload(layers=1)
        self.fail_stage = fail_stage
        self.cleanup_nonzero = cleanup_nonzero
        self.commands = []
        self.export_observations = []

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
            assert "--output" not in command
            sink = kwargs["stdout_sink"]
            _write_rootfs_archive(sink)
            sink.flush()
            self.export_observations.append(os.fstat(sink.fileno()))
            return subprocess.CompletedProcess(command, 0, stdout=None, stderr="")
        if command[:3] == ["docker", "image", "import"]:
            if self.fail_stage == "import":
                raise subprocess.CalledProcessError(1, command)
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        if self.cleanup_nonzero and command[1:3] in (["container", "rm"], ["image", "rm"]):
            return subprocess.CompletedProcess(command, 1, stdout="", stderr="")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")


def _cleanup_commands(fake):
    return [command for command, _ in fake.commands if command[1:3] in (["container", "rm"], ["image", "rm"])]


def _replace_archive(path):
    path.unlink()
    path.write_bytes(b"replacement")
    if os.name == "posix":
        os.chmod(path, 0o600)


def test_flattened_backend_base_streams_two_secure_archives_then_imports_only_the_noncanonical_ref(
    tmp_path, monkeypatch
):
    docker = _FakeDocker(tmp_path)
    bases = []
    verified_archives = []
    original_verify_archive = release_backend_flatten._verify_archive

    def observe_verified_archive(path, identity):
        archive = original_verify_archive(path, identity)
        metadata = path.stat(follow_symlinks=False)
        with path.open("rb") as handle:
            assert handle.read(1)
        verified_archives.append((metadata, identity))
        return archive

    monkeypatch.setattr(release_backend_flatten, "_verify_archive", observe_verified_archive)

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
    assert "ENV PATH=/usr/local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin" in imports[0]
    assert any("LABEL ai-platform.source-commit=" + COMMIT in value for value in imports[0])
    assert not any(command[1] == "tag" for command, _ in docker.commands)
    assert all(CURRENT_REFERENCE not in command for command in _cleanup_commands(docker))
    assert any(command[1:3] == ["container", "rm"] for command in _cleanup_commands(docker))
    assert any(command[1:3] == ["image", "rm"] and bases[0] in command for command in _cleanup_commands(docker))
    exports = [
        (command, kwargs)
        for command, kwargs in docker.commands
        if command[1:3] == ["container", "export"]
    ]
    assert len(exports) == 2
    assert all("--output" not in command for command, _ in exports)
    assert all(kwargs["text"] is False and kwargs["stdout_sink"] is not None for _, kwargs in exports)
    assert len(docker.export_observations) == 2
    assert all(metadata.st_size > 0 and stat.S_ISREG(metadata.st_mode) for metadata in docker.export_observations)
    assert len(verified_archives) == 2
    for metadata, identity in verified_archives:
        assert metadata.st_dev == identity.st_dev
        assert metadata.st_ino == identity.st_ino
        assert metadata.st_size > 0
        assert stat.S_ISREG(metadata.st_mode)
        if os.name == "posix":
            assert stat.S_IMODE(metadata.st_mode) == 0o600
            assert metadata.st_uid == os.getuid()
    assert not list(tmp_path.iterdir())


def test_verify_archive_refuses_a_replaced_inode_after_streaming(tmp_path):
    archive, sink, identity = release_backend_flatten._create_archive_sink(tmp_path, "source-rootfs.tar")
    try:
        _write_rootfs_archive(sink)
        sink.flush()
        os.fsync(sink.fileno())
    finally:
        sink.close()

    archive.unlink()
    archive.write_bytes(b"replacement")
    if os.name == "posix":
        os.chmod(archive, 0o600)

    with pytest.raises(BackendFlattenError, match="archive is unsafe"):
        release_backend_flatten._verify_archive(archive, identity)


def test_flattened_backend_base_rejects_source_archive_replaced_after_export_before_import(tmp_path, monkeypatch):
    docker = _FakeDocker(tmp_path)
    original_export = release_backend_flatten._export_container_archive

    def replace_source_archive(**kwargs):
        archive = original_export(**kwargs)
        if kwargs["name"] == "source-rootfs.tar":
            _replace_archive(archive.path)
        return archive

    monkeypatch.setattr(release_backend_flatten, "_export_container_archive", replace_source_archive)

    with pytest.raises(BackendFlattenError, match="archive is unsafe"):
        with flattened_backend_base(
            docker=["docker"],
            source_reference=CURRENT_REFERENCE,
            expected_commit=COMMIT,
            expected_repository=REPOSITORY,
            archive_root=tmp_path,
            runner=docker,
        ):
            raise AssertionError("a replaced source archive must not reach target build")

    assert not any(command[1:3] == ["image", "import"] for command, _ in docker.commands)


def test_flattened_backend_base_rechecks_source_archive_after_import_returns(tmp_path, monkeypatch):
    docker = _FakeDocker(tmp_path)
    original_runner = docker.__call__
    original_binding_check = release_backend_flatten._assert_verified_archive_binding

    def replace_after_import(command, **kwargs):
        result = original_runner(command, **kwargs)
        return result

    def reject_replaced_after_import(archive, handle):
        if any(command[1:3] == ["image", "import"] for command, _ in docker.commands):
            raise BackendFlattenError("backend flatten archive is unsafe")
        return original_binding_check(archive, handle)

    monkeypatch.setattr(
        release_backend_flatten,
        "_assert_verified_archive_binding",
        reject_replaced_after_import,
    )

    with pytest.raises(BackendFlattenError, match="archive is unsafe"):
        with flattened_backend_base(
            docker=["docker"],
            source_reference=CURRENT_REFERENCE,
            expected_commit=COMMIT,
            expected_repository=REPOSITORY,
            archive_root=tmp_path,
            runner=replace_after_import,
        ):
            raise AssertionError("a replaced source archive must fail after import returns")

    assert any(command[1:3] == ["image", "import"] for command, _ in docker.commands)


def test_flattened_backend_base_rejects_validation_archive_replaced_before_rootfs_read(tmp_path, monkeypatch):
    docker = _FakeDocker(tmp_path)
    original_export = release_backend_flatten._export_container_archive

    def replace_validation_archive(**kwargs):
        archive = original_export(**kwargs)
        if kwargs["name"] == "flat-rootfs.tar":
            _replace_archive(archive.path)
        return archive

    monkeypatch.setattr(release_backend_flatten, "_export_container_archive", replace_validation_archive)
    monkeypatch.setattr(
        release_backend_flatten,
        "_archive_members",
        lambda *_: pytest.fail("replaced validation archive must not reach tarfile parsing"),
    )

    with pytest.raises(BackendFlattenError, match="archive is unsafe"):
        with flattened_backend_base(
            docker=["docker"],
            source_reference=CURRENT_REFERENCE,
            expected_commit=COMMIT,
            expected_repository=REPOSITORY,
            archive_root=tmp_path,
            runner=docker,
        ):
            raise AssertionError("a replaced validation archive must not reach target build")


@pytest.mark.parametrize(
    "path_value",
    [
        "relative/bin:/usr/bin",
        "/usr/local/bin:/tmp/contains space",
        "/usr/local/bin:=/tmp/injected",
        "/usr/local/bin:/tmp/contains\tcontrol",
    ],
)
def test_flattened_backend_base_rejects_noncanonical_path_before_import(tmp_path, path_value):
    source = _image_payload()
    source["Config"]["Env"][0] = f"PATH={path_value}"
    docker = _FakeDocker(tmp_path, source=source)

    with pytest.raises(BackendFlattenError, match="source config"):
        with flattened_backend_base(
            docker=["docker"],
            source_reference=CURRENT_REFERENCE,
            expected_commit=COMMIT,
            expected_repository=REPOSITORY,
            archive_root=tmp_path,
            runner=docker,
        ):
            raise AssertionError("unsafe PATH must not be yielded")

    assert not any(command[1:3] == ["image", "import"] for command, _ in docker.commands)


def test_flattened_backend_base_rejects_extra_flat_environment_key(tmp_path):
    flat = _image_payload(layers=1)
    flat["Config"]["Env"].append("EXTRA_RUNTIME_ENV=1")
    docker = _FakeDocker(tmp_path, flat=flat)

    with pytest.raises(BackendFlattenError, match="flat image config"):
        with flattened_backend_base(
            docker=["docker"],
            source_reference=CURRENT_REFERENCE,
            expected_commit=COMMIT,
            expected_repository=REPOSITORY,
            archive_root=tmp_path,
            runner=docker,
        ):
            raise AssertionError("flat image with an extra environment key must not be yielded")


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
    assert all("--output" not in command for command, _ in docker.commands)


@pytest.mark.parametrize("fail_stage", ["export", "import", "validate", "target-build"])
def test_flattened_backend_base_marks_primary_failure_when_cleanup_also_fails(tmp_path, fail_stage):
    docker = _FakeDocker(
        tmp_path,
        fail_stage=None if fail_stage == "target-build" else fail_stage,
        cleanup_nonzero=True,
    )

    with pytest.raises((BackendFlattenError, RuntimeError)) as error:
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

    assert getattr(error.value, "cleanup_status", None) == "failed"


def test_authority_stage_keeps_cleanup_failure_evidence_bounded(tmp_path):
    docker = _FakeDocker(tmp_path, fail_stage="export", cleanup_nonzero=True)
    events = []

    with pytest.raises(release_authority.ReleaseAuthorityError, match="backend-layer-flatten-recovery"):
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
                target_build=lambda _: None,
            ),
        )

    assert events[-1]["cleanup_status"] == "failed"
    assert events[-1]["backend_flatten_operation"] == "source_export"
    assert events[-1]["backend_flatten_error_code"] == "backend_flatten_source_export_failed"
    assert "ai-platform-flatten" not in json.dumps(events[-1])


def test_authority_stage_retains_safe_source_export_timeout_code_without_archive_path(tmp_path):
    docker = _FakeDocker(tmp_path)
    original_runner = docker.__call__

    def timeout_export(command, **kwargs):
        if list(command)[1:3] == ["container", "export"]:
            assert "--output" not in command
            assert kwargs["stdout_sink"] is not None
            error = subprocess.TimeoutExpired(["docker", "private-marker", str(tmp_path)], 17)
            error.safe_stderr_diagnostic = {"stderr_status": "redacted"}
            raise error
        return original_runner(command, **kwargs)

    events = []
    with pytest.raises(release_authority.ReleaseAuthorityError, match="backend-layer-flatten-recovery"):
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
                runner=timeout_export,
                target_build=lambda _: None,
            ),
        )

    event = events[-1]
    assert event["failure_kind"] == "timeout"
    assert event["backend_flatten_operation"] == "source_export"
    assert event["backend_flatten_error_code"] == "backend_flatten_source_export_failed"
    assert event["stderr_status"] == "redacted"
    assert "private-marker" not in json.dumps(event)
    assert str(tmp_path) not in json.dumps(event)


def test_flattened_backend_base_labels_import_called_process_error_with_import_evidence(tmp_path):
    docker = _FakeDocker(tmp_path, fail_stage="import")

    with pytest.raises(BackendFlattenError) as error:
        with flattened_backend_base(
            docker=["docker"],
            source_reference=CURRENT_REFERENCE,
            expected_commit=COMMIT,
            expected_repository=REPOSITORY,
            archive_root=tmp_path,
            runner=docker,
        ):
            raise AssertionError("a failed import must not yield a flat base")

    assert error.value.backend_flatten_operation == "import"
    assert error.value.backend_flatten_error_code == "backend_flatten_import_failed"
    assert error.value.safe_backend_flatten_evidence == {
        "backend_flatten_operation": "import",
        "backend_flatten_error_code": "backend_flatten_import_failed",
    }


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


def test_rebuild_from_flattened_backend_preserves_target_release_authority_error_with_safe_code(tmp_path):
    docker = _FakeDocker(tmp_path)
    target_error = release_authority.ReleaseAuthorityError("target build denied")

    def fail_target(_):
        raise target_error

    with pytest.raises(release_authority.ReleaseAuthorityError) as error:
        release_authority.rebuild_from_flattened_backend(
            docker=["docker"],
            source_reference=CURRENT_REFERENCE,
            expected_commit=COMMIT,
            expected_repository=REPOSITORY,
            archive_root=tmp_path,
            runner=docker,
            target_build=fail_target,
        )

    assert error.value is target_error
    assert error.value.backend_flatten_operation == "target_build"
    assert error.value.backend_flatten_error_code == "backend_flatten_target_build_failed"


def test_rebuild_from_flattened_backend_labels_target_called_process_error_with_safe_code(tmp_path):
    docker = _FakeDocker(tmp_path)
    target_error = subprocess.CalledProcessError(7, ["docker", "target-build"])

    def fail_target(_):
        raise target_error

    with pytest.raises(BackendFlattenError) as error:
        release_authority.rebuild_from_flattened_backend(
            docker=["docker"],
            source_reference=CURRENT_REFERENCE,
            expected_commit=COMMIT,
            expected_repository=REPOSITORY,
            archive_root=tmp_path,
            runner=docker,
            target_build=fail_target,
        )

    assert error.value.backend_flatten_operation == "target_build"
    assert error.value.backend_flatten_error_code == "backend_flatten_target_build_failed"
    assert error.value.safe_backend_flatten_evidence == {
        "backend_flatten_operation": "target_build",
        "backend_flatten_error_code": "backend_flatten_target_build_failed",
    }


def test_authority_stage_records_target_timeout_cleanup_evidence_in_one_bounded_event(tmp_path):
    docker = _FakeDocker(tmp_path, cleanup_nonzero=True)
    events = []

    def timeout_target(_):
        error = subprocess.TimeoutExpired(["docker", "private-marker", str(tmp_path)], 17)
        error.safe_stderr_diagnostic = {"stderr_status": "redacted"}
        raise error

    with pytest.raises(release_authority.ReleaseAuthorityError, match="backend-layer-flatten-recovery"):
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
                target_build=timeout_target,
            ),
        )

    event = events[-1]
    assert event["failure_kind"] == "timeout"
    assert event["backend_flatten_operation"] == "target_build"
    assert event["backend_flatten_error_code"] == "backend_flatten_target_build_failed"
    assert event["cleanup_status"] == "failed"
    assert "private-marker" not in json.dumps(event)
    assert str(tmp_path) not in json.dumps(event)


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


def test_deploy_main_cli_redacts_backend_flatten_pre_stage_error(monkeypatch, capsys, tmp_path):
    def fake_after_authority(*args, **kwargs):
        raise BackendFlattenError("C:/private-marker/backend-flatten")

    monkeypatch.setattr("tools.release_authority.assert_clean_coordination_source", lambda *args: None)
    monkeypatch.setattr("tools.release_authority._deploy_main_commit_after_authority", fake_after_authority)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "release_authority.py",
            "deploy-main-commit",
            "--release-root",
            str(tmp_path / "releases"),
            "--commit",
            COMMIT,
            "--strategy",
            "auto",
            "--allow-backend-layer-flatten-recovery",
        ],
    )

    assert release_authority.main() == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload == {
        "authority_commit": COMMIT,
        "command": "deploy-main-commit",
        "error": "backend layer flatten recovery failed",
        "verified": False,
    }
