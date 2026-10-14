"""Record an executor snapshot and its durable references in one caller transaction."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class ContextSnapshotRecorder:
    create_snapshot: Callable[..., Awaitable[dict[str, Any]]]
    bind_run_snapshot: Callable[..., Awaitable[None]]
    append_event: Callable[..., Awaitable[Any]]
    public_manifest: Callable[[dict[str, Any]], dict[str, Any]]
    schema_version: str

    async def record(
        self, conn, *, tenant_id: str, workspace_id: str, user_id: str,
        session_id: str, run_id: str, trace_id: str, source: str,
        included_message_ids: list[str], included_file_ids: list[str],
        included_artifact_ids: list[str], summary: dict[str, Any],
        memory_policy_summary: dict[str, Any],
        conversation_authority: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """Write snapshot, Run binding and private event on the same connection.

        The admitting caller owns commit/rollback together with Run creation.
        Failures propagate unchanged so no partial reference can be accepted.
        """
        snapshot = await self.create_snapshot(
            conn,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            user_id=user_id,
            session_id=session_id,
            run_id=run_id,
            trace_id=trace_id,
            context_kind="executor",
            included_message_ids=included_message_ids,
            included_file_ids=included_file_ids,
            included_artifact_ids=included_artifact_ids,
            included_memory_record_ids=[],
            redaction_summary_json={
                "input_payload_stored": False,
                "raw_skill_selector_stored": False,
                "long_term_memory_read": False,
                **memory_policy_summary,
            },
            payload_json=summary,
            conversation_authority_json=conversation_authority,
        )
        public_manifest = self.public_manifest(summary["context_manifest"])
        context_ref = {
            "schema_version": self.schema_version,
            "context_snapshot_id": snapshot["id"],
            "source": source,
            "message_count": len(included_message_ids),
            "file_count": len(included_file_ids),
            "memory_record_count": 0,
            "memory_policy": {
                "source": memory_policy_summary["memory_policy_source"],
                "memory_enabled": memory_policy_summary["memory_enabled"],
                "long_term_memory_enabled": memory_policy_summary["long_term_memory_enabled"],
                "retention_days": memory_policy_summary["retention_days"],
            },
            "referenced_materials": summary["referenced_materials"],
            "used_context_summary": summary["used_context_summary"],
            "latest_artifact_version": summary["latest_artifact_version"],
            "execution_tier": summary["execution_tier"],
            "context_pack_version": summary["context_pack_version"],
            "context_pack_generated_at": summary["context_pack_generated_at"],
            "context_manifest": public_manifest,
            "context_window": public_manifest["context_window"],
        }
        await self.bind_run_snapshot(
            conn,
            tenant_id=tenant_id,
            run_id=run_id,
            context_snapshot_id=str(snapshot["id"]),
            context_snapshot=context_ref,
        )
        await self.append_event(
            conn,
            tenant_id=tenant_id,
            run_id=run_id,
            trace_id=trace_id,
            event_type="context_snapshot_created",
            stage="context",
            message="已记录运行上下文快照",
            payload={
                "visible_to_user": False,
                **context_ref,
            },
        )
        return context_ref
