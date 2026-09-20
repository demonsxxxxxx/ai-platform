"""Application boundary for immutable Run Knowledge admission."""

from __future__ import annotations

from typing import Any, Protocol

from app.knowledge.domain import (
    KnowledgeError,
    canonical_run_knowledge_bindings,
)


class RunKnowledgeAdmissionRepository(Protocol):
    async def create_run_snapshot_from_bindings(
        self,
        conn: Any,
        *,
        tenant_id: str,
        run_id: str,
        agent_id: str,
        profile_revision: int,
        profile_content_hash: str,
        principal_policy_version: int,
        knowledge_source_ids: tuple[str, ...],
        retrieval_profile_id: str,
        knowledge_bindings: tuple[dict[str, Any], ...],
    ) -> dict[str, Any]: ...


class RunKnowledgeAdmissionService:
    """Freeze server-authorized Agent bindings in the caller's Run transaction."""

    def __init__(self, repository: RunKnowledgeAdmissionRepository) -> None:
        self._repository = repository

    async def admit(
        self,
        conn: Any,
        *,
        tenant_id: str,
        run_id: str,
        agent_id: str,
        profile_revision: int,
        profile_content_hash: str,
        principal_policy_version: int,
        knowledge_source_ids: Any,
        retrieval_profile_id: Any,
        knowledge_bindings: Any,
    ) -> dict[str, Any]:
        if (
            not isinstance(knowledge_bindings, (list, tuple))
            or not knowledge_bindings
            or not all(isinstance(binding, dict) for binding in knowledge_bindings)
        ):
            raise KnowledgeError("knowledge_snapshot_profile_mismatch")
        canonical_bindings = canonical_run_knowledge_bindings(
            source_ids=knowledge_source_ids,
            retrieval_profile_id=retrieval_profile_id,
            bindings=knowledge_bindings,
        )
        return await self._repository.create_run_snapshot_from_bindings(
            conn,
            tenant_id=tenant_id,
            run_id=run_id,
            agent_id=agent_id,
            profile_revision=profile_revision,
            profile_content_hash=profile_content_hash,
            principal_policy_version=principal_policy_version,
            knowledge_source_ids=tuple(
                str(binding["source_id"]) for binding in canonical_bindings
            ),
            retrieval_profile_id=str(canonical_bindings[0]["retrieval_profile_id"]),
            knowledge_bindings=canonical_bindings,
        )


_service: RunKnowledgeAdmissionService | None = None


def configure_run_knowledge_admission(service: RunKnowledgeAdmissionService) -> None:
    global _service
    _service = service


def _configured_service() -> RunKnowledgeAdmissionService:
    if _service is None:
        raise RuntimeError("run_knowledge_admission_not_configured")
    return _service


async def admit_run_knowledge(conn: Any, **kwargs: Any) -> dict[str, Any]:
    return await _configured_service().admit(conn, **kwargs)


__all__ = [
    "RunKnowledgeAdmissionService",
    "admit_run_knowledge",
    "configure_run_knowledge_admission",
]
