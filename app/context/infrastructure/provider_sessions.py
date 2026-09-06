from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from psycopg import AsyncConnection

from app.context.domain.provider_sessions import (
    MAX_PROVIDER_SESSION_ENTRIES,
    MAX_PROVIDER_SESSION_TRANSCRIPT_BYTES,
    ProviderSessionConflictError,
    ProviderSessionContinuityError,
    ProviderSessionNotFoundError,
    ProviderSessionScope,
    normalize_provider_entry_batch,
    normalize_provider_subpath,
    provider_entry_json_bytes,
    provider_session_id_for_scope,
)


def _scope(
    *,
    tenant_id: str,
    workspace_id: str,
    user_id: str,
    session_id: str,
    agent_id: str,
    engine: str = "claude",
) -> ProviderSessionScope:
    return ProviderSessionScope(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        user_id=user_id,
        session_id=session_id,
        agent_id=agent_id,
        engine=engine,
    )


def _repository_error(error: ProviderSessionContinuityError) -> ProviderSessionConflictError:
    return ProviderSessionConflictError(error.code)


def _row_dict(row: Any) -> dict[str, Any] | None:
    if row is None:
        return None
    return dict(row) if not isinstance(row, dict) else row


def _binding_values(scope: ProviderSessionScope, provider_session_id: str) -> tuple[Any, ...]:
    return (
        scope.tenant_id,
        scope.workspace_id,
        scope.user_id,
        scope.session_id,
        scope.agent_id,
        scope.engine,
        provider_session_id,
    )


def _validate_provider_id(scope: ProviderSessionScope, provider_session_id: str) -> str:
    expected = provider_session_id_for_scope(scope)
    if str(provider_session_id or "").strip() != expected:
        raise ProviderSessionConflictError("provider_session_identity_mismatch")
    return expected


async def ensure_provider_session_binding(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    workspace_id: str,
    user_id: str,
    session_id: str,
    agent_id: str,
    engine: str = "claude",
) -> dict[str, Any]:
    """Create or load the deterministic provider binding for one live Session."""
    try:
        scope = _scope(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            user_id=user_id,
            session_id=session_id,
            agent_id=agent_id,
            engine=engine,
        )
        provider_session_id = provider_session_id_for_scope(scope)
    except ProviderSessionContinuityError as exc:
        raise _repository_error(exc) from exc

    cursor = await conn.execute(
        """
        insert into provider_session_bindings(
          tenant_id, workspace_id, user_id, session_id, agent_id, engine,
          provider_session_id, context_epoch, next_sequence
        )
        select %s, %s, %s, %s, %s, %s, %s::uuid, 1, 1
        from sessions
        where sessions.tenant_id = %s
          and sessions.workspace_id = %s
          and sessions.user_id = %s
          and sessions.id = %s
          and sessions.agent_id = %s
          and sessions.status = 'active'
        on conflict (tenant_id, session_id, engine) do update
        set updated_at = now()
        where provider_session_bindings.workspace_id = excluded.workspace_id
          and provider_session_bindings.user_id = excluded.user_id
          and provider_session_bindings.agent_id = excluded.agent_id
          and provider_session_bindings.provider_session_id = excluded.provider_session_id
        returning tenant_id, workspace_id, user_id, session_id, agent_id, engine,
                  provider_session_id, context_epoch, next_sequence,
                  writer_run_id, writer_attempt_id, created_at, updated_at
        """,
        (
            scope.tenant_id,
            scope.workspace_id,
            scope.user_id,
            scope.session_id,
            scope.agent_id,
            scope.engine,
            provider_session_id,
            scope.tenant_id,
            scope.workspace_id,
            scope.user_id,
            scope.session_id,
            scope.agent_id,
        ),
    )
    row = _row_dict(await cursor.fetchone())
    if row is None:
        raise ProviderSessionConflictError("provider_session_binding_scope_invalid")
    actual_provider_id = str(row.get("provider_session_id") or "")
    if actual_provider_id != provider_session_id:
        raise ProviderSessionConflictError("provider_session_identity_mismatch")
    return row


