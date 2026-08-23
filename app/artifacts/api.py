from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

_FORBIDDEN_ARTIFACT_MARKERS = ("/tmp/", "tenants/", "workspaces/", ":\\", ":/")
_FORBIDDEN_ARTIFACT_KEYS = {
    "storage_key",
    "local_path",
    "review_result",
    "artifact_path",
    "output_path",
    "runner",
    "runner_path",
    "executable_path",
    "cwd",
}


def sanitize_artifact_manifest(value: Any) -> Any:
    if isinstance(value, dict):
        cleaned = {}
        for key, item in value.items():
            if str(key).lower() in _FORBIDDEN_ARTIFACT_KEYS:
                continue
            sanitized = sanitize_artifact_manifest(item)
            if sanitized is not None:
                cleaned[key] = sanitized
        return cleaned
    if isinstance(value, list):
        cleaned_items = [sanitize_artifact_manifest(item) for item in value]
        return [item for item in cleaned_items if item is not None]
    if isinstance(value, str) and any(marker in value for marker in _FORBIDDEN_ARTIFACT_MARKERS):
        return None
    return value


def build_artifact_records(
    artifacts: Sequence[Any],
    *,
    new_id: Callable[[str], str],
) -> list[dict[str, Any]]:
    records = []
    for artifact in artifacts:
        artifact_id = new_id("art")
        records.append(
            {
                "id": artifact_id,
                "artifact_type": artifact.artifact_type,
                "label": artifact.label,
                "content_type": artifact.content_type,
                "storage_key": artifact.storage_key,
                "size_bytes": artifact.size_bytes,
                "download_url": f"/api/ai/artifacts/{artifact_id}/download",
                "manifest_json": artifact.manifest,
            }
        )
    return records


def public_artifact_records(artifact_records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "id": item["id"],
            "artifact_type": item["artifact_type"],
            "label": item["label"],
            "content_type": item["content_type"],
            "size_bytes": item["size_bytes"],
            "download_url": item["download_url"],
        }
        for item in artifact_records
    ]


def append_artifact_links(message: str, artifact_records: list[dict[str, Any]]) -> str:
    lines = []
    for line in message.splitlines():
        stripped = line.strip()
        if stripped.startswith(("详细报告:", "批注文档:")) and "/tmp/" in stripped:
            continue
        lines.append(line)
    base = "\n".join(lines).strip()
    if not artifact_records:
        return base
    links = [f"- {item['label']}: {item['download_url']}" for item in artifact_records]
    suffix = "输出文件:\n" + "\n".join(links)
    return f"{base}\n\n{suffix}" if base else suffix


__all__ = [
    "append_artifact_links",
    "build_artifact_records",
    "public_artifact_records",
    "sanitize_artifact_manifest",
]
