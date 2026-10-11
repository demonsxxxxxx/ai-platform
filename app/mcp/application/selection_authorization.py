"""Authorize Chat MCP selections without changing admission identity ownership."""

from collections.abc import Awaitable, Callable
from typing import Any

from app.mcp.domain.tool_references import mcp_runtime_metadata_usable


class _SelectionDenied(Exception):
    def __init__(self, context: Any, tool_id: str, *, decision: Any = None,
                 duplicate: bool = False) -> None:
        self.context = context
        self.tool_id = tool_id
        self.decision = decision
        self.duplicate = duplicate


class _DistributionConflict(_SelectionDenied):
    def __init__(self, context: Any, tool_id: str, cause: Exception) -> None:
        super().__init__(context, tool_id)
        self.cause = cause


class McpSelectionAuthorization:
    """One selection policy with explicit, retained Chat/replay error boundaries."""

    def __init__(self, *, get_tool: Callable[..., Awaitable[Any]],
                 get_distribution: Callable[..., Awaitable[Any]],
                 authorization_error: Callable[..., Exception],
                 conflict_error: type[Exception],
                 normalize_roles: Callable[..., Any], context_type: Callable[..., Any],
                 subject_type: Callable[..., Any], resolve_access: Callable[..., Any],
                 evaluate_policy: Callable[..., Any]) -> None:
        self._get_tool = get_tool
        self._get_distribution = get_distribution
        self._authorization_error = authorization_error
        self._conflict_error = conflict_error
        self._normalize_roles = normalize_roles
        self._context_type = context_type
        self._subject_type = subject_type
        self._resolve_access = resolve_access
        self._evaluate_policy = evaluate_policy

    def _denial(self, failure: _SelectionDenied, *, capability_id: str) -> Exception:
        return self._authorization_error(
            context=failure.context, capability_kind="mcp_tool",
            capability_id=capability_id, decision=failure.decision,
        )

    async def authorize_selected_chat(self, conn: Any, **kwargs: Any) -> list[dict[str, Any]]:
        return await self._chat(conn, available_only=False, **kwargs)

    async def authorize_available_chat(self, conn: Any, **kwargs: Any) -> list[dict[str, Any]]:
        return await self._chat(conn, available_only=True, **kwargs)

    async def _chat(self, conn: Any, *, available_only: bool, **kwargs: Any) -> list[dict[str, Any]]:
        try:
            return await self._authorize(conn, available_only=available_only, **kwargs)
        except _DistributionConflict as exc:
            # Preserve Chat's existing conflict (409), including available mode.
            raise exc.cause from None
        except _SelectionDenied as exc:
            raise self._denial(
                exc, capability_id="mcp_tool" if exc.duplicate else exc.tool_id,
            ) from exc

    async def authorize_selected_replay(self, conn: Any, **kwargs: Any) -> list[dict[str, Any]]:
        try:
            return await self._authorize(conn, available_only=False, **kwargs)
        except _SelectionDenied as exc:
            # Replay has always mapped corrupt distribution scope to an audited
            # authorization denial (403), and retains the duplicate tool identity.
            raise self._denial(exc, capability_id=exc.tool_id) from (
                exc.cause if isinstance(exc, _DistributionConflict) else exc
            )

    async def _authorize(
        self, conn: Any, *, tenant_id: str, tool_ids: list[str],
        principal_department_id: str, principal_roles: list[str] | None,
        is_admin: bool, permissions: list[str] | None, available_only: bool,
    ) -> list[dict[str, Any]]:
        context = self._context_type(
            tenant_id=tenant_id,
            department_id=str(principal_department_id or ""),
            roles=self._normalize_roles(principal_roles or []),
            is_admin=bool(is_admin),
            permissions=[str(item) for item in permissions or [] if str(item)],
        )
        if len(tool_ids) != len(set(tool_ids)):
            duplicate_id = next(item for index, item in enumerate(tool_ids) if item in tool_ids[:index])
            raise _SelectionDenied(context, duplicate_id, duplicate=True)
        authorized: list[dict[str, Any]] = []
        for tool_id in tool_ids:
            tool = await self._get_tool(conn, tenant_id=tenant_id, tool_id=tool_id)
            if (tool is None or str(tool.get("tool_id") or "").strip() != tool_id
                    or not mcp_runtime_metadata_usable(tool)):
                if available_only:
                    continue
                raise _SelectionDenied(context, tool_id)
            server_id = str(tool.get("server_id") or "")
            try:
                distribution = await self._get_distribution(
                    conn, tenant_id=tenant_id, capability_kind="mcp_server", capability_id=server_id,
                )
            except self._conflict_error as exc:
                raise _DistributionConflict(context, tool_id, exc) from exc
            lifecycle_status = (
                "active" if str(tool.get("effective_status") or "disabled") == "active"
                and str(tool.get("server_status") or "disabled") == "active"
                and bool(tool.get("visible_to_user")) else "disabled"
            )
            decision = self._resolve_access(
                context,
                self._subject_type(
                    capability_kind="mcp_tool", capability_id=tool_id,
                    lifecycle_status=lifecycle_status, distribution=distribution,
                    inherited_distribution_source=f"mcp_server:{server_id}",
                ),
                intent="use",
            )
            policy = self._evaluate_policy(tool={
                "mcp_server": server_id,
                "mcp_tool": str((tool.get("allowed_tools") or [""])[0]),
                "registered": True, "declared": True,
                "active": lifecycle_status == "active", "distributed": decision.usable,
                "identity_authorized": True, "object_authorized": True,
                "parameters_authorized": True,
                "risk_level": str(tool.get("risk_level") or "low"),
                "write_capable": bool(tool.get("write_capable")),
            })
            if not decision.usable or not policy.allowed:
                if available_only:
                    continue
                raise _SelectionDenied(context, tool_id, decision=decision)
            authorized.append(tool)
        return authorized