async def get_provider_session_binding(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    workspace_id: str,
    user_id: str,
    session_id: str,
    agent_id: str,
    provider_session_id: str | None = None,
    engine: str = "claude",
    for_update: bool = False,
) -> dict[str, Any] | None:
    try:
        scope = _scope(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            user_id=user_id,
            session_id=session_id,
            agent_id=agent_id,
            engine=engine,
        )
        expected_provider_id = provider_session_id_for_scope(scope)
        if provider_session_id is not None:
            _validate_provider_id(scope, provider_session_id)
    except ProviderSessionContinuityError as exc:
        raise _repository_error(exc) from exc

    lock_clause = " for update" if for_update else ""
    cursor = await conn.execute(
        f"""
        select tenant_id, workspace_id, user_id, session_id, agent_id, engine,
               provider_session_id, context_epoch, next_sequence,
               writer_run_id, writer_attempt_id, created_at, updated_at
        from provider_session_bindings
        where tenant_id = %s
          and workspace_id = %s
          and user_id = %s
          and session_id = %s
          and agent_id = %s
          and engine = %s
          and provider_session_id = %s::uuid
        {lock_clause}
        """,
        _binding_values(scope, expected_provider_id),
    )
    return _row_dict(await cursor.fetchone())


async def provider_session_binding_for_context(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    workspace_id: str,
    user_id: str,
    session_id: str,
    agent_id: str,
    engine: str = "claude",
) -> dict[str, Any] | None:
    """Compatibility-shaped lookup used by worker context materialization."""
    return await get_provider_session_binding(
        conn,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        user_id=user_id,
        session_id=session_id,
        agent_id=agent_id,
        engine=engine,
    )


async def provider_session_has_main_transcript(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    workspace_id: str,
    user_id: str,
    session_id: str,
    agent_id: str,
    provider_session_id: str | None = None,
    engine: str = "claude",
) -> bool:
    binding = await get_provider_session_binding(
        conn,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        user_id=user_id,
        session_id=session_id,
        agent_id=agent_id,
        provider_session_id=provider_session_id,
        engine=engine,
    )
    if binding is None:
        return False
    cursor = await conn.execute(
        """
        select exists (
          select 1
          from provider_session_entries
          where tenant_id = %s
            and workspace_id = %s
            and user_id = %s
            and session_id = %s
            and agent_id = %s
            and engine = %s
            and provider_session_id = %s::uuid
            and subpath = ''
        ) as has_main_transcript
        """,
        _binding_values(
            _scope(
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                user_id=user_id,
                session_id=session_id,
                agent_id=agent_id,
                engine=engine,
            ),
            str(binding["provider_session_id"]),
        ),
    )
    row = _row_dict(await cursor.fetchone()) or {}
    return bool(row.get("has_main_transcript"))


async def claim_provider_session_writer(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    workspace_id: str,
    user_id: str,
    session_id: str,
    agent_id: str,
    run_id: str,
    attempt_id: str,
    provider_session_id: str | None = None,
    engine: str = "claude",
) -> dict[str, Any]:
    """Atomically fence one active attempt as the sole transcript writer."""
    if not isinstance(run_id, str) or not run_id.strip() or not isinstance(attempt_id, str) or not attempt_id.strip():
        raise ProviderSessionConflictError("provider_session_writer_identity_invalid")
    try:
        scope = _scope(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            user_id=user_id,
            session_id=session_id,
            agent_id=agent_id,
            engine=engine,
        )
        expected_provider_id = provider_session_id_for_scope(scope)
        if provider_session_id is not None:
            _validate_provider_id(scope, provider_session_id)
    except ProviderSessionContinuityError as exc:
        raise _repository_error(exc) from exc

    cursor = await conn.execute(
        """
        update provider_session_bindings binding
        set writer_run_id = %s,
            writer_attempt_id = %s,
            updated_at = now()
        where binding.tenant_id = %s
          and binding.workspace_id = %s
          and binding.user_id = %s
          and binding.session_id = %s
          and binding.agent_id = %s
          and binding.engine = %s
          and binding.provider_session_id = %s::uuid
          and (
            (binding.writer_run_id = %s and binding.writer_attempt_id = %s)
            or binding.writer_attempt_id is null
            or not exists (
              select 1
              from sandbox_leases owner_lease
              where owner_lease.tenant_id = binding.tenant_id
                and owner_lease.workspace_id = binding.workspace_id
                and owner_lease.user_id = binding.user_id
                and owner_lease.session_id = binding.session_id
                and owner_lease.run_id = binding.writer_run_id
                and owner_lease.attempt_id = binding.writer_attempt_id
                and owner_lease.status = 'active'
                and owner_lease.released_at is null
                and (owner_lease.expires_at is null or owner_lease.expires_at > now())
            )
          )
          and exists (
            select 1
            from sandbox_leases requested_lease
            where requested_lease.tenant_id = binding.tenant_id
              and requested_lease.workspace_id = binding.workspace_id
              and requested_lease.user_id = binding.user_id
              and requested_lease.session_id = binding.session_id
              and requested_lease.run_id = %s
              and requested_lease.attempt_id = %s
              and requested_lease.status = 'active'
              and requested_lease.released_at is null
              and (requested_lease.expires_at is null or requested_lease.expires_at > now())
          )
        returning tenant_id, workspace_id, user_id, session_id, agent_id, engine,
                  provider_session_id, context_epoch, next_sequence,
                  writer_run_id, writer_attempt_id, created_at, updated_at
        """,
        (
            run_id,
            attempt_id,
            *(_binding_values(scope, expected_provider_id)),
            run_id,
            attempt_id,
            run_id,
            attempt_id,
        ),
    )
    row = _row_dict(await cursor.fetchone())
    if row is None:
        existing = await get_provider_session_binding(
            conn,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            user_id=user_id,
            session_id=session_id,
            agent_id=agent_id,
            provider_session_id=expected_provider_id,
            engine=engine,
        )
        if existing is None:
            raise ProviderSessionNotFoundError("provider_session_binding_not_found")
        raise ProviderSessionConflictError("provider_session_writer_conflict")
    return row


