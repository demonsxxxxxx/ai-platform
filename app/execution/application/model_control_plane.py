"""Application service for the shared compatible-model control plane."""

from __future__ import annotations

import asyncio
import hmac
import json
from collections.abc import Iterable, Mapping
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from typing import Any, Protocol

from app.execution.application.model_selection import (
    LegacyModelResolver,
    RunModelSelection,
    resolve_chat_model_selection,
)


_ALLOWED_RUNTIME_PATHS = {
    "openai": frozenset({"v1/chat/completions", "v1/responses"}),
    "anthropic": frozenset({"v1/messages", "v1/messages/count_tokens"}),
}


class TransactionFactory(Protocol):
    def __call__(self) -> AbstractAsyncContextManager[Any]: ...


class ModelManagementRepository(Protocol):
    async def connection_projection(self, conn: Any) -> dict[str, Any]: ...

    async def active_connection(self, conn: Any, *, encryption_key: str) -> Any: ...

    async def run_connection(self, conn: Any, **kwargs: Any) -> Any: ...

    async def admin_models(self, conn: Any) -> list[dict[str, Any]]: ...

    async def public_models(self, conn: Any) -> dict[str, Any] | None: ...

    async def activate_and_sync(self, conn: Any, **kwargs: Any) -> Any: ...

    async def update_catalog(self, conn: Any, **kwargs: Any) -> Any: ...

    async def resolve_run_model(self, conn: Any, **kwargs: Any) -> RunModelSelection | None: ...


class ModelEndpointSecurity(Protocol):
    def validate(self, base_url: str, *, allowed_internal_hosts: str) -> Any: ...

    def fingerprint(self, api_key: str) -> str: ...


class ModelUpstream(Protocol):
    def request(self, **kwargs: Any) -> bytes: ...

    def parse_model_ids(self, response_body: bytes) -> list[str]: ...

    def open_stream(self, **kwargs: Any) -> Any: ...


@dataclass(frozen=True)
class RuntimeProxyResponse:
    status: int
    content_type: str
    body: Iterable[bytes]


