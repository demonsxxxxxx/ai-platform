"""Fail-closed, temporary flattening of a verified backend image for one rebuild."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import stat
import subprocess
import tarfile
import tempfile
from typing import Any, Callable, Iterator


BACKEND_LAYER_FLATTEN_MIN_LAYERS = 96
BACKEND_LAYER_FLATTEN_MAX_FLAT_LAYERS = 4
_FULL_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
_IMAGE_ID_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_UNSAFE_CONFIG_RE = re.compile(
    r"(?:authorization|password|passwd|secret|token|api[_-]?key|credential|private[ _-]?key)",
    re.IGNORECASE,
)
_RUNTIME_ENVIRONMENT_KEYS = (
    "PATH",
    "PYTHONDONTWRITEBYTECODE",
    "PYTHONUNBUFFERED",
    "PIP_DISABLE_PIP_VERSION_CHECK",
    "APP_MODULE",
    "APP_PORT",
    "HOME",
    "TMPDIR",
    "XDG_CACHE_HOME",
    "XDG_CONFIG_HOME",
    "XDG_DATA_HOME",
)
_REQUIRED_RUNTIME_ENVIRONMENT = {
    "PYTHONDONTWRITEBYTECODE": "1",
    "PYTHONUNBUFFERED": "1",
    "PIP_DISABLE_PIP_VERSION_CHECK": "1",
    "APP_MODULE": "app.main:create_app",
    "APP_PORT": "8020",
    "HOME": "/home/ai-platform",
    "TMPDIR": "/home/ai-platform/tmp",
    "XDG_CACHE_HOME": "/home/ai-platform/.cache",
    "XDG_CONFIG_HOME": "/home/ai-platform/.config",
    "XDG_DATA_HOME": "/home/ai-platform/.local/share",
}
_REQUIRED_LABELS = (
    "ai-platform.source-commit",
    "org.opencontainers.image.revision",
    "ai-platform.source-repository",
    "ai-platform.build-dirty",
    "ai-platform.release-role",
)
_MAX_MARKER_BYTES = 128 * 1024
_PATH_SEGMENT_RE = re.compile(r"^[A-Za-z0-9._+-]+$")


def release_label_dockerfile_lines(role: str) -> str:
    """Return all target-provenance labels required for one promoted image role."""
    common = [
        "LABEL org.opencontainers.image.revision=$AI_PLATFORM_BUILD_COMMIT",
        "LABEL ai-platform.source-revision=$AI_PLATFORM_BUILD_COMMIT",
        "LABEL ai-platform.source-commit=$AI_PLATFORM_BUILD_COMMIT",
        'LABEL ai-platform.build-dirty="$AI_PLATFORM_BUILD_DIRTY"',
        "LABEL ai-platform.source-repository=$AI_PLATFORM_BUILD_REPOSITORY",
        f"LABEL ai-platform.release-role={role}",
    ]
    if role == "backend":
        common[1:1] = [
            "LABEL ai-platform.runtime-subject=$AI_PLATFORM_BUILD_COMMIT",
            "LABEL ai-platform.source_revision=$AI_PLATFORM_BUILD_COMMIT",
            "LABEL ai-platform.source_commit=$AI_PLATFORM_BUILD_COMMIT",
            "LABEL ai-platform.runtime_subject=$AI_PLATFORM_BUILD_COMMIT",
            "LABEL ai-platform.source_tree_commit=$AI_PLATFORM_BUILD_COMMIT",
        ]
    return "\n".join(common)


def backend_provenance_dockerfile_run() -> str:
    """Return the backend embedded-source marker update used by source rebuilds and promotions."""
    return '''RUN printf '%s\\n' "$AI_PLATFORM_BUILD_COMMIT" > /app/.ai-platform-source-revision \\
    && printf '%s\\n' "$AI_PLATFORM_BUILD_COMMIT" > /app/.codex-source-revision \\
    && printf '%s\\n' "$AI_PLATFORM_BUILD_COMMIT" > /app/.source-commit \\
    && AI_PLATFORM_BUILD_COMMIT="$AI_PLATFORM_BUILD_COMMIT" AI_PLATFORM_BUILD_DIRTY="$AI_PLATFORM_BUILD_DIRTY" \\
       python -c "import json, os; from pathlib import Path; commit = os.environ.get('AI_PLATFORM_BUILD_COMMIT', 'unknown').strip() or 'unknown'; dirty_text = os.environ.get('AI_PLATFORM_BUILD_DIRTY', 'unknown').strip().lower(); dirty = dirty_text != 'false'; dirty_paths = [] if not dirty else ['unknown_runtime_affecting_dirty_paths']; payload = dict(schema_version='ai-platform.source-snapshot.v1', source_tree_commit_sha=commit, runtime_subject_commit_sha=commit, source_tree_dirty=dirty, runtime_affecting_changes_since_runtime_subject=[], runtime_affecting_dirty_paths=dirty_paths, snapshot_source='dockerfile_build_args'); Path('/app/.ai-platform-source-snapshot.json').write_text(json.dumps(payload, indent=2, sort_keys=True) + '\\n', encoding='utf-8')"'''


def backend_runtime_dockerfile() -> str:
    """Build source-only backend runtime from a verified image with no dependency installer command."""
    labels = release_label_dockerfile_lines("backend")
    marker = backend_provenance_dockerfile_run()
    return f"""ARG BASE_IMAGE
