"""Artifact records and reservation promotion for one Worker Attempt."""

from collections.abc import Callable, Iterable
from typing import Any

from app.artifacts.domain.manifest_projection import sanitize_artifact_manifest


def build_artifact_records(
    artifacts: Iterable[Any],
    reconciliation: bool,
    new_id: Callable[[str], str],
    download_url: Callable[[str], str],
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for artifact in artifacts:
        if reconciliation and not artifact.provisional_cleanup_id:
            raise ValueError("executor_reconciliation_artifact_cleanup_receipt_missing")
        artifact_id = new_id("art")
        records.append({
            "id": artifact_id,
            "artifact_type": artifact.artifact_type,
            "label": artifact.label,
            "content_type": artifact.content_type,
            "storage_key": artifact.storage_key,
            "size_bytes": artifact.size_bytes,
            "download_url": download_url(artifact_id),
            "manifest_json": artifact.manifest,
            "provisional_cleanup_id": artifact.provisional_cleanup_id,
        })
    return records


async def promote_artifact_reservations(
    conn: Any,
    artifacts: list[dict[str, Any]],
    payload: Any,
    promote_cleanup: Callable[..., Any],
) -> None:
    for artifact in artifacts:
        cleanup_id = artifact["provisional_cleanup_id"]
        if cleanup_id is None:
            continue
        promoted = await promote_cleanup(
            conn,
            artifact_id=str(cleanup_id),
            tenant_id=payload.tenant_id,
            run_id=payload.run_id,
            storage_key=artifact["storage_key"],
        )
        if not promoted:
            raise RuntimeError("executor_artifact_cleanup_receipt_lost")


async def persist_worker_artifacts(
    conn: Any,
    artifact_records: list[dict[str, Any]],
    payload: Any,
    *,
    trace_id: str,
    promote_cleanup: Callable[..., Any],
    manifest_contract: Callable[..., dict[str, Any]],
    lineage_contract: Callable[..., dict[str, Any]],
    create_artifact: Callable[..., Any],
    append_user_event: Callable[..., Any],
) -> None:
    await promote_artifact_reservations(conn, artifact_records, payload, promote_cleanup)
    for artifact in artifact_records:
        manifest_json = manifest_contract(
            artifact_type=artifact["artifact_type"],
            manifest=sanitize_artifact_manifest(artifact["manifest_json"]),
        )
        lineage = lineage_contract(manifest_json, source_run_id=payload.run_id)
        await create_artifact(
            conn,
            artifact_id=artifact["id"],
            tenant_id=payload.tenant_id,
            run_id=payload.run_id,
            artifact_type=artifact["artifact_type"],
            label=artifact["label"],
            content_type=artifact["content_type"],
            storage_key=artifact["storage_key"],
            size_bytes=artifact["size_bytes"],
            trace_id=trace_id,
            manifest_json=manifest_json,
        )
        await append_user_event(
            conn,
            tenant_id=payload.tenant_id,
            run_id=payload.run_id,
            event_type="artifact_ready",
            stage="artifact",
            message="Artifact is ready",
            payload={
                "artifact_id": artifact["id"],
                "artifact_type": artifact["artifact_type"],
                "download_url": artifact["download_url"],
                "lineage": lineage,
            },
        )
