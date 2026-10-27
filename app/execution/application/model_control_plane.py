"""Application service for the shared compatible-model control plane."""

from __future__ import annotations

import asyncio
import hmac
import json
from collections.abc import Callable, Iterable, Mapping
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from functools import partial
from typing import Any, Protocol

from app.execution.domain.runtime_debug import normalize_execution_debug
from app.execution.application.model_response_evidence import observe_anthropic_text
from app.execution.application.model_stream_ownership import (
    ModelResponseStream, OwnedModelResponseBody, close_model_stream, open_owned_model_stream,
)
from app.execution.application.model_selection import (
    RunModelSelection,
    resolve_chat_model_selection,
)
from app.execution.domain.model_catalog import (
    normalize_model_display_name,
    normalize_model_token_limits,
    platform_model_id,
    public_model_projection,
)


_ALLOWED_RUNTIME_PATHS = {
    "openai": frozenset({"v1/chat/completions", "v1/responses"}),
    "anthropic": frozenset({"v1/messages", "v1/messages/count_tokens"}),
}
_ANTHROPIC_BETA_ALLOWLIST = {
    "v1/messages": frozenset({
        "claude-code-20250219",
        "interleaved-thinking-2025-05-14",
        "thinking-token-count-2026-05-13",
        "context-management-2025-06-27",
        "prompt-caching-scope-2026-01-05",
        "mid-conversation-system-2026-04-07",
        "mid-conversation-tool-changes-2026-07-01",
        "effort-2025-11-24",
        "thinking-display-updates-2026-08-18",
    }),
    "v1/messages/count_tokens": frozenset({
        "claude-code-20250219",
        "interleaved-thinking-2025-05-14",
        "context-management-2025-06-27",
        "token-counting-2024-11-01",
    }),
}


def _policy_error(code: str, *, policy: str, beta_count: int = 0) -> PermissionError:
    error = PermissionError(code)
    error.debug = {"component": "model_proxy", "phase": "preflight", "failure_class": "policy_rejected", "policy": policy, "beta_count": beta_count}
    return error


def _runtime_proxy_headers(
    provider: str, upstream_path: str, headers: Mapping[str, str]
) -> dict[str, str]:
    outbound = {str(name).lower(): str(value) for name, value in headers.items()}
    if provider != "anthropic":
        return outbound
    version = outbound.get("anthropic-version")
    if version != "2023-06-01":
        raise _policy_error("model_proxy_anthropic_version_not_allowed", policy="anthropic_version")
    betas = outbound.get("anthropic-beta", "")
    if betas:
        values = [item.strip() for item in betas.split(",")]
        if (
            len(values) > 16
            or any(not item for item in values)
            or any(item not in _ANTHROPIC_BETA_ALLOWLIST[upstream_path] for item in values)
        ):
            raise _policy_error("model_proxy_anthropic_beta_not_allowed", policy="anthropic_beta", beta_count=len(values))
        outbound["anthropic-beta"] = ",".join(sorted(set(values)))
    return outbound


class TransactionFactory(Protocol):
    def __call__(self) -> AbstractAsyncContextManager[Any]: ...