FROM ${{BASE_IMAGE}}
ARG AI_PLATFORM_BUILD_COMMIT
ARG AI_PLATFORM_BUILD_DIRTY
ARG AI_PLATFORM_BUILD_REPOSITORY
USER root
RUN rm -rf /app/app /app/tools /app/scripts /app/skills /app/docs/release-evidence \\
    && rm -f /app/docker-entrypoint.sh /app/.ai-platform-source-revision \\
       /app/.codex-source-revision /app/.source-commit /app/.ai-platform-source-snapshot.json
COPY app /app/app
COPY tools /app/tools
COPY scripts /app/scripts
COPY skills /app/skills
COPY docs/release-evidence /app/docs/release-evidence
COPY docker-entrypoint.sh /app/docker-entrypoint.sh
RUN chmod -R a+rX /app && chmod 0755 /app/docker-entrypoint.sh
{labels}
{marker}
USER 10001:10001
"""


def promotion_dockerfile(role: str) -> str:
    """Build a provenance-only image from a verified local role image without dependency commands."""
    labels = release_label_dockerfile_lines(role)
    if role == "backend":
        marker = backend_provenance_dockerfile_run()
        user = "USER 10001:10001"
    elif role == "frontend":
        marker = (
            'RUN sed -i "s/\\\"commit\\\": \\\"[^\\\"]*\\\"/\\\"commit\\\": '
            '\\\"${AI_PLATFORM_BUILD_COMMIT}\\\"/" '
            "/usr/share/nginx/html/ai-platform-build-provenance.json"
        )
        user = ""
    else:
        raise BackendFlattenError("release role is invalid")
    return f"""ARG BASE_IMAGE