async def _assert_writer(
    conn: AsyncConnection,
    *,
    scope: ProviderSessionScope,
    provider_session_id: str,
    run_id: str,
    attempt_id: str,
) -> None:
    cursor = await conn.execute(
        """
        select 1 as authorized
        from provider_session_bindings binding
        where binding.tenant_id = %s
          and binding.workspace_id = %s
          and binding.user_id = %s
          and binding.session_id = %s
          and binding.agent_id = %s
          and binding.engine = %s
          and binding.provider_session_id = %s::uuid
          and binding.writer_run_id = %s
          and binding.writer_attempt_id = %s
          and exists (
            select 1 from sandbox_leases lease
            where lease.tenant_id = binding.tenant_id
              and lease.workspace_id = binding.workspace_id
              and lease.user_id = binding.user_id
              and lease.session_id = binding.session_id
              and lease.run_id = %s
              and lease.attempt_id = %s
              and lease.status = 'active'
              and lease.released_at is null
              and (lease.expires_at is null or lease.expires_at > now())
          )
        """,
        (
            *(_binding_values(scope, provider_session_id)),
            run_id,
            attempt_id,
            run_id,
            attempt_id,
        ),
    )
    if _row_dict(await cursor.fetchone()) is None:
        raise ProviderSessionConflictError("provider_session_writer_conflict")


