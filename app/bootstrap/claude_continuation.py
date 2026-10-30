"""Claude adapter bindings for Context-owned conversation continuation."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from app.bootstrap.conversation_continuation import (
    build_continuation_settlement_ports,
    finish_worker_workspace_restore,
    prepare_worker_workspace_restore,
    runtime_continuation_fields,
)
from app.context_manifest import CONTEXT_MANIFEST_SCHEMA_VERSION
from app.executors.claude.capability_policy import sandbox_runtime_tool_policy_subjects


def context_manifest_from_pack(context_pack: dict[str, Any]) -> dict[str, Any] | None:
    manifest = context_pack.get("context_manifest")
    if (
        not isinstance(manifest, dict)
        or manifest.get("schema_version") != CONTEXT_MANIFEST_SCHEMA_VERSION
    ):
        return None
    return manifest


def build_sandbox_runtime(
    runtime_factory: Callable[..., Any], workspace_root: str | Path
) -> Any:
    """Assemble the Context settlement port without hiding the runtime seam."""

    return runtime_factory(
        workspace_root=workspace_root,
        continuation_ports=build_continuation_settlement_ports(),
    )


async def prepare_workspace_restore(
    payload: Any,
    context_pack: dict[str, Any],
    workspace_root: str | Path,
    workspace: Path,
    enabled: bool,
    sandbox_provider: object,
) -> tuple[Any, Any, dict[str, Any]] | None:
    """Bind frozen recovery and authorized tool policy to the Worker workspace."""

    return await prepare_worker_workspace_restore(
        payload=payload,
        context_pack=context_pack,
        tool_policy_subjects=sandbox_runtime_tool_policy_subjects(
            payload,
            context_manifest_from_pack(context_pack),
            sandbox_provider=str(sandbox_provider),
        ),
        workspace_root=workspace_root,
        workspace=workspace,
        enabled=enabled,
    )


async def finish_workspace_restore(
    binding: tuple[Any, Any, dict[str, Any]] | None,
    context_pack: dict[str, Any],
    staged_names: list[str],
    workspace_root: str | Path,
) -> None:
    await finish_worker_workspace_restore(
        binding,
        context_pack=context_pack,
        staged_names=staged_names,
        workspace_root=workspace_root,
    )


def request_fields(
    payload: Any,
    context_pack: dict[str, Any],
    context_manifest: dict[str, Any] | None,
    settings: Any,
) -> dict[str, Any]:
    """Project the admitted continuation binding into a Sandbox request."""

    return runtime_continuation_fields(
        context_pack,
        sandbox_runtime_tool_policy_subjects(
            payload,
            context_manifest,
            sandbox_provider=str(settings.sandbox_container_provider),
        ),
    )
