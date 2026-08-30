"""Application authority for Knowledge connections and source catalogs."""

from __future__ import annotations

from contextlib import AbstractAsyncContextManager
import hashlib
import json
import math
from typing import Any, Awaitable, Callable, Protocol
import unicodedata
import uuid

from app.knowledge.domain import (
    KnowledgeConnectionDefinition,
    KnowledgeError,
    canonical_connection_name,
    canonical_knowledge_role_id,
    canonical_knowledge_user_id,
    canonical_origin,
)
from .provider import KnowledgeProvider


class TransactionFactory(Protocol):
    def __call__(self) -> AbstractAsyncContextManager[Any]: ...


class KnowledgeRepository(Protocol):
    async def get_connection_by_create_operation(self, conn: Any, **kwargs: Any) -> Any: ...

    async def create_connection(self, conn: Any, **kwargs: Any) -> dict[str, Any]: ...

    async def rotate_candidate(self, conn: Any, **kwargs: Any) -> Any: ...

    async def lock_connection_for_rotation(self, conn: Any, **kwargs: Any) -> Any: ...

    async def get_connection(self, conn: Any, **kwargs: Any) -> Any: ...

    async def list_connections(self, conn: Any, **kwargs: Any) -> dict[str, Any]: ...

    async def record_check(self, conn: Any, **kwargs: Any) -> Any: ...

    async def claim_connection_check(self, conn: Any, **kwargs: Any) -> dict[str, Any]: ...

    async def finish_connection_check(self, conn: Any, **kwargs: Any) -> dict[str, Any]: ...

    async def claim_catalog_sync(self, conn: Any, **kwargs: Any) -> dict[str, Any]: ...

    async def commit_catalog(self, conn: Any, **kwargs: Any) -> dict[str, Any]: ...

    async def fail_catalog_sync(self, conn: Any, **kwargs: Any) -> dict[str, Any]: ...

    async def get_sync(self, conn: Any, **kwargs: Any) -> Any: ...

    async def disable_connection(self, conn: Any, **kwargs: Any) -> Any: ...

    async def list_sources(self, conn: Any, **kwargs: Any) -> dict[str, Any]: ...

    async def list_builder_catalog(self, conn: Any, **kwargs: Any) -> dict[str, Any]: ...

    async def get_source(self, conn: Any, **kwargs: Any) -> Any: ...

    async def update_source(self, conn: Any, **kwargs: Any) -> Any: ...

    async def replace_source_acl(self, conn: Any, **kwargs: Any) -> Any: ...

    async def get_source_acl_by_operation(self, conn: Any, **kwargs: Any) -> Any: ...


class CredentialVault(Protocol):
    async def store(self, conn: Any, **kwargs: Any) -> Any: ...

    async def resolve(self, conn: Any, **kwargs: Any) -> str: ...


class AuditWriter(Protocol):
    async def append(self, conn: Any, **kwargs: Any) -> str: ...