async def append_provider_session_entries(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    workspace_id: str,
    user_id: str,
    session_id: str,
    agent_id: str,
    run_id: str,
    attempt_id: str,
    entries: list[Mapping[str, Any]],
    subpath: object = None,
    provider_session_id: str | None = None,
    engine: str = "claude",
) -> list[dict[str, Any]]:
    """Append an authenticated batch after writer and lease checks.

    The caller owns the transaction and must commit before returning callback
    success. UUID-bearing rows are looked up before insert, making retries
    idempotent while UUID-less rows remain append-only.
    """
    try:
        normalized_entries, _ = normalize_provider_entry_batch(entries, subpath=subpath)
        normalized_subpath = normalize_provider_subpath(subpath) or ""
        scope = _scope(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            user_id=user_id,
            session_id=session_id,
            agent_id=agent_id,
            engine=engine,
        )
        expected_provider_id = provider_session_id_for_scope(scope)
        if provider_session_id is not None:
            _validate_provider_id(scope, provider_session_id)
    except ProviderSessionContinuityError as exc:
        raise _repository_error(exc) from exc

    binding = await get_provider_session_binding(
        conn,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        user_id=user_id,
        session_id=session_id,
        agent_id=agent_id,
        provider_session_id=expected_provider_id,
        engine=engine,
        for_update=True,
    )
    if binding is None:
        raise ProviderSessionNotFoundError("provider_session_binding_not_found")
    await _assert_writer(
        conn,
        scope=scope,
        provider_session_id=expected_provider_id,
        run_id=run_id,
        attempt_id=attempt_id,
    )

    result: list[dict[str, Any]] = []
    cursor = await conn.execute(
        """
        select id, tenant_id, workspace_id, user_id, session_id, agent_id, engine,
               provider_session_id, subpath, sequence, sdk_entry_uuid, entry_json, created_at
        from provider_session_entries
        where tenant_id = %s
          and workspace_id = %s
          and user_id = %s
          and session_id = %s
          and agent_id = %s
          and engine = %s
          and provider_session_id = %s::uuid
        order by subpath asc, sequence asc
        """,
        _binding_values(scope, expected_provider_id),
    )
    existing_rows = [_row_dict(row) or {} for row in await cursor.fetchall()]
    existing_by_uuid: dict[tuple[str, str], dict[str, Any]] = {}
    total_entries = 0
    total_bytes = 0
    for existing_row in existing_rows:
        existing_payload = existing_row.get("entry_json")
        if not isinstance(existing_payload, Mapping):
            raise ProviderSessionConflictError("provider_session_entry_shape_invalid")
        try:
            existing_subpath = normalize_provider_subpath(existing_row.get("subpath")) or ""
            total_entries += 1
            total_bytes += provider_entry_json_bytes(existing_payload)
        except ProviderSessionContinuityError as exc:
            raise _repository_error(exc) from exc
        existing_uuid = str(existing_row.get("sdk_entry_uuid") or "").strip()
        if existing_uuid:
            existing_by_uuid[(existing_subpath, existing_uuid)] = existing_row
    for item in normalized_entries:
        item_uuid = item.sdk_entry_uuid
        existing = existing_by_uuid.get((normalized_subpath, item_uuid)) if item_uuid else None
        if existing is not None:
            stored_entry = existing.get("entry_json")
            if not isinstance(stored_entry, Mapping) or dict(stored_entry) != item.entry:
                raise ProviderSessionConflictError("provider_session_entry_conflict")
            result.append(existing)
            continue
        try:
            item_bytes = provider_entry_json_bytes(item.entry)
        except ProviderSessionContinuityError as exc:
            raise _repository_error(exc) from exc
        if (
            total_entries + 1 > MAX_PROVIDER_SESSION_ENTRIES
            or total_bytes + item_bytes > MAX_PROVIDER_SESSION_TRANSCRIPT_BYTES
        ):
            raise ProviderSessionConflictError("provider_session_transcript_too_large")

        cursor = await conn.execute(
            """
            insert into provider_session_entries(
              id, tenant_id, workspace_id, user_id, session_id, agent_id, engine,
              provider_session_id, subpath, sequence, sdk_entry_uuid, entry_json
            )
            select 'pse_' || md5(random()::text || clock_timestamp()::text || %s),
                   binding.tenant_id, binding.workspace_id, binding.user_id,
                   binding.session_id, binding.agent_id, binding.engine,
                   binding.provider_session_id, %s, binding.next_sequence, %s, %s::jsonb
            from provider_session_bindings binding
            where binding.tenant_id = %s
              and binding.workspace_id = %s
              and binding.user_id = %s
              and binding.session_id = %s
              and binding.agent_id = %s
              and binding.engine = %s
              and binding.provider_session_id = %s::uuid
              and binding.writer_run_id = %s
              and binding.writer_attempt_id = %s
              and exists (
                select 1 from sandbox_leases lease
                where lease.tenant_id = binding.tenant_id
                  and lease.workspace_id = binding.workspace_id
                  and lease.user_id = binding.user_id
                  and lease.session_id = binding.session_id
                  and lease.run_id = %s
                  and lease.attempt_id = %s
                  and lease.status = 'active'
                  and lease.released_at is null
                  and (lease.expires_at is null or lease.expires_at > now())
              )
            returning id, tenant_id, workspace_id, user_id, session_id, agent_id, engine,
                      provider_session_id, subpath, sequence, sdk_entry_uuid, entry_json, created_at
            """,
            (
                run_id,
                item.subpath or "",
                item.sdk_entry_uuid,
                json.dumps(item.entry, ensure_ascii=False, separators=(",", ":")),
                scope.tenant_id,
                scope.workspace_id,
                scope.user_id,
                scope.session_id,
                scope.agent_id,
                scope.engine,
                expected_provider_id,
                run_id,
                attempt_id,
                run_id,
                attempt_id,
            ),
        )
        row = _row_dict(await cursor.fetchone())
        if row is None:
            raise ProviderSessionConflictError("provider_session_writer_conflict")
        update_cursor = await conn.execute(
            """
            update provider_session_bindings
            set next_sequence = next_sequence + 1, updated_at = now()
            where tenant_id = %s
              and workspace_id = %s
              and user_id = %s
              and session_id = %s
              and agent_id = %s
              and engine = %s
              and provider_session_id = %s::uuid
              and writer_run_id = %s
              and writer_attempt_id = %s
              and exists (
                select 1 from sandbox_leases lease
                where lease.tenant_id = provider_session_bindings.tenant_id
                  and lease.workspace_id = provider_session_bindings.workspace_id
                  and lease.user_id = provider_session_bindings.user_id
                  and lease.session_id = provider_session_bindings.session_id
                  and lease.run_id = %s
                  and lease.attempt_id = %s
                  and lease.status = 'active'
                  and lease.released_at is null
                  and (lease.expires_at is null or lease.expires_at > now())
              )
            returning next_sequence
            """,
            (
                *(_binding_values(scope, expected_provider_id)),
                run_id,
                attempt_id,
                run_id,
                attempt_id,
            ),
        )
        if _row_dict(await update_cursor.fetchone()) is None:
            raise ProviderSessionConflictError("provider_session_writer_conflict")
        total_entries += 1
        total_bytes += item_bytes
        if item_uuid:
            existing_by_uuid[(normalized_subpath, item_uuid)] = row
        result.append(row)
    return result


