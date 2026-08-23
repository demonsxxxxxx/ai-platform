from __future__ import annotations

from typing import Any, Sequence

from app import repositories
from app.control_plane_contracts import artifact_lineage_contract, artifact_manifest_contract
from app.executors.base import ArtifactManifest
from app.streaming.v4 import append_artifact_ready_v4_row

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


def _sanitize_artifact_manifest(value: Any) -> Any:
    if isinstance(value, dict):
        cleaned = {}
        for key, item in value.items():
            if str(key).lower() in _FORBIDDEN_ARTIFACT_KEYS:
                continue
            sanitized = _sanitize_artifact_manifest(item)
            if sanitized is not None:
                cleaned[key] = sanitized
        return cleaned
    if isinstance(value, list):
        cleaned_items = [_sanitize_artifact_manifest(item) for item in value]
        return [item for item in cleaned_items if item is not None]
    if isinstance(value, str) and any(marker in value for marker in _FORBIDDEN_ARTIFACT_MARKERS):
        return None
    return value


def build_artifact_records(artifacts: Sequence[ArtifactManifest]) -> list[dict[str, Any]]:
    records = []
    for artifact in artifacts:
        artifact_id = repositories.new_id("art")
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


async def persist_ready_artifacts(
    conn: Any,
    *,
    tenant_id: str,
    run_id: str,
    trace_id: str,
    execution_lease_id: str,
    artifact_records: list[dict[str, Any]],
) -> None:
    for artifact in artifact_records:
        manifest_json = artifact_manifest_contract(
            artifact_type=artifact["artifact_type"],
            manifest=_sanitize_artifact_manifest(artifact["manifest_json"]),
        )
        lineage = artifact_lineage_contract(manifest_json, source_run_id=run_id)
        await repositories.create_artifact(
            conn,
            artifact_id=artifact["id"],
            tenant_id=tenant_id,
            run_id=run_id,
            artifact_type=artifact["artifact_type"],
            label=artifact["label"],
            content_type=artifact["content_type"],
            storage_key=artifact["storage_key"],
            size_bytes=artifact["size_bytes"],
            trace_id=trace_id,
            manifest_json=manifest_json,
        )
        await append_artifact_ready_v4_row(
            conn,
            tenant_id=tenant_id,
            run_id=run_id,
            artifact_id=artifact["id"],
            filename=artifact["label"],
            media_type=artifact["content_type"],
            size_bytes=artifact["size_bytes"],
            execution_lease_id=execution_lease_id,
            trace_ref=trace_id,
        )
        await repositories.append_event(
            conn,
            tenant_id=tenant_id,
            run_id=run_id,
            event_type="artifact_ready",
            stage="artifact",
            message="Artifact is ready",
            payload={
                "visible_to_user": True,
                "severity": "info",
                "artifact_id": artifact["id"],
                "artifact_type": artifact["artifact_type"],
                "download_url": artifact["download_url"],
                "lineage": lineage,
            },
        )