class ModelControlPlaneService:
    def __init__(
        self,
        *,
        transaction_factory: TransactionFactory,
        settings_provider: Any,
        repository: ModelManagementRepository,
        legacy_catalog: LegacyModelResolver,
        security: ModelEndpointSecurity,
        upstream: ModelUpstream,
    ) -> None:
        self._transaction = transaction_factory
        self._settings_provider = settings_provider
        self._repository = repository
        self._legacy_catalog = legacy_catalog
        self._security = security
        self._upstream = upstream

    def _security_settings(self) -> tuple[str, str]:
        settings = self._settings_provider()
        return (
            str(settings.model_connection_encryption_key or ""),
            str(settings.model_connection_allowed_internal_hosts or ""),
        )

    async def _discover_models(self, *, base_url: str, api_key: str) -> tuple[str, list[str]]:
        encryption_key, allowed_hosts = self._security_settings()
        if not encryption_key:
            raise ValueError("model_connection_encryption_key_invalid")
        endpoint = self._security.validate(
            base_url,
            allowed_internal_hosts=allowed_hosts,
        )
        response = await asyncio.to_thread(
            self._upstream.request,
            base_url=endpoint.base_url,
            allowed_internal_hosts=allowed_hosts,
            api_key=api_key,
            method="GET",
            path="/v1/models",
            provider="catalog",
        )
        return endpoint.base_url, self._upstream.parse_model_ids(response)

    async def admin_projection(self) -> dict[str, Any]:
        async with self._transaction() as conn:
            return {
                "connection": await self._repository.connection_projection(conn),
                "models": await self._repository.admin_models(conn),
            }

    async def configure_connection(
        self,
        *,
        base_url: str,
        api_key: str | None,
        actor_user_id: str,
    ) -> dict[str, Any]:
        encryption_key, _ = self._security_settings()
        resolved_api_key = str(api_key or "").strip()
        if not resolved_api_key:
            async with self._transaction() as conn:
                current = await self._repository.active_connection(
                    conn,
                    encryption_key=encryption_key,
                )
            if current is None:
                raise ValueError("model_connection_api_key_required")
            resolved_api_key = current.api_key
        normalized_url, model_ids = await self._discover_models(
            base_url=base_url,
            api_key=resolved_api_key,
        )
        async with self._transaction() as conn:
            revision, models = await self._repository.activate_and_sync(
                conn,
                base_url=normalized_url,
                api_key=resolved_api_key,
                key_fingerprint=self._security.fingerprint(resolved_api_key),
                encryption_key=encryption_key,
                actor_user_id=actor_user_id,
                upstream_model_ids=model_ids,
            )
            connection = await self._repository.connection_projection(conn)
        return {"connection": connection, "models": models, "revision": revision}

    async def sync(self, *, actor_user_id: str) -> dict[str, Any]:
        encryption_key, _ = self._security_settings()
        async with self._transaction() as conn:
            current = await self._repository.active_connection(
                conn,
                encryption_key=encryption_key,
            )
        if current is None:
            raise ValueError("model_connection_not_configured")
        normalized_url, model_ids = await self._discover_models(
            base_url=current.base_url,
            api_key=current.api_key,
        )
        async with self._transaction() as conn:
            revision, models = await self._repository.activate_and_sync(
                conn,
                base_url=normalized_url,
                api_key=current.api_key,
                key_fingerprint=current.key_fingerprint,
                encryption_key=encryption_key,
                actor_user_id=actor_user_id,
                upstream_model_ids=model_ids,
            )
            connection = await self._repository.connection_projection(conn)
        return {"connection": connection, "models": models, "revision": revision}

    async def patch_catalog(self, *, model_id: str, **patch: Any) -> dict[str, Any] | None:
        async with self._transaction() as conn:
            return await self._repository.update_catalog(conn, model_id=model_id, **patch)

    async def public_models(self, conn: Any) -> dict[str, Any]:
        governed = await self._repository.public_models(conn)
        if governed is not None:
            return governed
        return await self._legacy_catalog.public_models()

    async def resolve_selection(
        self,
        conn: Any,
        *,
        selection: dict[str, str] | None,
    ) -> RunModelSelection | None:
        return await resolve_chat_model_selection(
            conn,
            selection=selection,
            resolve_governed_model=self._repository.resolve_run_model,
            resolve_legacy_model=self._legacy_catalog,
        )

    async def proxy(
        self,
        *,
        provider: str,
        upstream_path: str,
        query_present: bool,
        body: bytes,
        headers: Mapping[str, str],
        run_id: str,
        attempt_id: str,
        internal_token: str,
    ) -> RuntimeProxyResponse:
        settings = self._settings_provider()
        expected_token = str(settings.model_proxy_internal_token or "")
        if not expected_token or not hmac.compare_digest(internal_token, expected_token):
            raise PermissionError("model_proxy_forbidden")
        if not attempt_id:
            raise PermissionError("model_proxy_attempt_required")
        if upstream_path not in _ALLOWED_RUNTIME_PATHS.get(provider, frozenset()):
            raise PermissionError("model_proxy_path_not_allowed")
        if query_present:
            raise PermissionError("model_proxy_query_not_allowed")
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
        upstream = await asyncio.to_thread(
            self._upstream.open_stream,
            base_url=connection.base_url,
            allowed_internal_hosts=allowed_hosts,
            api_key=connection.api_key,
            method="POST",
            path=f"/{upstream_path}",
            provider=provider,
            body=body,
            headers=headers,
        )
        return RuntimeProxyResponse(
            status=upstream.status,
            content_type=upstream.content_type,
            body=upstream.body(),
        )


_service: ModelControlPlaneService | None = None


def configure_model_control_plane(service: ModelControlPlaneService) -> None:
    global _service
    _service = service


def configured_model_control_plane() -> ModelControlPlaneService:
    if _service is None:
        raise RuntimeError("model_control_plane_service_not_configured")
    return _service
