"""Immutable artifact transfer metadata and limits (not upload/preview limits)."""

from dataclasses import dataclass
from pathlib import PurePosixPath
import re


ARTIFACT_JSON_COMPAT_MAX_BYTES = 16 * 1024 * 1024
ARTIFACT_TRANSFER_CHUNK_BYTES = 64 * 1024
ARTIFACT_TRANSFER_HEADER = "X-Context-Artifact"


ARTIFACT_TRANSFER_SAFE_ERRORS = frozenset(
    {
        "context_artifact_too_large",
        "context_artifact_size_required",
        "context_artifact_identity_mismatch",
        "context_artifact_unavailable",
        "context_artifact_target_conflict",
        "context_artifact_name_invalid",
        "context_artifact_safe_filesystem_required",
    }
)


class ArtifactTransferError(ValueError):
    pass


@dataclass(frozen=True)
class ArtifactTransferSource:
    artifact_id: str
    name: str
    source_run_id: str
    storage_key: str
    size_bytes: int
    expected_sha256: str | None
    max_bytes: int

    @classmethod
    def from_row(cls, row: dict, *, artifact_id: str, max_bytes: int):
        cap = max_bytes
        size = row.get("size_bytes")
        if type(size) is not int or size < 0:
            raise ArtifactTransferError("context_artifact_size_required")
        if size > cap:
            raise ArtifactTransferError("context_artifact_too_large")
        storage_key = str(row.get("storage_key") or "")
        if not storage_key:
            raise ArtifactTransferError("context_artifact_unavailable")
        name = str(
            row.get("original_name")
            or row.get("label")
            or row.get("name")
            or "context.bin"
        )
        name = name.replace("\\", "/").rsplit("/", 1)[-1]
        validate_artifact_filename(name)
        return cls(
            artifact_id,
            name,
            str(row.get("run_id") or ""),
            storage_key,
            size,
            expected_artifact_sha256(row),
            cap,
        )

    def metadata(self, *, sha256: str) -> dict:
        return {
            "artifact_id": self.artifact_id,
            "name": self.name,
            "source_run_id": self.source_run_id,
            "size_bytes": self.size_bytes,
            "sha256": sha256,
            "max_bytes": self.max_bytes,
        }


def expected_artifact_sha256(row: dict) -> str | None:
    manifest = row.get("manifest_json")
    digests = [row.get("sha256")]
    if isinstance(manifest, dict):
        digests.extend([manifest.get("sha256"), manifest.get("content_sha256")])
    declared = [str(value).lower() for value in digests if value is not None]
    if (
        any(not re.fullmatch(r"[0-9a-f]{64}", value) for value in declared)
        or len(set(declared)) > 1
    ):
        raise ArtifactTransferError("context_artifact_identity_mismatch")
    return declared[0] if declared else None


def validate_artifact_filename(name: str) -> None:
    if (
        not name
        or name in {".", ".."}
        or PurePosixPath(name).name != name
        or any(ord(c) < 32 or c in '\\/:*?"<>|' for c in name)
        or len(name.encode("utf-8")) > 255
    ):
        raise ArtifactTransferError("context_artifact_name_invalid")


def artifact_workspace_parts(artifact_id: str, name: str) -> tuple[str, ...]:
    # IDs are not lossy-sanitized: two source identities must never alias a path.
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", artifact_id):
        raise ArtifactTransferError("context_artifact_identity_mismatch")
    validate_artifact_filename(name)
    return "context", artifact_id, name
