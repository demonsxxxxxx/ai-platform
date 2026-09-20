"""Atomic Conversation Run creation with optional Knowledge authority."""

from __future__ import annotations

import re
from typing import Any, Protocol

from app.knowledge.api import KnowledgeError, admit_run_knowledge


_SAFE_CODE_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_FALLBACK_CODE = "knowledge_snapshot_unavailable"


class CreateRun(Protocol):
    async def __call__(self, conn: Any, **kwargs: Any) -> str: ...


class ConversationRunAdmissionError(ValueError):
    """Safe Conversation rejection raised before the Run transaction commits."""

    def __init__(self, code: object) -> None:
        self.code = (
            code
            if isinstance(code, str) and _SAFE_CODE_PATTERN.fullmatch(code)
            else _FALLBACK_CODE
        )
        super().__init__(self.code)


async def create_admitted_run(
    conn: Any,
    create_run: CreateRun,
    run_create_kwargs: dict[str, Any],
    agent_profile_execution_input: dict[str, Any] | None,
) -> str:
    """Create the Run and its immutable Knowledge snapshot in one transaction."""

    run_id = await create_run(conn, **run_create_kwargs)
    await admit_created_run_knowledge(
        conn,
        run_id=run_id,
        tenant_id=run_create_kwargs.get("tenant_id"),
        agent_id=run_create_kwargs.get("agent_id"),
        profile_revision=run_create_kwargs.get("admitted_agent_profile_revision"),
        profile_content_hash=run_create_kwargs.get("admitted_agent_profile_hash"),
        principal_policy_version=run_create_kwargs.get("authz_policy_version"),
        agent_profile_execution_input=agent_profile_execution_input,
    )
    return run_id


async def admit_created_run_knowledge(
    conn: Any,
    *,
    run_id: str,
    tenant_id: Any,
    agent_id: Any,
    profile_revision: Any,
    profile_content_hash: Any,
    principal_policy_version: Any,
    agent_profile_execution_input: dict[str, Any] | None,
) -> None:
    """Attach an enabled Agent's new Knowledge snapshot before its transaction commits."""

    if (
        agent_profile_execution_input is None
        or agent_profile_execution_input.get("knowledge_enabled") is not True
    ):
        return
    try:
        await admit_run_knowledge(
            conn,
            tenant_id=tenant_id,
            run_id=run_id,
            agent_id=agent_id,
            profile_revision=profile_revision,
            profile_content_hash=profile_content_hash,
            principal_policy_version=principal_policy_version,
            knowledge_source_ids=agent_profile_execution_input.get(
                "knowledge_source_ids"
            ),
            retrieval_profile_id=agent_profile_execution_input.get(
                "retrieval_profile_id"
            ),
            knowledge_bindings=agent_profile_execution_input.get(
                "knowledge_bindings"
            ),
        )
    except KnowledgeError as exc:
        raise ConversationRunAdmissionError(exc.code) from exc