class KnowledgeControlPlane:
    def __init__(
        self,
        *,
        transaction_factory: TransactionFactory,
        settings_provider: Any,
        repository: KnowledgeRepository,
        credential_vault: CredentialVault,
        audit_writer: AuditWriter,
        providers: tuple[KnowledgeProvider, ...],
        department_authority_validator: Callable[[list[str]], Awaitable[list[str]]]
        | None = None,
    ) -> None:
        self._transaction = transaction_factory
        self._settings_provider = settings_provider
        self._repository = repository
        self._credential_vault = credential_vault
        self._audit_writer = audit_writer
        self._department_authority_validator = department_authority_validator
        self._providers = {provider.provider_key: provider for provider in providers}
        if set(self._providers) != {provider.provider_key for provider in providers}:
            raise RuntimeError("knowledge_provider_registry_duplicate")
        if "ragflow" not in self._providers:
            raise RuntimeError("knowledge_provider_registry_incomplete")

    def _transport_policy(self) -> dict[str, Any]:
        return {
            "timeout_seconds": float(
                self._settings_provider().knowledge_provider_timeout_seconds
            ),
            "follow_redirects": False,
        }

    @staticmethod
    def _provider_failure(exc: Exception) -> KnowledgeError:
        if isinstance(exc, KnowledgeError):
            return exc
        return KnowledgeError("knowledge_provider_unavailable")

    @staticmethod
    def _request_hash(value: dict[str, Any]) -> str:
        return hashlib.sha256(
            json.dumps(
                value,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()

    @staticmethod
    def _credential_fingerprint(value: str) -> str:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]

    async def create_connection(
        self,
        *,
        tenant_id: str,
        actor_id: str,
        operation_id: str,
        name: str,
        base_url: str,
        credential: str,
    ) -> dict[str, Any]:
        normalized_name = canonical_connection_name(name)
        normalized_url = canonical_origin(base_url)
        request_hash = self._request_hash(
            {
                "base_url": normalized_url,
                "credential_fingerprint": self._credential_fingerprint(credential.strip()),
                "name": normalized_name,
                "provider_key": "ragflow",
            }
        )
        async with self._transaction() as conn:
            existing = await self._repository.get_connection_by_create_operation(
                conn,
                tenant_id=tenant_id,
                operation_id=operation_id,
            )
            if existing is not None:
                replay_hash = str(existing.pop("_create_request_hash", ""))
                if replay_hash != request_hash:
                    raise KnowledgeError("knowledge_operation_identity_reused")
                return existing
            stored = await self._credential_vault.store(
                conn,
                tenant_id=tenant_id,
                purpose="knowledge_provider",
                value=credential,
                actor_id=actor_id,
            )
            definition = KnowledgeConnectionDefinition(
                provider_key="ragflow",
                base_url=normalized_url,
                secret_ref=stored.secret_ref,
                transport_policy=self._transport_policy(),
            )
            result = await self._repository.create_connection(
                conn,
                tenant_id=tenant_id,
                name=normalized_name,
                definition=definition,
                actor_id=actor_id,
                operation_id=operation_id,
                request_hash=request_hash,
            )
            await self._audit_writer.append(
                conn,
                tenant_id=tenant_id,
                actor_id=actor_id,
                action="knowledge.connection.created",
                target_type="knowledge_connection",
                target_id=str(result["id"]),
                operation_id=operation_id,
                payload={"status": str(result["status"])},
            )
            return result

    async def rotate_credential(
        self,
        *,
        tenant_id: str,
        actor_id: str,
        connection_id: str,
        operation_id: str,
        credential: str,
    ) -> dict[str, Any] | None:
        async with self._transaction() as conn:
            connection = await self._repository.lock_connection_for_rotation(
                conn,
                tenant_id=tenant_id,
                connection_id=connection_id,
                operation_id=operation_id,
                credential_fingerprint=self._credential_fingerprint(credential.strip()),
            )
            if connection is None:
                return None
            if connection["replayed"]:
                return await self._repository.get_connection(
                    conn,
                    tenant_id=tenant_id,
                    connection_id=connection_id,
                )
            stored = await self._credential_vault.store(
                conn,
                tenant_id=tenant_id,
                purpose="knowledge_provider",
                value=credential,
                actor_id=actor_id,
            )
            definition = KnowledgeConnectionDefinition(
                provider_key=str(connection["provider_key"]),
                base_url=canonical_origin(str(connection["base_url"])),
                secret_ref=stored.secret_ref,
                transport_policy=self._transport_policy(),
            )
            result = await self._repository.rotate_candidate(
                conn,
                tenant_id=tenant_id,
                connection_id=connection_id,
                definition=definition,
                actor_id=actor_id,
                operation_id=operation_id,
            )
            await self._audit_writer.append(
                conn,
                tenant_id=tenant_id,
                actor_id=actor_id,
                action="knowledge.connection.credential_rotated",
                target_type="knowledge_connection",
                target_id=connection_id,
                operation_id=operation_id,
                payload={"status": str(result["status"])},
            )
            return result

    def _provider(self, key: str) -> KnowledgeProvider:
        provider = self._providers.get(key)
        if provider is None:
            raise KnowledgeError("knowledge_provider_unsupported")
        return provider

    async def _claim_catalog(
        self,
        *,
        tenant_id: str,
        actor_id: str,
        connection_id: str,
        operation_id: str,
        purpose: str,
    ) -> tuple[dict[str, Any], str | None]:
        lease_owner = f"knowledge_{uuid.uuid4().hex}"
        lease_seconds = max(
            5,
            math.ceil(float(self._settings_provider().knowledge_provider_timeout_seconds)) + 5,
        )
        async with self._transaction() as conn:
            claim = await self._repository.claim_catalog_sync(
                conn,
                tenant_id=tenant_id,
                connection_id=connection_id,
                purpose=purpose,
                operation_id=operation_id,
                actor_id=actor_id,
                lease_owner=lease_owner,
                lease_seconds=lease_seconds,
            )
            if not claim["claimed"]:
                return claim, None
            revision = claim["revision"]
            credential = await self._credential_vault.resolve(
                conn,
                tenant_id=tenant_id,
                secret_ref=str(revision["secret_ref"]),
                purpose="knowledge_provider",
            )
        return claim, credential

    @staticmethod
    def _replayed_sync(claim: dict[str, Any]) -> dict[str, Any]:
        sync = claim["sync"]
        status = str(sync["status"])
        if status == "succeeded":
            return sync
        if status == "failed":
            raise KnowledgeError(str(sync.get("safe_failure_code") or "knowledge_sync_failed"))
        if status == "reconcile_required":
            raise KnowledgeError("knowledge_sync_reconcile_required")
        raise KnowledgeError("knowledge_sync_in_progress")

    async def _fail_catalog(
        self,
        *,
        tenant_id: str,
        actor_id: str,
        connection_id: str,
        operation_id: str,
        purpose: str,
        claim: dict[str, Any],
        failure_code: str,
    ) -> None:
        async with self._transaction() as conn:
            sync = await self._repository.fail_catalog_sync(
                conn,
                tenant_id=tenant_id,
                connection_id=connection_id,
                sync_id=str(claim["sync"]["id"]),
                lease_owner=str(claim["lease_owner"]),
                lease_generation=int(claim["lease_generation"]),
                failure_code=failure_code,
            )
            await self._audit_writer.append(
                conn,
                tenant_id=tenant_id,
                actor_id=actor_id,
                action="knowledge.catalog_sync.failed",
                target_type="knowledge_connection",
                target_id=connection_id,
                operation_id=operation_id,
                payload={
                    "failure_code": failure_code,
                    "purpose": purpose,
                    "sync_id": str(sync["id"]),
                },
            )

    async def check_connection(
        self,
        *,
        tenant_id: str,
        actor_id: str,
        connection_id: str,
        operation_id: str,
    ) -> dict[str, Any]:
        lease_owner = f"knowledge_{uuid.uuid4().hex}"
        lease_seconds = max(
            5,
            math.ceil(float(self._settings_provider().knowledge_provider_timeout_seconds)) + 5,
        )
        async with self._transaction() as conn:
            claim = await self._repository.claim_connection_check(
                conn,
                tenant_id=tenant_id,
                connection_id=connection_id,
                operation_id=operation_id,
                actor_id=actor_id,
                lease_owner=lease_owner,
                lease_seconds=lease_seconds,
            )
            if not claim["claimed"]:
                status = str(claim["status"])
                if status == "passed":
                    connection = await self._repository.get_connection(
                        conn,
                        tenant_id=tenant_id,
                        connection_id=connection_id,
                    )
                    return {"status": "passed", "connection": connection}
                if status == "failed":
                    raise KnowledgeError(
                        str(claim.get("safe_failure_code") or "knowledge_connection_check_failed")
                    )
                if status == "reconcile_required":
                    raise KnowledgeError("knowledge_check_reconcile_required")
                raise KnowledgeError("knowledge_check_in_progress")
            revision = claim["revision"]
            credential = await self._credential_vault.resolve(
                conn,
                tenant_id=tenant_id,
                secret_ref=str(revision["secret_ref"]),
                purpose="knowledge_provider",
            )
        provider = self._provider(str(revision["provider_key"]))
        try:
            await provider.check(base_url=str(revision["base_url"]), credential=credential)
        except Exception as exc:
            failure = self._provider_failure(exc)
            async with self._transaction() as conn:
                result = await self._repository.finish_connection_check(
                    conn,
                    tenant_id=tenant_id,
                    connection_id=connection_id,
                    operation_id=operation_id,
                    revision_id=str(revision["revision_id"]),
                    lease_owner=str(claim["lease_owner"]),
                    lease_generation=int(claim["lease_generation"]),
                    passed=False,
                    failure_code=failure.code,
                )
                await self._audit_writer.append(
                    conn,
                    tenant_id=tenant_id,
                    actor_id=actor_id,
                    action="knowledge.connection.checked",
                    target_type="knowledge_connection",
                    target_id=connection_id,
                    operation_id=operation_id,
                    payload={"failure_code": failure.code, "outcome": result["status"]},
                )
            if isinstance(exc, KnowledgeError):
                raise
            raise failure from exc
        async with self._transaction() as conn:
            result = await self._repository.finish_connection_check(
                conn,
                tenant_id=tenant_id,
                connection_id=connection_id,
                operation_id=operation_id,
                revision_id=str(revision["revision_id"]),
                lease_owner=str(claim["lease_owner"]),
                lease_generation=int(claim["lease_generation"]),
                passed=True,
                failure_code=None,
            )
            await self._audit_writer.append(
                conn,
                tenant_id=tenant_id,
                actor_id=actor_id,
                action="knowledge.connection.checked",
                target_type="knowledge_connection",
                target_id=connection_id,
                operation_id=operation_id,
                payload={"outcome": result["status"]},
            )
            return result

    async def activate_candidate(
        self,
        *,
        tenant_id: str,
        actor_id: str,
        connection_id: str,
        operation_id: str,
    ) -> dict[str, Any]:
        claim, credential = await self._claim_catalog(
            tenant_id=tenant_id,
            actor_id=actor_id,
            connection_id=connection_id,
            operation_id=operation_id,
            purpose="candidate_activation",
        )
        if not claim["claimed"]:
            sync = self._replayed_sync(claim)
            async with self._transaction() as conn:
                connection = await self._repository.get_connection(
                    conn,
                    tenant_id=tenant_id,
                    connection_id=connection_id,
                )
            return {"connection": connection, "sync": sync}
        assert credential is not None
        revision = claim["revision"]
        provider = self._provider(str(revision["provider_key"]))
        try:
            await provider.check(base_url=str(revision["base_url"]), credential=credential)
        except Exception as exc:
            failure = self._provider_failure(exc)
            async with self._transaction() as conn:
                await self._repository.record_check(
                    conn,
                    tenant_id=tenant_id,
                    connection_id=connection_id,
                    revision_id=str(revision["revision_id"]),
                    passed=False,
                    failure_code=failure.code,
                    cataloging=False,
                )
                await self._repository.fail_catalog_sync(
                    conn,
                    tenant_id=tenant_id,
                    connection_id=connection_id,
                    sync_id=str(claim["sync"]["id"]),
                    lease_owner=str(claim["lease_owner"]),
                    lease_generation=int(claim["lease_generation"]),
                    failure_code=failure.code,
                )
                await self._audit_writer.append(
                    conn,
                    tenant_id=tenant_id,
                    actor_id=actor_id,
                    action="knowledge.catalog_sync.failed",
                    target_type="knowledge_connection",
                    target_id=connection_id,
                    operation_id=operation_id,
                    payload={
                        "failure_code": failure.code,
                        "purpose": "candidate_activation",
                        "sync_id": str(claim["sync"]["id"]),
                    },
                )
            if isinstance(exc, KnowledgeError):
                raise
            raise failure from exc
        async with self._transaction() as conn:
            await self._repository.record_check(
                conn,
                tenant_id=tenant_id,
                connection_id=connection_id,
                revision_id=str(revision["revision_id"]),
                passed=True,
                failure_code=None,
                cataloging=True,
            )
        try:
            snapshot = await provider.list_sources(
                base_url=str(revision["base_url"]),
                credential=credential,
            )
        except Exception as exc:
            failure = self._provider_failure(exc)
            await self._fail_catalog(
                tenant_id=tenant_id,
                actor_id=actor_id,
                connection_id=connection_id,
                operation_id=operation_id,
                purpose="candidate_activation",
                claim=claim,
                failure_code=failure.code,
            )
            if isinstance(exc, KnowledgeError):
                raise
            raise failure from exc
        async with self._transaction() as conn:
            sync = await self._repository.commit_catalog(
                conn,
                tenant_id=tenant_id,
                connection_id=connection_id,
                revision_id=str(revision["revision_id"]),
                purpose="candidate_activation",
                operation_id=operation_id,
                sync_id=str(claim["sync"]["id"]),
                lease_owner=str(claim["lease_owner"]),
                lease_generation=int(claim["lease_generation"]),
                actor_id=actor_id,
                records=snapshot.records,
                page_count=snapshot.page_count,
            )
            connection = await self._repository.get_connection(
                conn,
                tenant_id=tenant_id,
                connection_id=connection_id,
            )
            await self._audit_writer.append(
                conn,
                tenant_id=tenant_id,
                actor_id=actor_id,
                action="knowledge.connection.activated",
                target_type="knowledge_connection",
                target_id=connection_id,
                operation_id=operation_id,
                payload={
                    "observed_count": int(sync["observed_count"]),
                    "sync_id": str(sync["id"]),
                },
            )
        return {"connection": connection, "sync": sync}

    async def sync_connection(
        self,
        *,
        tenant_id: str,
        actor_id: str,
        connection_id: str,
        operation_id: str,
    ) -> dict[str, Any]:
        claim, credential = await self._claim_catalog(
            tenant_id=tenant_id,
            actor_id=actor_id,
            connection_id=connection_id,
            operation_id=operation_id,
            purpose="manual_active_refresh",
        )
        if not claim["claimed"]:
            return self._replayed_sync(claim)
        assert credential is not None
        revision = claim["revision"]
        try:
            snapshot = await self._provider(str(revision["provider_key"])).list_sources(
                base_url=str(revision["base_url"]),
                credential=credential,
            )
        except Exception as exc:
            failure = self._provider_failure(exc)
            await self._fail_catalog(
                tenant_id=tenant_id,
                actor_id=actor_id,
                connection_id=connection_id,
                operation_id=operation_id,
                purpose="manual_active_refresh",
                claim=claim,
                failure_code=failure.code,
            )
            if isinstance(exc, KnowledgeError):
                raise
            raise failure from exc
        async with self._transaction() as conn:
            sync = await self._repository.commit_catalog(
                conn,
                tenant_id=tenant_id,
                connection_id=connection_id,
                revision_id=str(revision["revision_id"]),
                purpose="manual_active_refresh",
                operation_id=operation_id,
                sync_id=str(claim["sync"]["id"]),
                lease_owner=str(claim["lease_owner"]),
                lease_generation=int(claim["lease_generation"]),
                actor_id=actor_id,
                records=snapshot.records,
                page_count=snapshot.page_count,
            )
            await self._audit_writer.append(
                conn,
                tenant_id=tenant_id,
                actor_id=actor_id,
                action="knowledge.catalog_sync.completed",
                target_type="knowledge_connection",
                target_id=connection_id,
                operation_id=operation_id,
                payload={
                    "observed_count": int(sync["observed_count"]),
                    "sync_id": str(sync["id"]),
                },
            )
            return sync

    async def list_connections(self, **kwargs: Any) -> dict[str, Any]:
        async with self._transaction() as conn:
            return await self._repository.list_connections(conn, **kwargs)

    async def get_connection(self, **kwargs: Any) -> dict[str, Any] | None:
        async with self._transaction() as conn:
            return await self._repository.get_connection(conn, **kwargs)

    async def get_sync(self, **kwargs: Any) -> dict[str, Any] | None:
        async with self._transaction() as conn:
            return await self._repository.get_sync(conn, **kwargs)

    async def disable_connection(
        self,
        *,
        tenant_id: str,
        actor_id: str,
        connection_id: str,
        operation_id: str,
    ) -> dict[str, Any] | None:
        async with self._transaction() as conn:
            result = await self._repository.disable_connection(
                conn,
                tenant_id=tenant_id,
                actor_id=actor_id,
                connection_id=connection_id,
                operation_id=operation_id,
            )
            if result is not None:
                await self._audit_writer.append(
                    conn,
                    tenant_id=tenant_id,
                    actor_id=actor_id,
                    action="knowledge.connection.disabled",
                    target_type="knowledge_connection",
                    target_id=connection_id,
                    operation_id=operation_id,
                    payload={"status": str(result["status"])},
                )
            return result

    async def list_sources(self, **kwargs: Any) -> dict[str, Any]:
        async with self._transaction() as conn:
            return await self._repository.list_sources(conn, **kwargs)

    async def list_builder_catalog(self, **kwargs: Any) -> dict[str, Any]:
        async with self._transaction() as conn:
            return await self._repository.list_builder_catalog(conn, **kwargs)

    async def get_source(self, **kwargs: Any) -> dict[str, Any] | None:
        async with self._transaction() as conn:
            return await self._repository.get_source(conn, **kwargs)

    async def update_source(
        self,
        *,
        tenant_id: str,
        actor_id: str,
        source_id: str,
        operation_id: str,
        display_name_present: bool,
        display_name: str | None,
        description_present: bool,
        description: str | None,
        status: str | None,
    ) -> dict[str, Any] | None:
        normalized_name: str | None = None
        if display_name_present:
            normalized_name = " ".join((display_name or "").split()) or None
            if normalized_name and len(normalized_name) > 240:
                raise KnowledgeError("knowledge_source_display_name_invalid")
        normalized_description: str | None = None
        if description_present:
            normalized_description = (description or "").strip() or None
            if normalized_description and len(normalized_description) > 1000:
                raise KnowledgeError("knowledge_source_description_invalid")
        if status is not None and status not in {"active", "disabled"}:
            raise KnowledgeError("knowledge_source_status_invalid")
        request_hash = hashlib.sha256(
            json.dumps(
                {
                    "description": normalized_description if description_present else None,
                    "description_present": description_present,
                    "display_name": normalized_name if display_name_present else None,
                    "display_name_present": display_name_present,
                    "status": status,
                },
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()
        async with self._transaction() as conn:
            result = await self._repository.update_source(
                conn,
                tenant_id=tenant_id,
                source_id=source_id,
                display_name_present=display_name_present,
                display_name=normalized_name if display_name_present else None,
                description_present=description_present,
                description=normalized_description if description_present else None,
                status=status,
                operation_id=operation_id,
                request_hash=request_hash,
                actor_id=actor_id,
            )
            if result is not None:
                await self._audit_writer.append(
                    conn,
                    tenant_id=tenant_id,
                    actor_id=actor_id,
                    action="knowledge.source.updated",
                    target_type="knowledge_source",
                    target_id=source_id,
                    operation_id=operation_id,
                    payload={"status": str(result["status"])},
                )
            return result

    async def replace_source_acl(
        self,
        *,
        tenant_id: str,
        actor_id: str,
        source_id: str,
        operation_id: str,
        expected_version: int,
        visibility: str,
        department_ids: list[str],
    ) -> dict[str, Any] | None:
        if visibility not in {"enterprise", "restricted"}:
            raise KnowledgeError("knowledge_source_visibility_invalid")
        normalized_departments: list[str] = []
        for value in department_ids:
            candidate = value.strip()
            if (
                not candidate
                or len(candidate) > 160
                or any(unicodedata.category(character).startswith("C") for character in candidate)
            ):
                raise KnowledgeError("knowledge_source_acl_identity_invalid")
            if candidate not in normalized_departments:
                normalized_departments.append(candidate)
        async with self._transaction() as conn:
            source = await self._repository.get_source(
                conn,
                tenant_id=tenant_id,
                source_id=source_id,
            )
        if source is None:
            return None
        preserved_roles = source.get("allowed_roles", []) if visibility == "restricted" else []
        preserved_users = source.get("allowed_user_ids", []) if visibility == "restricted" else []
        normalized_roles: list[str] = []
        for value in preserved_roles:
            candidate = canonical_knowledge_role_id(value)
            if candidate not in normalized_roles:
                normalized_roles.append(candidate)
        normalized_users: list[str] = []
        for value in preserved_users:
            candidate = canonical_knowledge_user_id(value)
            if candidate not in normalized_users:
                normalized_users.append(candidate)
        normalized = [
            tuple(sorted(normalized_departments)),
            tuple(sorted(normalized_roles)),
            tuple(sorted(normalized_users)),
        ]
        if visibility == "enterprise" and normalized[0]:
            raise KnowledgeError("knowledge_source_acl_enterprise_scope_invalid")
        if visibility == "restricted" and not any(normalized):
            raise KnowledgeError("knowledge_source_acl_scope_required")
        if normalized[0]:
            if self._department_authority_validator is None:
                raise KnowledgeError("knowledge_source_acl_identity_authority_unavailable")
            normalized[0] = tuple(
                await self._department_authority_validator(list(normalized[0]))
            )
        content_hash = self._request_hash(
            {
                "department_ids": normalized[0],
                "roles": normalized[1],
                "user_ids": normalized[2],
                "visibility": visibility,
            }
        )
        async with self._transaction() as conn:
            replay = await self._repository.get_source_acl_by_operation(
                conn,
                tenant_id=tenant_id,
                source_id=source_id,
                operation_id=operation_id,
            )
            if replay is not None:
                if replay["content_hash"] != content_hash:
                    raise KnowledgeError("knowledge_operation_identity_reused")
                return replay["source"]
            result = await self._repository.replace_source_acl(
                conn,
                tenant_id=tenant_id,
                source_id=source_id,
                expected_version=expected_version,
                visibility=visibility,
                department_ids=normalized[0],
                roles=normalized[1],
                user_ids=normalized[2],
                actor_id=actor_id,
                operation_id=operation_id,
                content_hash=content_hash,
            )
            if result is not None:
                await self._audit_writer.append(
                    conn,
                    tenant_id=tenant_id,
                    actor_id=actor_id,
                    action="knowledge.source_acl.replaced",
                    target_type="knowledge_source",
                    target_id=source_id,
                    operation_id=operation_id,
                    payload={
                        "authorization_version": int(result["authorization_version"]),
                        "department_count": len(normalized[0]),
                        "visibility": visibility,
                    },
                )
            return result


_configured_service: KnowledgeControlPlane | None = None


def configure_knowledge_control_plane(service: KnowledgeControlPlane) -> None:
    global _configured_service
    _configured_service = service


def configured_knowledge_control_plane() -> KnowledgeControlPlane:
    if _configured_service is None:
        raise RuntimeError("knowledge_control_plane_not_configured")
    return _configured_service