async def list_provider_session_entries(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    workspace_id: str,
    user_id: str,
    session_id: str,
    agent_id: str,
    subpath: object = None,
    provider_session_id: str | None = None,
    engine: str = "claude",
    max_entries: int = MAX_PROVIDER_SESSION_ENTRIES,
    max_bytes: int = MAX_PROVIDER_SESSION_TRANSCRIPT_BYTES,
) -> list[dict[str, Any]]:
    try:
        normalized_subpath = normalize_provider_subpath(subpath) or ""
        if type(max_entries) is not int or max_entries < 1 or max_entries > MAX_PROVIDER_SESSION_ENTRIES:
            raise ProviderSessionContinuityError("provider_session_entry_limit_invalid")
        if type(max_bytes) is not int or max_bytes < 1 or max_bytes > MAX_PROVIDER_SESSION_TRANSCRIPT_BYTES:
            raise ProviderSessionContinuityError("provider_session_transcript_limit_invalid")
        scope = _scope(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            user_id=user_id,
            session_id=session_id,
            agent_id=agent_id,
            engine=engine,
        )
        expected_provider_id = provider_session_id_for_scope(scope)
        if provider_session_id is not None:
            _validate_provider_id(scope, provider_session_id)
    except ProviderSessionContinuityError as exc:
        raise _repository_error(exc) from exc

    binding = await get_provider_session_binding(
        conn,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        user_id=user_id,
        session_id=session_id,
        agent_id=agent_id,
        provider_session_id=expected_provider_id,
        engine=engine,
    )
    if binding is None:
        raise ProviderSessionNotFoundError("provider_session_binding_not_found")

    cursor = await conn.execute(
        """
        select id, tenant_id, workspace_id, user_id, session_id, agent_id, engine,
               provider_session_id, subpath, sequence, sdk_entry_uuid, entry_json, created_at
        from provider_session_entries
        where tenant_id = %s
          and workspace_id = %s
          and user_id = %s
          and session_id = %s
          and agent_id = %s
          and engine = %s
          and provider_session_id = %s::uuid
          and subpath = %s
        order by sequence asc
        limit %s
        """,
        (
            scope.tenant_id,
            scope.workspace_id,
            scope.user_id,
            scope.session_id,
            scope.agent_id,
            scope.engine,
            expected_provider_id,
            normalized_subpath,
            max_entries + 1,
        ),
    )
    rows = [_row_dict(row) or {} for row in await cursor.fetchall()]
    if len(rows) > max_entries:
        raise ProviderSessionConflictError("provider_session_transcript_too_large")
    total_bytes = 0
    for row in rows:
        payload = row.get("entry_json")
        if not isinstance(payload, Mapping):
            raise ProviderSessionConflictError("provider_session_entry_shape_invalid")
        try:
            total_bytes += provider_entry_json_bytes(payload)
        except ProviderSessionContinuityError as exc:
            raise _repository_error(exc) from exc
        if total_bytes > max_bytes:
            raise ProviderSessionConflictError("provider_session_transcript_too_large")
        row["subpath"] = normalize_provider_subpath(row.get("subpath"))
    return rows


