"""Compose stopped native epochs with private host-bind workspace versions."""
from __future__ import annotations

from pathlib import Path
from functools import partial
from types import SimpleNamespace
from typing import Any

from app.context import api as context_api
from app.execution.api import ContinuationSettlementPorts
from app.platform.postgres.sandbox_leases import release_sandbox_lease
from app.platform.storage_io_completion import StorageIOCompletion
from app.sandbox import api as sandbox_api
from app.sandbox.infrastructure import workspace_versions
from app.settings import get_settings
from app.storage import run_storage_io
from app.runtime.sandbox.workspace_manager import SandboxWorkspaceManager, reset_worker_workspace


async def _workspace_io(operation: Any, **kwargs: Any) -> dict[str, Any]:
    completion = StorageIOCompletion()
    try:
        return await run_storage_io(
            operation, completion=completion, timeout_seconds=None, **kwargs,
        )
    except BaseException:
        # Keep the Attempt owner until copy/flock/fsync has physically finished.
        await completion.settle()
        raise


async def _save_stopped_workspace(*, request: Any, workspace_lease: Any,
                                  stop_evidence: str) -> dict[str, Any]:
    evidence = {
        "state": stop_evidence,
        "scope": {key: getattr(request, key) for key in (
            "tenant_id", "workspace_id", "user_id", "session_id", "run_id", "attempt_id",
        )},
        "owner_generation": request.owner_generation,
    }
    return await _workspace_io(
        sandbox_api.save_workspace_version,
        request=request, workspace_lease=workspace_lease, stop_evidence=evidence,
        storage=partial(workspace_versions.save_workspace_version,
                        workspace_root=get_settings().sandbox_workspace_root),
    )


async def verify_checkpoint_workspace(*, request: Any, receipt: dict[str, Any]) -> dict[str, Any]:
    return await _workspace_io(
        sandbox_api.verify_workspace_version, request=request, receipt=receipt,
        storage=partial(workspace_versions.verify_workspace_version,
                        workspace_root=get_settings().sandbox_workspace_root),
    )


async def _settle_stopped_continuation(conn: Any, **settlement: Any) -> dict[str, Any]:
    sdk = dict(settlement["sdk_settlement"])
    cursor = await conn.execute(
        """
        select attempt.owner_generation, epoch.writer_owner_generation,
               exists (select 1 from provider_session_entries entry
                       join provider_session_append_receipts ack on ack.epoch_id = entry.epoch_id
                         and entry.sequence between ack.expected_sequence and ack.last_sequence
                       where entry.epoch_id = epoch.id and entry.subpath = ''
                         and ack.run_id = owning_run.id and ack.attempt_id = attempt.id) as has_current_main,
               (select max(last_sequence) from provider_session_append_receipts
                where epoch_id = epoch.id) as recorded_boundary
        from runs owning_run
        join run_attempts attempt on attempt.tenant_id = owning_run.tenant_id
          and attempt.run_id = owning_run.id and attempt.id = %s
        join provider_session_epochs epoch on epoch.tenant_id = owning_run.tenant_id
          and epoch.workspace_id = owning_run.workspace_id and epoch.user_id = owning_run.user_id
          and epoch.session_id = owning_run.session_id and epoch.agent_id = owning_run.agent_id
          and epoch.id = attempt.execution_spec_json->'context_pack'->'conversation_context'->>'provider_epoch_id'
        where owning_run.tenant_id = %s and owning_run.id = %s
        for update of owning_run, attempt, epoch
        """, (settlement["attempt_id"], settlement["tenant_id"], settlement["run_id"]),
    )
    owner = await cursor.fetchone()
    if owner is None:
        raise ValueError("continuation_writer_binding_invalid")
    sdk["owner_generation"] = owner["owner_generation"]
    if type(sdk.get("input_accepted")) is not bool:
        sdk["input_accepted"] = owner["has_current_main"] is True
        sdk["ack_unknown"] = owner["has_current_main"] is not True
    final_sequence = settlement.get("final_sequence")
    if final_sequence is None and owner["has_current_main"] is True:
        # Stop + locked committed append receipts reconcile the host's durable prefix.
        final_sequence = owner["recorded_boundary"]
        sdk["mirror_complete"] = False
        sdk["ack_unknown"] = False
    if owner["writer_owner_generation"] is not None:
        sdk["writer_owner_generation"] = owner["writer_owner_generation"]
    receipt = settlement.get("workspace_receipt")
    # Unsaved is absence of a receipt, never a malformed saved receipt.
    if isinstance(receipt, dict) and receipt.get("state") == "unsaved":
        receipt = None
    return await context_api.settle_provider_continuation(
        conn, **{**settlement, "sdk_settlement": sdk, "workspace_receipt": receipt,
                 "final_sequence": final_sequence},
    )


async def settle_predispatch_continuation(
    conn: Any, *, tenant_id: str, run_id: str, attempt_id: str, settlement: dict[str, Any],
) -> dict[str, Any]:
    if (not isinstance(settlement, dict)
        or any(settlement.get(key) != value for key, value in {
            "tenant_id": tenant_id, "run_id": run_id, "attempt_id": attempt_id,
        }.items())
        or settlement.get("stop_evidence") not in {"provider_not_created", "provider_stopped", "provider_not_found"}
        or settlement.get("final_sequence") is not None
        or settlement.get("workspace_receipt") is not None
        or settlement.get("external_effect_unknown") is not False):
        raise ValueError("predispatch_continuation_receipt_invalid")
    sdk = settlement.get("sdk_settlement")
    if (not isinstance(sdk, dict) or sdk.get("input_accepted") is not False
        or sdk.get("ack_unknown") is not False or sdk.get("mirror_complete") is not False
        or sdk.get("received_result") is not False
        or sdk.get("terminal_reason") != "predispatch_failure"):
        raise ValueError("predispatch_continuation_receipt_invalid")
    context_settlement = dict(settlement)
    if context_settlement.pop("runtime_lease", None) is not None:
        ContinuationSettlementPorts._validate_runtime_lease(settlement)
    # Keep the runtime's exact generation; a later owner must never adopt this proof.
    return await context_api.settle_provider_continuation(conn, **context_settlement)