class ModelManagementRepository(Protocol):
    async def connection_projection(self, conn: Any) -> dict[str, Any]: ...

    async def active_connection(self, conn: Any, *, encryption_key: str) -> Any: ...

    async def run_connection(self, conn: Any, **kwargs: Any) -> Any: ...

    async def admin_models(self, conn: Any) -> list[dict[str, Any]]: ...

    async def public_models(self, conn: Any, **kwargs: Any) -> dict[str, Any] | None: ...

    async def publish_models(self, conn: Any, **kwargs: Any) -> Any: ...

    async def resolve_run_model(self, conn: Any, **kwargs: Any) -> RunModelSelection | None: ...


    async def catalog_access(self, conn: Any) -> dict[str, dict[str, Any]]: ...

    async def resolve_group_model(self, conn: Any, **kwargs: Any) -> Any: ...

    async def list_groups_for_user(self, conn: Any, **kwargs: Any) -> list[dict[str, Any]]: ...

    async def list_admin_groups(self, conn: Any, **kwargs: Any) -> list[dict[str, Any]]: ...

    async def get_admin_group_details(self, conn: Any, **kwargs: Any) -> dict[str, Any] | None: ...

    async def create_group(self, conn: Any, **kwargs: Any) -> dict[str, Any]: ...

    async def update_group(self, conn: Any, **kwargs: Any) -> dict[str, Any]: ...

    async def archive_or_delete_group(self, conn: Any, **kwargs: Any) -> dict[str, Any]: ...

    async def set_group_models(self, conn: Any, **kwargs: Any) -> dict[str, Any]: ...

    async def set_group_members(self, conn: Any, **kwargs: Any) -> dict[str, Any]: ...

    async def batch_group_models(self, conn: Any, **kwargs: Any) -> dict[str, Any]: ...

    async def get_user_group_memberships(self, conn: Any, **kwargs: Any) -> dict[str, Any] | None: ...

    async def set_user_group_memberships(self, conn: Any, **kwargs: Any) -> dict[str, Any]: ...

    async def set_users_group_memberships_batch(self, conn: Any, **kwargs: Any) -> dict[str, Any]: ...

    async def resolve_company_employee_user(self, conn: Any, **kwargs: Any) -> dict[str, str]: ...


class EmployeeGrantValidator(Protocol):
    async def validate_grants(self, *, tenant_id: str, work_ids: set[str]) -> None: ...


class ModelEndpointSecurity(Protocol):
    def normalize(self, base_url: str) -> str: ...

    def fingerprint(self, api_key: str) -> str: ...


class ModelAttemptCapabilityVerifier(Protocol):
    def __call__(
        self,
        *,
        run_id: str,
        attempt_id: str,
        provided_capability: str,
    ) -> bool: ...


class ModelUpstream(Protocol):
    def request(self, **kwargs: Any) -> Any: ...

    def parse_model_ids(self, response_body: bytes) -> list[str]: ...

    def open_stream(self, **kwargs: Any) -> ModelResponseStream: ...


@dataclass(frozen=True)
class RuntimeProxyResponse:
    status: int
    content_type: str
    body: Iterable[bytes]
    close: Callable[[], None] | None = None
    debug: dict[str, str | int] | None = None


_COUNT_TOKENS_FALLBACK_OVERHEAD = 4096


def _conservative_input_token_count(body: bytes) -> int:
    # ponytail: UTF-8 bytes plus fixed protocol overhead is deliberately loose for
    # count-less compatible gateways; use a provider tokenizer if exact counts matter.
    return len(body) + _COUNT_TOKENS_FALLBACK_OVERHEAD