FROM ${{BASE_IMAGE}}
ARG AI_PLATFORM_BUILD_COMMIT
ARG AI_PLATFORM_BUILD_DIRTY
ARG AI_PLATFORM_BUILD_REPOSITORY
USER root
{labels}
{marker}
{user}
"""


class BackendFlattenError(RuntimeError):
    """Raised when a temporary backend flatten operation cannot prove its invariants."""


@dataclass(frozen=True)
class FlattenedBackendBase:
    """One verified non-canonical image reference, valid only inside its context."""

    reference: str
    source_layer_count: int
    flat_layer_count: int


@dataclass(frozen=True)
class _FlattenConfig:
    environment: tuple[tuple[str, str], ...]
    labels: tuple[tuple[str, str], ...]


@dataclass
class _TemporarySubjects:
    source_container: str | None = None
    validation_container: str | None = None
    flat_reference: str | None = None


Runner = Callable[..., subprocess.CompletedProcess[Any]]


def _safe_text(value: Any) -> str:
    if not isinstance(value, str) or not value or "\x00" in value or "\n" in value or "\r" in value:
        raise BackendFlattenError("backend flatten source config is unsafe")
    if _UNSAFE_CONFIG_RE.search(value):
        raise BackendFlattenError("backend flatten source config is unsafe")
    return value


def _normalize_commit(value: str) -> str:
    if not isinstance(value, str) or not _FULL_COMMIT_RE.fullmatch(value):
        raise BackendFlattenError("backend flatten commit is invalid")
    return value


def validate_backend_layer_flatten_recovery_request(
    *, enabled: bool, strategy: str, backend_action: str | None
) -> None:
    """Reject every recovery request except an explicit auto runtime rebuild."""
    if not enabled:
        return
    if strategy != "auto":
        raise BackendFlattenError("backend layer flatten recovery requires the auto strategy")
    if backend_action != "runtime-rebuild":
        raise BackendFlattenError(
            "backend layer flatten recovery requires a backend runtime-rebuild plan action"
        )


def _image_payload(runner: Runner, docker: list[str], reference: str) -> dict[str, Any]:
    try:
        result = runner([*docker, "image", "inspect", reference])
        payload = json.loads(result.stdout)
    except (OSError, subprocess.SubprocessError, ValueError, TypeError):
        raise BackendFlattenError("backend flatten image inspection failed") from None
    if not isinstance(payload, list) or len(payload) != 1 or not isinstance(payload[0], dict):
        raise BackendFlattenError("backend flatten image metadata is invalid")
    return payload[0]


def _layers(image: dict[str, Any], *, flat: bool) -> int:
    rootfs = image.get("RootFS")
    layers = rootfs.get("Layers") if isinstance(rootfs, dict) else None
    if not isinstance(layers, list) or not all(isinstance(layer, str) and layer for layer in layers):
        raise BackendFlattenError("backend flatten image layer metadata is invalid")
    count = len(layers)
    if flat:
        if count < 1 or count > BACKEND_LAYER_FLATTEN_MAX_FLAT_LAYERS:
            raise BackendFlattenError("backend flatten flat image layer count is invalid")
    elif count < BACKEND_LAYER_FLATTEN_MIN_LAYERS:
        raise BackendFlattenError("backend flatten source image does not meet the layer threshold")
    return count


def _environment_mapping(config: dict[str, Any]) -> dict[str, str]:
    raw_environment = config.get("Env")
    if not isinstance(raw_environment, list):
        raise BackendFlattenError("backend flatten source config is invalid")
    values: dict[str, str] = {}
    for item in raw_environment:
        item = _safe_text(item)
        key, separator, value = item.partition("=")
        if not separator or not re.fullmatch(r"[A-Z][A-Z0-9_]*", key) or key in values:
            raise BackendFlattenError("backend flatten source config is invalid")
        _safe_text(key)
        _safe_text(value)
        values[key] = value
    return values


def _is_canonical_path(value: str) -> bool:
    """Accept only non-empty colon-separated absolute path components."""
    return bool(value) and all(
        component.startswith("/")
        and all(segment not in {"", ".", ".."} and _PATH_SEGMENT_RE.fullmatch(segment) for segment in component[1:].split("/"))
        for component in value.split(":")
    )


def _validated_flatten_config(
    image: dict[str, Any],
    *,
    commit: str,
    repository: str,
    flat: bool,
) -> _FlattenConfig:
    image_id = image.get("Id")
    if not isinstance(image_id, str) or not _IMAGE_ID_RE.fullmatch(image_id):
        raise BackendFlattenError("backend flatten image ID is invalid")
    config = image.get("Config")
    if not isinstance(config, dict):
        raise BackendFlattenError("backend flatten image config is invalid")
    if config.get("Volumes") not in (None, {}):
        raise BackendFlattenError("backend flatten image config declares a mount")
    if config.get("User") != "10001:10001":
        raise BackendFlattenError("backend flatten flat image config is invalid" if flat else "backend flatten source config is invalid")
    if config.get("WorkingDir") != "/app":
        raise BackendFlattenError("backend flatten flat image config is invalid" if flat else "backend flatten source config is invalid")
    if config.get("Entrypoint") != ["/app/docker-entrypoint.sh"] or config.get("Cmd") != ["uvicorn"]:
        raise BackendFlattenError("backend flatten flat image config is invalid" if flat else "backend flatten source config is invalid")
    exposed = config.get("ExposedPorts")
    if not isinstance(exposed, dict) or set(exposed) != {"8020/tcp"}:
        raise BackendFlattenError("backend flatten flat image config is invalid" if flat else "backend flatten source config is invalid")
    environment = _environment_mapping(config)
    expected_environment_keys = set(_RUNTIME_ENVIRONMENT_KEYS)
    if (set(environment) != expected_environment_keys) if flat else (expected_environment_keys - set(environment)):
        raise BackendFlattenError("backend flatten flat image config is invalid" if flat else "backend flatten source config is invalid")
    for key, expected in _REQUIRED_RUNTIME_ENVIRONMENT.items():
        if environment.get(key) != expected:
            raise BackendFlattenError("backend flatten source config is invalid")
    path_value = environment.get("PATH")
    if path_value is None or not _is_canonical_path(path_value):
        raise BackendFlattenError("backend flatten source config is invalid")
    labels = config.get("Labels")
    if not isinstance(labels, dict):
        raise BackendFlattenError("backend flatten image provenance is invalid")
    expected_labels = {
        "ai-platform.source-commit": commit,
        "org.opencontainers.image.revision": commit,
        "ai-platform.source-repository": repository,
        "ai-platform.build-dirty": "false",
        "ai-platform.release-role": "backend",
    }
    for label, expected in expected_labels.items():
        if labels.get(label) != expected:
            raise BackendFlattenError("backend flatten image provenance is invalid")
    for label, value in labels.items():
        _safe_text(label)
        _safe_text(value)
    return _FlattenConfig(
        environment=tuple((key, environment[key]) for key in _RUNTIME_ENVIRONMENT_KEYS),
        labels=tuple((key, expected_labels[key]) for key in _REQUIRED_LABELS),
    )


def _import_changes(config: _FlattenConfig) -> list[str]:
    changes = [
        "--change",
        "USER 10001:10001",
        "--change",
        "WORKDIR /app",
        "--change",
        'ENTRYPOINT ["/app/docker-entrypoint.sh"]',
        "--change",
        'CMD ["uvicorn"]',
        "--change",
        "EXPOSE 8020",
    ]
    for key, value in config.environment:
        changes.extend(("--change", f"ENV {key}={value}"))
    for key, value in config.labels:
        changes.extend(("--change", f"LABEL {key}={value}"))
    return changes


def _create_archive_path(directory: Path, name: str) -> Path:
    archive = directory / name
    descriptor = os.open(archive, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    os.close(descriptor)
    os.chmod(archive, 0o600)
    return archive


def _verify_archive(path: Path) -> None:
    try:
        metadata = path.stat(follow_symlinks=False)
    except OSError:
        raise BackendFlattenError("backend flatten archive is unavailable") from None
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_size <= 0
        or (os.name == "posix" and stat.S_IMODE(metadata.st_mode) != 0o600)
    ):
        raise BackendFlattenError("backend flatten archive is unsafe")
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            while True:
                chunk = handle.read(128 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
    except OSError:
        raise BackendFlattenError("backend flatten archive is unavailable") from None
    if not digest.hexdigest():
        raise BackendFlattenError("backend flatten archive checksum failed")


def _member_content(archive: tarfile.TarFile, member: tarfile.TarInfo) -> str:
    if member.size < 0 or member.size > _MAX_MARKER_BYTES:
        raise BackendFlattenError("backend flatten marker validation failed")
    handle = archive.extractfile(member)
    if handle is None:
        raise BackendFlattenError("backend flatten marker validation failed")
    try:
        return handle.read(_MAX_MARKER_BYTES + 1).decode("utf-8")
    except (OSError, UnicodeDecodeError):
        raise BackendFlattenError("backend flatten marker validation failed") from None


def _archive_members(archive: tarfile.TarFile) -> dict[str, tarfile.TarInfo]:
    members: dict[str, tarfile.TarInfo] = {}
    for member in archive.getmembers():
        name = member.name.lstrip("./")
        if not name or name.startswith("/") or ".." in Path(name).parts or name in members:
            raise BackendFlattenError("backend flatten archive is unsafe")
        members[name] = member
    return members


def _verify_flat_rootfs(path: Path, *, commit: str) -> None:
    try:
        with tarfile.open(path, "r") as archive:
            members = _archive_members(archive)
            for marker in (
                "app/.ai-platform-source-revision",
                "app/.codex-source-revision",
                "app/.source-commit",
            ):
                member = members.get(marker)
                if member is None or not member.isfile() or _member_content(archive, member) != f"{commit}\n":
                    raise BackendFlattenError("backend flatten marker validation failed")
            snapshot_member = members.get("app/.ai-platform-source-snapshot.json")
            if snapshot_member is None or not snapshot_member.isfile():
                raise BackendFlattenError("backend flatten marker validation failed")
            snapshot = json.loads(_member_content(archive, snapshot_member))
            if not isinstance(snapshot, dict) or (
                snapshot.get("schema_version") != "ai-platform.source-snapshot.v1"
                or snapshot.get("source_tree_commit_sha") != commit
                or snapshot.get("runtime_subject_commit_sha") != commit
                or snapshot.get("source_tree_dirty") is not False
            ):
                raise BackendFlattenError("backend flatten marker validation failed")
            entrypoint = members.get("app/docker-entrypoint.sh")
            if entrypoint is None or not entrypoint.isfile() or stat.S_IMODE(entrypoint.mode) != 0o755:
                raise BackendFlattenError("backend flatten entrypoint validation failed")
            for executable in ("usr/local/bin/python", "usr/local/bin/uvicorn"):
                member = members.get(executable)
                if member is None or not (member.isfile() or member.issym() or member.islnk()):
                    raise BackendFlattenError("backend flatten runtime executable validation failed")
                if member.isfile() and not (member.mode & stat.S_IXUSR):
                    raise BackendFlattenError("backend flatten runtime executable validation failed")
            passwd = members.get("etc/passwd")
            group = members.get("etc/group")
            if (
                passwd is None
                or group is None
                or not passwd.isfile()
                or not group.isfile()
                or "ai-platform:x:10001:10001:" not in _member_content(archive, passwd)
                or "ai-platform:x:10001:" not in _member_content(archive, group)
            ):
                raise BackendFlattenError("backend flatten runtime identity validation failed")
    except (OSError, tarfile.TarError, json.JSONDecodeError):
        raise BackendFlattenError("backend flatten rootfs validation failed") from None


def _cleanup(runner: Runner, docker: list[str], temporary: _TemporarySubjects) -> bool:
    failed = False
    for container in (temporary.validation_container, temporary.source_container):
        if container is None:
            continue
        try:
            result = runner([*docker, "container", "rm", "-f", container], check=False)
            if result.returncode != 0:
                failed = True
        except (OSError, subprocess.SubprocessError):
            failed = True
    if temporary.flat_reference is not None:
        try:
            result = runner([*docker, "image", "rm", "-f", temporary.flat_reference], check=False)
            if result.returncode != 0:
                failed = True
        except (OSError, subprocess.SubprocessError):
            failed = True
    return failed


@contextmanager
def flattened_backend_base(
    *,
    docker: list[str],
    source_reference: str,
    expected_commit: str,
    expected_repository: str,
    archive_root: Path,
    runner: Runner,
) -> Iterator[FlattenedBackendBase]:
    """Yield one validated flat base and remove every temporary subject on exit.

    The only container exports in this flow come from authority-created, stopped
    containers without runtime environment or mounts. The current tag and running
    containers are never inspected, retagged, or mutated.
    """
    commit = _normalize_commit(expected_commit)
    repository = _safe_text(expected_repository)
    if source_reference != f"ai-platform:{commit}":
        raise BackendFlattenError("backend flatten source reference is not canonical")
    root = Path(archive_root)
    if not root.is_absolute() or not root.is_dir() or root.is_symlink():
        raise BackendFlattenError("backend flatten managed archive root is invalid")
    nonce = secrets.token_hex(12)
    temporary = _TemporarySubjects(
        source_container=f"ai-platform-flatten-source-{nonce}",
        validation_container=f"ai-platform-flatten-validate-{nonce}",
        flat_reference=f"ai-platform:flatten-base-{commit[:12]}-{nonce}",
    )
    primary_error: BaseException | None = None
    try:
        source_image = _image_payload(runner, docker, source_reference)
        source_layers = _layers(source_image, flat=False)
        config = _validated_flatten_config(
            source_image,
            commit=commit,
            repository=repository,
            flat=False,
        )
        with tempfile.TemporaryDirectory(prefix="ai-platform-flatten-", dir=root) as directory_name:
            directory = Path(directory_name)
            source_archive = _create_archive_path(directory, "source-rootfs.tar")
            runner([*docker, "container", "create", "--name", temporary.source_container, source_reference])
            runner([*docker, "container", "export", "--output", str(source_archive), temporary.source_container])
            _verify_archive(source_archive)
            runner(
                [
                    *docker,
                    "image",
                    "import",
                    *_import_changes(config),
                    str(source_archive),
                    temporary.flat_reference,
                ]
            )
            flat_image = _image_payload(runner, docker, temporary.flat_reference)
            flat_layers = _layers(flat_image, flat=True)
            _validated_flatten_config(
                flat_image,
                commit=commit,
                repository=repository,
                flat=True,
            )
            validation_archive = _create_archive_path(directory, "flat-rootfs.tar")
            runner([*docker, "container", "create", "--name", temporary.validation_container, temporary.flat_reference])
            runner(
                [*docker, "container", "export", "--output", str(validation_archive), temporary.validation_container]
            )
            _verify_archive(validation_archive)
            _verify_flat_rootfs(validation_archive, commit=commit)
            yield FlattenedBackendBase(
                reference=temporary.flat_reference,
                source_layer_count=source_layers,
                flat_layer_count=flat_layers,
            )
    except BackendFlattenError as exc:
        primary_error = exc
        raise
    except (OSError, subprocess.SubprocessError, ValueError, TypeError):
        primary_error = BackendFlattenError("backend layer flatten recovery failed")
        raise primary_error from None
    except BaseException as exc:
        primary_error = exc
        raise
    finally:
        cleanup_failed = _cleanup(runner, docker, temporary)
        if cleanup_failed:
            if primary_error is not None:
                setattr(primary_error, "cleanup_status", "failed")
            else:
                cleanup_error = BackendFlattenError("backend layer flatten cleanup failed")
                cleanup_error.cleanup_status = "failed"
                raise cleanup_error


def rebuild_from_flattened_backend(
    *,
    docker: list[str],
    source_reference: str,
    expected_commit: str,
    expected_repository: str,
    archive_root: Path,
    runner: Runner,
    target_build: Callable[[str], None],
) -> None:
    """Run one target build while its verified temporary flat base is available."""
    with flattened_backend_base(
        docker=docker,
        source_reference=source_reference,
        expected_commit=expected_commit,
        expected_repository=expected_repository,
        archive_root=archive_root,
        runner=runner,
    ) as flattened:
        target_build(flattened.reference)