async def release_predispatch_continuation(conn: Any, *, settlement: dict[str, Any], reason: str) -> None:
    await build_continuation_settlement_ports().release(conn, settlement=settlement, reason=reason)
    await context_api.release_provider_lineage(
        conn, tenant_id=settlement["tenant_id"], run_id=settlement["run_id"],
    )


def build_continuation_settlement_ports() -> ContinuationSettlementPorts:
    return ContinuationSettlementPorts(
        save_workspace_version=_save_stopped_workspace,
        settle_provider_continuation=_settle_stopped_continuation,
        release_runtime_lease=release_sandbox_lease,
    )


async def prepare_worker_workspace_restore(*, payload: Any, context_pack: dict,
                                          tool_policy_subjects: list[dict], workspace_root: Any,
                                          workspace: Path, enabled: bool) -> tuple[Any, Any, dict] | None:
    binding = await restore_worker_workspace(
        payload=payload, context_pack=context_pack, tool_policy_subjects=tool_policy_subjects,
        workspace_root=workspace_root,
    ) if enabled else None
    if binding is None:
        reset_worker_workspace(workspace_root, workspace)
    elif Path(binding[1].workspace_host_path) != workspace:
        raise ValueError("workspace_restore_binding_invalid")
    return binding


async def restore_worker_workspace(*, payload: Any, context_pack: dict, tool_policy_subjects: list[dict],
                                   workspace_root: Any) -> tuple[Any, Any, dict] | None:
    fields = runtime_continuation_fields(context_pack, tool_policy_subjects)
    receipt = fields.get("workspace_restore_receipt")
    if receipt is None:
        return None
    request = SimpleNamespace(**{key: getattr(payload, key) for key in (
        "tenant_id", "workspace_id", "user_id", "session_id", "run_id", "attempt_id", "owner_generation",
    )}, tool_policy_subjects=tool_policy_subjects, sandbox_mode="ephemeral",
       browser_enabled=bool(payload.input.get("browser_enabled")))
    manager = SandboxWorkspaceManager(root=workspace_root)
    lease = manager.reserve(request)
    names = workspace_versions.authorized_workspace_skill_names(request)
    restored = await _workspace_io(
        sandbox_api.restore_workspace_version, receipt=receipt, request=request,
        target_workspace_lease=lease, authorized_skill_names=names,
        storage=partial(workspace_versions.restore_workspace_version, workspace_root=workspace_root),
    )
    manager.prepare(request)
    return request, lease, restored


async def finish_worker_workspace_restore(binding: tuple[Any, Any, dict] | None, *,
                                          context_pack: dict, staged_names: list[str],
                                          workspace_root: Any) -> None:
    if binding is None or binding[2]["state"] == "restored":
        return
    request, lease, restored = binding
    await _workspace_io(
        sandbox_api.restore_workspace_skill_outputs,
        receipt=context_pack["conversation_context"]["recovery"]["workspace_receipt"],
        restore_receipt=restored, request=request, target_workspace_lease=lease,
        authorized_skill_names=staged_names,
        staging_evidence={
            "state": "skills_staged", "execution_phase": "before_executor_start",
            "scope": {key: getattr(request, key) for key in (
                "tenant_id", "workspace_id", "user_id", "session_id", "run_id", "attempt_id",
            )}, "owner_generation": request.owner_generation, "staged_skill_names": staged_names,
        },
        storage=partial(workspace_versions.restore_workspace_skill_outputs, workspace_root=workspace_root),
    )


def runtime_continuation_fields(context_pack: Any, tool_policy_subjects: list[dict]) -> dict[str, Any]:
    """Pass only the Context-frozen recovery binding, never user input fields."""
    conversation = context_pack.get("conversation_context") if isinstance(context_pack, dict) else None
    if not isinstance(conversation, dict):
        raise ValueError("provider_session_spec_missing")
    fields: dict[str, Any] = {"provider_epoch_id": conversation["provider_epoch_id"]}
    recovery = conversation.get("recovery")
    if recovery is None:
        return fields
    if not isinstance(recovery, dict) or recovery.get("state") not in {"resumable", "recovery_candidate"}:
        raise ValueError("provider_session_recovery_binding_invalid")
    receipt = recovery.get("workspace_receipt")
    if not isinstance(receipt, dict) or receipt.get("state") != "saved":
        raise ValueError("provider_session_workspace_unsaved")
    fields["workspace_restore_receipt"] = dict(receipt)
    if recovery["state"] == "recovery_candidate":
        # Executable native surfaces stay outside inspection even after an MCP receipt resolves.
        allowed = {"Read", "Glob", "Grep", "LS"}
        allowed.update(
            subject["identity"] for subject in tool_policy_subjects
            if isinstance(subject, dict) and isinstance(subject.get("identity"), str)
            and subject["identity"].startswith("mcp__")
            and all(subject.get(key) is True for key in ("registered", "declared", "active", "distributed"))
        )
        fields["recovery_tool_gate"] = {
            "state": "inspection", "allowed_tool_identities": sorted(allowed),
            "released_tool_identities": [],
        }
    return fields