def _input_token_count(response: Any, *, fallback_body: bytes | None = None) -> int:
    status = getattr(response, "status", None)
    if status == 404 and fallback_body is not None:
        return _conservative_input_token_count(fallback_body)
    if type(status) is not int or not 200 <= status < 300:
        error = RuntimeError(
            "model_proxy_count_tokens_unavailable" if status in {429, 503} else "model_proxy_count_tokens_failed"
        )
        error.debug = normalize_execution_debug({
            **normalize_execution_debug(getattr(response, "debug", None)), "component": "model_proxy", "operation": "count_tokens",
            "phase": "response", "failure_class": "http_error", "http_status": status,
        })
        raise error
    try:
        payload = json.loads(response.body)
    except (AttributeError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise RuntimeError("model_proxy_count_tokens_invalid") from exc
    count = payload.get("input_tokens") if isinstance(payload, dict) else None
    if type(count) is not int or count < 0:
        raise RuntimeError("model_proxy_count_tokens_invalid")
    return count


class ModelControlPlaneService:
    def __init__(
        self,
        *,
        transaction_factory: TransactionFactory,
        settings_provider: Any,
        repository: ModelManagementRepository,
        security: ModelEndpointSecurity,
        upstream: ModelUpstream,
        attempt_capability_verifier: ModelAttemptCapabilityVerifier,
        diagnostic_recorder: Callable[..., Any] | None = None,
        employee_directory: EmployeeGrantValidator | None = None,
    ) -> None:
        self._transaction = transaction_factory
        self._settings_provider = settings_provider
        self._repository = repository
        self._security = security
        self._upstream = upstream
        self._attempt_capability_verifier = attempt_capability_verifier
        self._diagnostic_recorder = diagnostic_recorder
        self._employee_directory = employee_directory

    async def record_debug(self, *, run_id: str, attempt_id: str, error_code: str, debug: object) -> None:
        facts = normalize_execution_debug(debug)
        if self._diagnostic_recorder is not None and facts:
            await self._diagnostic_recorder(run_id=run_id, attempt_id=attempt_id, error_code=error_code, debug=facts)

    def _security_settings(self) -> tuple[str, str]:
        settings = self._settings_provider()
        return (
            str(settings.model_connection_encryption_key or ""),
            str(settings.model_connection_allowed_internal_hosts or ""),
        )

    async def _discover_models(self, *, base_url: str, api_key: str) -> list[str]:
        encryption_key, allowed_hosts = self._security_settings()
        if not encryption_key:
            raise ValueError("model_connection_encryption_key_invalid")
        response = await asyncio.to_thread(
            self._upstream.request,
            base_url=base_url,
            allowed_internal_hosts=allowed_hosts,
            api_key=api_key,
            method="GET",
            path="/v1/models",
            provider="catalog",
        )
        return self._upstream.parse_model_ids(response)

    async def admin_projection(self) -> dict[str, Any]:
        async with self._transaction() as conn:
            return {
                "connection": await self._repository.connection_projection(conn),
                "models": await self._repository.admin_models(conn),
            }

    async def discover(self, *, base_url: str, api_key: str | None) -> dict[str, Any]:
        encryption_key, _allowed_hosts = self._security_settings()
        requested_url = self._security.normalize(base_url)
        resolved_key = str(api_key or "").strip()
        async with self._transaction() as conn:
            current = await self._repository.active_connection(
                conn, encryption_key=encryption_key
            )
            connection = await self._repository.connection_projection(conn)
            existing = await self._repository.admin_models(conn)
        if not resolved_key:
            if current is None or requested_url != current.base_url:
                raise ValueError("model_connection_api_key_required")
            resolved_key = current.api_key
        model_ids = await self._discover_models(
            base_url=requested_url, api_key=resolved_key
        )
        by_value = {model["value"]: model for model in existing}
        return {
            "connection": connection,
            "base_url": requested_url,
            "models": [
                {
                    **(by_value.get(value) or {
                        "id": platform_model_id(value),
                        "value": value,
                        "label": value,
                        "provider": "compatible",
                        "enabled": False,
                        "is_default": False,
                        "order": order,
                    }),
                    "order": order,
                    "available": True,
                }
                for order, value in enumerate(model_ids, start=1)
            ],
        }

    async def publish(
        self,
        *,
        base_url: str,
        api_key: str | None,
        expected_revision: int | None,
        models: list[dict[str, Any]],
        actor_user_id: str,
        tenant_id: str = "default",
    ) -> dict[str, Any]:
        encryption_key, _allowed_hosts = self._security_settings()
        requested_url = self._security.normalize(base_url)
        resolved_key = str(api_key or "").strip()
        if not resolved_key:
            async with self._transaction() as conn:
                current = await self._repository.active_connection(
                    conn, encryption_key=encryption_key
                )
            if current is None or requested_url != current.base_url:
                raise ValueError("model_connection_api_key_required")
            resolved_key = current.api_key
        model_ids = await self._discover_models(
            base_url=requested_url, api_key=resolved_key
        )
        discovered = set(model_ids)
        if not model_ids or len(models) != len(model_ids) or {
            model.get("value") for model in models
        } != discovered:
            raise ValueError("model_catalog_discovery_changed")
        if len({model.get("value") for model in models}) != len(models):
            raise ValueError("model_catalog_discovery_changed")
        for model in models:
            if any(field in model for field in ("access_mode", "allowed_user_ids", "is_default")):
                raise ValueError("model_user_access_retired")
            if model.get("id") != platform_model_id(model["value"]):
                raise ValueError("model_catalog_identity_collision")
            window, output_limit = normalize_model_token_limits(
                model.get("auto_compact_window"), model.get("max_output_tokens")
            )
            if model["enabled"] and output_limit is None:
                raise ValueError("model_capacity_pair_required")
            if output_limit is None:
                # A row without an execution cap keeps both columns empty; the
                # window has no meaning until the model can actually run.
                window = None
            # Persist the resolved window so discovery and reload never drift.
            model["auto_compact_window"] = window
            model["max_output_tokens"] = output_limit
        if len({model["order"] for model in models}) != len(models):
            raise ValueError("model_catalog_order_invalid")
        async with self._transaction() as conn:
            saved = await self._repository.catalog_access(conn)
        for model in models:
            previous = saved.get(model["id"])
            model["display_name"] = normalize_model_display_name(
                model.get("display_name"),
                fallback=model["value"],
                current=previous["display_name"] if previous else None,
            )
            # Legacy ACL columns are read-only migration input. New publication
            # cannot write or widen them; grouped admission owns authorization.
        # Network validation is outside transactions; publication still owns revision CAS.
        async with self._transaction() as conn:
            revision, published = await self._repository.publish_models(
                conn,
                base_url=requested_url,
                api_key=resolved_key,
                key_fingerprint=self._security.fingerprint(resolved_key),
                encryption_key=encryption_key,
                actor_user_id=actor_user_id,
                upstream_model_ids=model_ids,
                expected_revision=expected_revision,
                models=models,
            )
            connection = await self._repository.connection_projection(conn)
        return {"connection": connection, "models": published, "revision": revision}

    async def public_models(self, conn: Any, *, user_id: str, tenant_id: str | None = None, usage_scene: str = "independent", group_id: str | None = None) -> dict[str, Any]:
        governed = await self._repository.public_models(
            conn, user_id=user_id, tenant_id=tenant_id, usage_scene=usage_scene,
            group_id=group_id,
        )
        if governed is not None:
            return governed
        return public_model_projection([])

    async def groups_for_user(self, *, tenant_id: str, user_id: str) -> list[dict[str, Any]]:
        async with self._transaction() as conn:
            return await self._repository.list_groups_for_user(conn, tenant_id=tenant_id, user_id=user_id)

    async def admin_groups(self, *, tenant_id: str) -> list[dict[str, Any]]:
        async with self._transaction() as conn:
            return await self._repository.list_admin_groups(conn, tenant_id=tenant_id)

    async def admin_group_details(self, *, tenant_id: str, group_id: str) -> dict[str, Any] | None:
        async with self._transaction() as conn:
            return await self._repository.get_admin_group_details(
                conn, tenant_id=tenant_id, group_id=group_id,
            )

    async def create_model_group(self, **kwargs: Any) -> dict[str, Any]:
        async with self._transaction() as conn:
            return await self._repository.create_group(conn, **kwargs)

    async def update_model_group(self, **kwargs: Any) -> dict[str, Any]:
        async with self._transaction() as conn:
            return await self._repository.update_group(conn, **kwargs)

    async def archive_or_delete_model_group(self, **kwargs: Any) -> dict[str, Any]:
        async with self._transaction() as conn:
            return await self._repository.archive_or_delete_group(conn, **kwargs)

    async def set_model_group_models(self, **kwargs: Any) -> dict[str, Any]:
        async with self._transaction() as conn:
            return await self._repository.set_group_models(conn, **kwargs)

    async def user_group_memberships(self, *, tenant_id: str, user_id: str) -> dict[str, Any] | None:
        async with self._transaction() as conn:
            return await self._repository.get_user_group_memberships(
                conn, tenant_id=tenant_id, user_id=user_id,
            )

    async def set_user_group_memberships(self, **kwargs: Any) -> dict[str, Any]:
        async with self._transaction() as conn:
            return await self._repository.set_user_group_memberships(conn, **kwargs)

    async def set_users_group_memberships_batch(self, **kwargs: Any) -> dict[str, Any]:
        async with self._transaction() as conn:
            return await self._repository.set_users_group_memberships_batch(conn, **kwargs)

    async def batch_group_models(self, **kwargs: Any) -> dict[str, Any]:
        async with self._transaction() as conn:
            return await self._repository.batch_group_models(conn, **kwargs)

    async def set_model_group_members(self, **kwargs: Any) -> dict[str, Any]:
        async with self._transaction() as conn:
            members = []
            for member in kwargs.pop("members", []):
                if not isinstance(member, dict):
                    raise ValueError("model_group_member_invalid")
                user_id = member.get("user_id")
                work_id = member.get("work_id")
                if user_id is not None and work_id is not None:
                    raise ValueError("model_group_member_identity_conflict")
                if work_id is not None:
                    resolved = await self._repository.resolve_company_employee_user(
                        conn, tenant_id=kwargs["tenant_id"], work_id=work_id,
                    )
                    user_id = resolved["user_id"]
                if not isinstance(user_id, str) or not user_id.strip():
                    raise ValueError("model_group_member_identity_required")
                members.append({"user_id": user_id, "is_primary": bool(member.get("is_primary"))})
            kwargs["members"] = members
            return await self._repository.set_group_members(conn, **kwargs)

    async def resolve_selection(
        self,
        conn: Any,
        *,
        selection: dict[str, str] | None,
        user_id: str,
        tenant_id: str | None = None,
        group_id: str | None = None,
        selection_mode: str = "auto",
        usage_scene: str = "independent",
    ) -> RunModelSelection:
        effective_group_id = group_id or (selection or {}).get("group_id")
        effective_mode = (selection or {}).get("selection_mode") or (
            "explicit" if (selection or {}).get("id") or (selection or {}).get("value") else selection_mode
        )
        async def resolve_effective(conn: Any, *, model_id: str | None, model_value: str | None, user_id: str) -> RunModelSelection | None:
            return await self._repository.resolve_run_model(
                conn,
                model_id=model_id,
                model_value=model_value,
                user_id=user_id,
                tenant_id=tenant_id,
                group_id=effective_group_id,
                selection_mode=effective_mode,
                usage_scene=usage_scene,
            )
        return await resolve_chat_model_selection(
            conn, selection=selection, user_id=user_id,
            resolve_governed_model=resolve_effective,
        )

    async def proxy(
        self,
        *,
        provider: str,
        upstream_path: str,
        query: str,
        body: bytes,
        headers: Mapping[str, str],
        run_id: str,
        attempt_id: str,
        internal_token: str,
        model_proxy_capability: str,
    ) -> RuntimeProxyResponse:
        settings = self._settings_provider()
        expected_token = str(settings.model_proxy_internal_token or "")
        if not expected_token or not hmac.compare_digest(
            internal_token.encode("utf-8"), expected_token.encode("utf-8")
        ):
            raise PermissionError("model_proxy_forbidden")
        if not attempt_id:
            raise PermissionError("model_proxy_attempt_required")
        if not self._attempt_capability_verifier(
            run_id=run_id,
            attempt_id=attempt_id,
            provided_capability=model_proxy_capability,
        ):
            raise PermissionError("model_proxy_capability_invalid")
        if upstream_path not in _ALLOWED_RUNTIME_PATHS.get(provider, frozenset()):
            raise PermissionError("model_proxy_path_not_allowed")
        if query and (provider != "anthropic" or query != "beta=true"):
            raise PermissionError("model_proxy_query_not_allowed")
        outbound_headers = _runtime_proxy_headers(provider, upstream_path, headers)
        try:
            payload = json.loads(body)
            model_value = payload.get("model") if isinstance(payload, dict) else None
            if not isinstance(model_value, str) or not model_value:
                raise ValueError
        except (TypeError, ValueError, json.JSONDecodeError):
            raise ValueError("model_proxy_body_invalid") from None

        encryption_key, allowed_hosts = self._security_settings()
        async with self._transaction() as conn:
            connection = await self._repository.run_connection(
                conn,
                run_id=run_id,
                attempt_id=attempt_id,
                model_value=model_value,
                encryption_key=encryption_key,
            )
        if connection is None:
            raise PermissionError("model_proxy_run_binding_invalid")
        if connection.max_output_tokens is None or connection.auto_compact_window is None:
            raise PermissionError("model_capacity_missing")
        if provider == "anthropic" and upstream_path == "v1/messages":
            if connection.conversation_mode not in {"native_resume", "empty_start"}:
                raise PermissionError("model_proxy_conversation_mode_invalid")
            max_tokens = payload.get("max_tokens")
            if (
                type(max_tokens) is not int
                or max_tokens < 1
                or max_tokens > connection.max_output_tokens
            ):
                raise ValueError("model_proxy_max_tokens_invalid")
        elif provider == "anthropic" and upstream_path == "v1/messages/count_tokens":
            count_response = await asyncio.to_thread(
                self._upstream.request,
                base_url=connection.base_url,
                allowed_internal_hosts=allowed_hosts,
                api_key=connection.api_key,
                method="POST",
                path="/v1/messages/count_tokens",
                provider="anthropic",
                body=body,
                headers=outbound_headers,
                query=query,
                max_response_bytes=8192,
            )
            if count_response.status == 404:
                fallback = json.dumps(
                    {"input_tokens": _conservative_input_token_count(body)},
                    separators=(",", ":"),
                ).encode("utf-8")
                return RuntimeProxyResponse(
                    status=200, content_type="application/json", body=(fallback,),
                )
            _input_token_count(count_response)
            return RuntimeProxyResponse(
                status=count_response.status,
                content_type=getattr(count_response, "content_type", "application/json"),
                body=(count_response.body,),
                debug=normalize_execution_debug(getattr(count_response, "debug", None)),
            )
        upstream = await open_owned_model_stream(partial(
            self._upstream.open_stream,
            base_url=connection.base_url,
            allowed_internal_hosts=allowed_hosts,
            api_key=connection.api_key,
            method="POST",
            path=f"/{upstream_path}",
            provider=provider,
            body=body,
            headers=outbound_headers,
            query=query,
        ))
        try:
            response_body = upstream.body()
            if (
                provider == "anthropic"
                and upstream_path == "v1/messages"
                and upstream.status == 200
                and upstream.content_type.lower().startswith("text/event-stream")
            ):
                response_body = observe_anthropic_text(
                    response_body, run_id=run_id, attempt_id=attempt_id
                )
            owned_body = OwnedModelResponseBody(response_body, close=upstream.close)
            return RuntimeProxyResponse(
                status=upstream.status,
                content_type=upstream.content_type,
                body=owned_body,
                close=owned_body.close,
                debug=normalize_execution_debug(getattr(upstream, "debug", None)),
            )
        except BaseException:
            close_model_stream(upstream.close)
            raise



_service: ModelControlPlaneService | None = None


def configure_model_control_plane(service: ModelControlPlaneService) -> None:
    global _service
    _service = service


def configured_model_control_plane() -> ModelControlPlaneService:
    if _service is None:
        raise RuntimeError("model_control_plane_service_not_configured")
    return _service
