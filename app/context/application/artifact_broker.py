"""Short authorization transactions around a bounded, read-only object transfer."""

from app.context.application.artifact_transfer import (
    prepare_artifact_transfer,
    download_artifact_transfer,
)
from app.context.domain.artifact_transfer import ArtifactTransferError


async def broker_artifact_transfer(
    request,
    *,
    transaction,
    lock_authority,
    load_snapshot,
    retrieval_factory,
    download,
    tools,
    record_event,
):
    async def authorize(conn):
        run, lease = await lock_authority(
            conn,
            run_id=request.run_id,
            attempt_id=request.attempt_id,
            callback_token_id=request.callback_token_id,
            session_id=request.session_id,
        )
        if run.get("cancel_requested_at") is not None or str(
            run.get("status") or ""
        ).lower() in {"cancelling", "canceling"}:
            raise ArtifactTransferError("context_scope_denied")
        identity = {
            key: str(run.get(key) or "")
            for key in ("tenant_id", "workspace_id", "user_id", "agent_id")
        }
        identity.update(session_id=request.session_id, run_id=request.run_id)
        snapshot = await load_snapshot(
            conn, **{k: v for k, v in identity.items() if k != "agent_id"}
        )
        if not snapshot:
            raise ArtifactTransferError("context_snapshot_unavailable")
        payload = snapshot.get("payload_json") or {}
        if request.action not in tools(payload.get("context_manifest")):
            raise ArtifactTransferError("context_scope_denied")
        source = await prepare_artifact_transfer(
            retrieval_factory(conn), identity, request.arguments
        )
        # Full frozen snapshot and immutable lease identity, not just artifact membership.
        lease_identity = {
            k: lease.get(k)
            for k in (
                "id",
                "tenant_id",
                "workspace_id",
                "user_id",
                "run_id",
                "attempt_id",
                "provider",
                "runtime_container_id",
                "runtime_container_name",
                "runtime_executor_url",
                "runtime_workspace_container_path",
                "lease_payload_json",
            )
        }
        return identity, snapshot, lease_identity, source

    async with transaction() as conn:
        before = await authorize(conn)
    downloaded = await download_artifact_transfer(before[3], download)
    try:
        async with transaction() as conn:
            after = await authorize(conn)
            if after != before:
                raise ArtifactTransferError("context_scope_denied")
            await record_event(
                conn,
                tenant_id=before[0]["tenant_id"],
                run_id=request.run_id,
                event_type="context_retrieved",
                stage="context",
                message="Sandbox context retrieval completed",
                payload={
                    "action": request.action,
                    "result": "allowed",
                    "visible_to_user": False,
                },
            )
        return before[3], downloaded
    except BaseException:
        from pathlib import Path

        Path(downloaded.path).unlink(missing_ok=True)
        raise
