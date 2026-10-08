"""Bind the Runs Worker-result use case to existing context-owned writers."""

from __future__ import annotations

from typing import Any

from app.agent_apps.capability_state import exact_invoked_skills
from app.artifacts import api as artifacts_api
from app.artifacts.infrastructure import records_postgres as artifact_records_postgres
from app.conversations.infrastructure import postgres as conversations_postgres
from app.control_plane_contracts import artifact_lineage_contract, artifact_manifest_contract
from app.execution import api as execution_api
from app.persistence import artifacts as artifact_persistence
from app.platform.postgres import sandbox_leases as sandbox_lease_repository
from app.platform.postgres.limits import MESSAGE_CONTENT_MAX_BYTES, RUN_RESULT_MAX_BYTES, json_size_bytes
from app.platform.public_payload import sanitize_public_text
from app.platform.postgres import values as platform_values
from app.runs import api as runs_api
from app.runs.infrastructure import postgres as runs_postgres
from app.skills import api as skills_api
from app.skills.infrastructure import postgres as skills_postgres
from app.skills.infrastructure import run_snapshots_postgres as skills_run_snapshots_postgres
from app.streaming import api as streaming_api
from app.streaming.infrastructure import run_events_postgres as streaming_run_events_postgres
from app.streaming.worker_projection import persist_worker_failure_event


def build_worker_artifact_records(result: Any, *, reconciliation: bool) -> list[dict[str, Any]]:
    return artifacts_api.build_artifact_records(
        result.artifacts, reconciliation, platform_values.new_id,
        artifacts_api.artifact_download_url,
    )


def build_worker_result_commit_service(transaction_factory: Any) -> runs_api.WorkerResultCommitService:
    limits = execution_api.AnswerPersistenceLimits(
        MESSAGE_CONTENT_MAX_BYTES, RUN_RESULT_MAX_BYTES, json_size_bytes
    )

    async def materialize_answer(capabilities, conn, **kwargs):
        return await execution_api.materialize_worker_answer(
            capabilities, conn, limits=limits, **kwargs
        )

    async def append_user_event(conn, **kwargs):
        await streaming_api.append_worker_user_event(
            conn, append_event=streaming_run_events_postgres.append_event, **kwargs
        )

    async def persist_artifacts(conn, records, payload, *, trace_id):
        await artifacts_api.persist_worker_artifacts(
            conn, records, payload,
            trace_id=trace_id,
            promote_cleanup=artifact_persistence.promote_provisional_artifact_cleanup,
            manifest_contract=artifact_manifest_contract,
            lineage_contract=artifact_lineage_contract,
            create_artifact=artifact_records_postgres.create_artifact,
            append_user_event=append_user_event,
        )

    async def persist_skills(conn, result, payload):
        await skills_api.persist_worker_skill_snapshots(
            conn,
            execution_api.skill_manifests_for_persistence(
                result, payload.skill_manifests, invoked_skill_ids=exact_invoked_skills
            ),
            tenant_id=payload.tenant_id,
            run_id=payload.run_id,
            release_decision=payload.release_decision,
            source_json=skills_postgres.run_skill_snapshot_source_json,
            upsert_snapshot=skills_run_snapshots_postgres.upsert_run_skill_snapshot,
        )

    async def persist_assistant(
        conn, result, payload, records, skill_snapshot,
        assistant_message, assistant_metadata, attempt_id, result_payload,
    ):
        await runs_api.persist_assistant_with_provider_coverage(
            conn, append_message=conversations_postgres.append_message,
            tenant_id=payload.tenant_id, session_id=payload.session_id,
            run_id=payload.run_id, attempt_id=attempt_id,
            executor_type=payload.executor_type,
            content=(assistant_message if assistant_message is not None
                     else str(result_payload.get("message") or "")),
            metadata_json=execution_api.worker_assistant_metadata(
                records, result, assistant_metadata, skill_snapshot
            ),
            provider_final_sequence=result.executor_payload.get("provider_session_final_sequence"),
        )

    return runs_api.WorkerResultCommitService(
        transaction_factory=transaction_factory,
        lock_run=runs_postgres.get_run,
        reconciliation_claim_current=(
            sandbox_lease_repository.is_sandbox_executor_reconciliation_claim_current
        ),
        materialize_answer=materialize_answer,
        persist_artifacts=persist_artifacts,
        persist_skill_snapshots=persist_skills,
        persist_assistant=persist_assistant,
        append_user_event=append_user_event,
        append_hidden_event=streaming_run_events_postgres.append_event,
        persist_failure_event=persist_worker_failure_event,
        public_failure_message=lambda result: execution_api.public_executor_failure_message(
            result, sanitize_text=sanitize_public_text
        ),
        prefers_cancelled=execution_api.sandbox_failure_prefers_cancelled,
    )
