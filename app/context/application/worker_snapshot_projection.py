"""Project a scoped Context snapshot row for Worker dispatch."""

from collections.abc import Callable
from typing import Any


def _included_count(row: dict[str, Any], field: str, payload: dict[str, Any], payload_field: str) -> int:
    raw = row.get(field)
    if isinstance(raw, list):
        return len(raw)
    try:
        return int(payload.get(payload_field) or 0)
    except (TypeError, ValueError):
        return 0


def _safe_context_memory_policy(raw: object) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    source = str(raw.get("source") or "default").strip()
    if source not in {"default", "stored", "not_recorded"}:
        source = "stored"
    try:
        retention_days = int(raw.get("retention_days") or 90)
    except (TypeError, ValueError):
        retention_days = 90
    if retention_days <= 0:
        retention_days = 90
    return {
        "source": source,
        "memory_enabled": bool(raw.get("memory_enabled", True)),
        "long_term_memory_enabled": False,
        "retention_days": retention_days,
    }


def project_worker_snapshot_ref(
    row: dict[str, Any],
    *,
    public_provenance: Callable[..., dict[str, Any]],
    sanitize_manifest: Callable[..., dict[str, Any]],
    snapshot_schema_version: str,
    manifest_schema_version: str,
) -> dict[str, Any]:
    payload = row.get("payload_json") if isinstance(row.get("payload_json"), dict) else {}
    public_payload = public_provenance(
        payload,
        source="stored_context_snapshot",
        message_count=_included_count(row, "included_message_ids", payload, "message_count"),
        file_count=_included_count(row, "included_file_ids", payload, "file_count"),
        artifact_count=_included_count(row, "included_artifact_ids", payload, "artifact_count"),
        memory_record_count=_included_count(row, "included_memory_record_ids", payload, "memory_record_count"),
        memory_policy_source="not_recorded",
        long_term_memory_read=False,
        preserve_stored_input_keys=True,
    )
    context_ref: dict[str, Any] = {
        "schema_version": str(row.get("schema_version") or payload.get("schema_version") or snapshot_schema_version),
        "context_snapshot_id": str(row["id"]),
        "source": public_payload["used_context_summary"]["source"],
        "message_count": public_payload["referenced_materials"]["message_count"],
        "file_count": public_payload["referenced_materials"]["file_count"],
        "memory_record_count": public_payload["referenced_materials"]["memory_record_count"],
        "referenced_materials": public_payload["referenced_materials"],
        "used_context_summary": public_payload["used_context_summary"],
        "latest_artifact_version": public_payload["latest_artifact_version"],
        "execution_tier": public_payload["execution_tier"],
        "context_pack_version": public_payload["context_pack_version"],
        "context_pack_generated_at": public_payload["context_pack_generated_at"],
    }
    memory_policy = _safe_context_memory_policy(payload.get("memory_policy"))
    if memory_policy is not None:
        context_ref["memory_policy"] = memory_policy
    context_manifest = payload.get("context_manifest")
    if isinstance(context_manifest, dict) and context_manifest.get("schema_version") == manifest_schema_version:
        context_ref["context_manifest"] = sanitize_manifest(context_manifest)
    return context_ref