async def list_provider_session_subpaths(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    workspace_id: str,
    user_id: str,
    session_id: str,
    agent_id: str,
    provider_session_id: str | None = None,
    engine: str = "claude",
    max_subpaths: int = MAX_PROVIDER_SESSION_ENTRIES,
) -> list[str]:
    try:
        if type(max_subpaths) is not int or max_subpaths < 1 or max_subpaths > MAX_PROVIDER_SESSION_ENTRIES:
            raise ProviderSessionContinuityError("provider_session_entry_limit_invalid")
        scope = _scope(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            user_id=user_id,
            session_id=session_id,
            agent_id=agent_id,
            engine=engine,
        )
        expected_provider_id = provider_session_id_for_scope(scope)
        if provider_session_id is not None:
            _validate_provider_id(scope, provider_session_id)
    except ProviderSessionContinuityError as exc:
        raise _repository_error(exc) from exc

    binding = await get_provider_session_binding(
        conn,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        user_id=user_id,
        session_id=session_id,
        agent_id=agent_id,
        provider_session_id=expected_provider_id,
        engine=engine,
    )
    if binding is None:
        raise ProviderSessionNotFoundError("provider_session_binding_not_found")

    cursor = await conn.execute(
        """
        select distinct subpath
        from provider_session_entries
        where tenant_id = %s
          and workspace_id = %s
          and user_id = %s
          and session_id = %s
          and agent_id = %s
          and engine = %s
          and provider_session_id = %s::uuid
          and subpath <> ''
        order by subpath asc
        limit %s
        """,
        (
            scope.tenant_id,
            scope.workspace_id,
            scope.user_id,
            scope.session_id,
            scope.agent_id,
            scope.engine,
            expected_provider_id,
            max_subpaths + 1,
        ),
    )
    rows = [_row_dict(row) or {} for row in await cursor.fetchall()]
    if len(rows) > max_subpaths:
        raise ProviderSessionConflictError("provider_session_transcript_too_large")
    result: list[str] = []
    for row in rows:
        value = normalize_provider_subpath(row.get("subpath"))
        if value is not None:
            result.append(value)
    return result


async def load_provider_session_transcript(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    workspace_id: str,
    user_id: str,
    session_id: str,
    agent_id: str,
    provider_session_id: str | None = None,
    engine: str = "claude",
    max_entries: int = MAX_PROVIDER_SESSION_ENTRIES,
    max_bytes: int = MAX_PROVIDER_SESSION_TRANSCRIPT_BYTES,
) -> dict[str | None, list[dict[str, Any]]]:
    """Load main and every bounded subpath in sequence order."""
    subpaths = await list_provider_session_subpaths(
        conn,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        user_id=user_id,
        session_id=session_id,
        agent_id=agent_id,
        provider_session_id=provider_session_id,
        engine=engine,
    )
    result: dict[str | None, list[dict[str, Any]]] = {}
    total_entries = 0
    total_bytes = 0
    for current_subpath in [None, *subpaths]:
        rows = await list_provider_session_entries(
            conn,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            user_id=user_id,
            session_id=session_id,
            agent_id=agent_id,
            subpath=current_subpath,
            provider_session_id=provider_session_id,
            engine=engine,
            max_entries=max_entries,
            max_bytes=max_bytes,
        )
        total_entries += len(rows)
        total_bytes += sum(
            provider_entry_json_bytes(row["entry_json"])
            for row in rows
            if isinstance(row.get("entry_json"), Mapping)
        )
        if total_entries > max_entries or total_bytes > max_bytes:
            raise ProviderSessionConflictError("provider_session_transcript_too_large")
        result[current_subpath] = rows
    return result


class PostgresProviderSessionRepository:
    """PostgreSQL implementation of the Context provider-session port."""

    ensure_binding = staticmethod(ensure_provider_session_binding)
    claim_writer = staticmethod(claim_provider_session_writer)
    append_entries = staticmethod(append_provider_session_entries)
    list_entries = staticmethod(list_provider_session_entries)
    list_subpaths = staticmethod(list_provider_session_subpaths)
    has_main_transcript = staticmethod(provider_session_has_main_transcript)


# Names used by the callback adapter are deliberately aliases of the same
# bounded repository operations; there is one persistence authority.
load_provider_session_entries = list_provider_session_entries
append_provider_transcript_entries = append_provider_session_entries
has_provider_session_main_transcript = provider_session_has_main_transcript


__all__ = [
    "PostgresProviderSessionRepository",
    "append_provider_session_entries",
    "append_provider_transcript_entries",
    "claim_provider_session_writer",
    "ensure_provider_session_binding",
    "get_provider_session_binding",
    "has_provider_session_main_transcript",
    "list_provider_session_entries",
    "list_provider_session_subpaths",
    "load_provider_session_entries",
    "load_provider_session_transcript",
    "provider_session_binding_for_context",
    "provider_session_has_main_transcript",
]
