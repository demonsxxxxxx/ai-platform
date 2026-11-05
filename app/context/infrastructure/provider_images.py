"""Private image reservations and deletion ownership for provider transcripts."""

from __future__ import annotations

import uuid
from typing import Any

from app.context.domain.provider_media import MAX_PROVIDER_IMAGE_BYTES, MAX_PROVIDER_IMAGE_FIELDS, ProviderMediaProjection, ProviderMediaTransfer
from app.context.domain.provider_sessions import (
    MAX_PROVIDER_SESSION_TRANSCRIPT_BYTES, ProviderSessionConflictError, ProviderSessionScope,
)


async def reserve_provider_images(
    conn, *, scope: ProviderSessionScope, epoch: dict, attempt: dict,
    run_id: str, attempt_id: str, projection: ProviderMediaProjection | None,
) -> ProviderMediaTransfer:
    objects: list[dict[str, Any]] = []
    if projection is not None:
        if projection.images:
            cursor = await conn.execute(
                """
                select count(*) as count, coalesce(sum(image.size_bytes), 0) as bytes
                from provider_session_images image
                join object_deletion_outbox outbox on outbox.provider_image_id = image.id
                  and outbox.tenant_id = image.tenant_id and outbox.storage_key = image.storage_key
                where image.epoch_id = %s and image.provisional
                  and outbox.state <> 'provider_image_deleted'
                """, (epoch["id"],),
            )
            pending = await cursor.fetchone()
            if (pending["count"] + len(projection.images) > MAX_PROVIDER_IMAGE_FIELDS
                or pending["bytes"] + sum(len(image.content) for image in projection.images.values())
                   > MAX_PROVIDER_SESSION_TRANSCRIPT_BYTES):
                raise ProviderSessionConflictError("provider_session_image_upload_pending")
        for payload in projection.images.values():
            image_id = f"psi_{uuid.uuid4().hex}"
            storage_key = f"private/provider-images/{uuid.uuid4().hex}"
            await conn.execute(
                """
                insert into provider_session_images (
                  id, tenant_id, workspace_id, user_id, session_id, agent_id, epoch_id,
                  run_id, attempt_id, owner_generation, storage_key, sha256, size_bytes
                ) values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """, (image_id, scope.tenant_id, scope.workspace_id, scope.user_id,
                      scope.session_id, scope.agent_id, epoch["id"], run_id, attempt_id,
                      attempt["owner_generation"], storage_key, payload.sha256, len(payload.content)),
            )
            await conn.execute(
                """
                insert into object_deletion_outbox (
                  id, tenant_id, target_type, provider_image_id, storage_key, state, available_at
                ) values (%s, %s, 'provider_image', %s, %s, 'provider_image_pending', now() + interval '15 minutes')
                """, (f"odo_{uuid.uuid4().hex}", scope.tenant_id, image_id, storage_key),
            )
            objects.append({"id": image_id, "storage_key": storage_key,
                            "sha256": payload.sha256, "size_bytes": len(payload.content)})
        by_digest = {obj["sha256"]: obj["id"] for obj in objects}
        for refs in projection.references:
            for ref in refs:
                ref["image_id"] = by_digest[ref["sha256"]]
    else:
        objects = await read_epoch_images(conn, epoch={**{key: getattr(scope, key) for key in ("tenant_id", "workspace_id", "user_id", "session_id", "agent_id")}, **epoch})
    return ProviderMediaTransfer(epoch["id"], epoch["next_sequence"],
                                 attempt["owner_generation"], objects, projection)


async def read_epoch_images(conn, *, epoch: dict) -> list[dict]:
    """Entry references own shared use; lock image rows against cleanup/copy races."""
    cursor = await conn.execute(
        """select image.id,image.storage_key,image.sha256,image.size_bytes
           from provider_session_images image
           where image.id in (
             select ref->>'image_id' from provider_session_entries entry
             cross join lateral jsonb_array_elements(entry.image_references) ref
             where entry.epoch_id=%s and entry.tenant_id=image.tenant_id
               and entry.workspace_id=image.workspace_id and entry.user_id=image.user_id
               and entry.session_id=image.session_id and entry.agent_id=image.agent_id
           ) and image.tenant_id=%s and image.workspace_id=%s and image.user_id=%s
             and image.session_id=%s and image.agent_id=%s
             and image.lifecycle_state='active' and not image.provisional
           order by image.id for share of image""",
        (epoch["id"], *(epoch[key] for key in ("tenant_id", "workspace_id", "user_id", "session_id", "agent_id"))),
    )
    objects = list(await cursor.fetchall())
    if (any(type(obj["size_bytes"]) is not int or not 0 < obj["size_bytes"] <= MAX_PROVIDER_IMAGE_BYTES for obj in objects)
        or sum(obj["size_bytes"] for obj in objects) > MAX_PROVIDER_SESSION_TRANSCRIPT_BYTES):
        raise ProviderSessionConflictError("provider_session_transcript_too_large")
    return objects


async def promote_provider_images(conn, *, transfer: ProviderMediaTransfer, scope: ProviderSessionScope,
                                  run_id: str, attempt_id: str) -> None:
    # Promotion and append commit together; a claimed cleanup can never be revived.
    for obj in transfer.objects:
        cursor = await conn.execute(
            """
            select image.id, outbox.id as outbox_id
            from provider_session_images image
            join object_deletion_outbox outbox on outbox.provider_image_id = image.id
              and outbox.tenant_id = image.tenant_id and outbox.storage_key = image.storage_key
              and outbox.target_type = 'provider_image'
            where image.id = %s and image.tenant_id = %s and image.workspace_id = %s
              and image.user_id = %s and image.session_id = %s and image.agent_id = %s
              and image.epoch_id = %s and image.run_id = %s and image.attempt_id = %s
              and image.owner_generation = %s and image.storage_key = %s
              and image.sha256 = %s and image.size_bytes = %s
              and image.lifecycle_state = 'delete_pending' and image.provisional
              and outbox.state = 'provider_image_pending' and outbox.attempts = 0
              and outbox.available_at > clock_timestamp()
            for update of outbox, image
            """, (obj["id"], scope.tenant_id, scope.workspace_id, scope.user_id,
                  scope.session_id, scope.agent_id, transfer.epoch_id, run_id, attempt_id,
                  transfer.owner_generation, obj["storage_key"], obj["sha256"], obj["size_bytes"]),
        )
        row = await cursor.fetchone()
        if row is None:
            raise ProviderSessionConflictError("provider_session_image_reservation_expired")
        await conn.execute("delete from object_deletion_outbox where id = %s", (row["outbox_id"],))
        await conn.execute(
            "update provider_session_images set lifecycle_state = 'active', provisional = false where id = %s",
            (row["id"],),
        )


async def queue_provider_images_for_deletion(conn, *, tenant_id: str | None = None,
                                            session_id: str | None = None, limit: int = 50) -> list[dict]:
    """Keep object tombstones after logical or physical session/epoch removal."""
    cursor = await conn.execute(
        """
        with candidates as materialized (
          select image.id
          from provider_session_images image
          where image.lifecycle_state = 'active'
            and (%s::text is null or image.tenant_id = %s)
            and (%s::text is null or image.session_id = %s)
            and not exists (
              select 1 from provider_session_epochs epoch
              join sessions session on session.id = epoch.session_id
                and session.tenant_id = epoch.tenant_id and session.status <> 'deleted'
              where epoch.tenant_id = image.tenant_id
                and epoch.workspace_id = image.workspace_id and epoch.user_id = image.user_id
                and epoch.session_id = image.session_id and epoch.agent_id = image.agent_id
                and exists (
                  select 1 from provider_session_entries entry where entry.epoch_id=epoch.id
                    and entry.tenant_id=image.tenant_id and entry.workspace_id=image.workspace_id
                    and entry.user_id=image.user_id and entry.session_id=image.session_id
                    and entry.agent_id=image.agent_id
                    and entry.image_references @> jsonb_build_array(jsonb_build_object('image_id',image.id))
                )
            )
          order by image.created_at, image.id limit %s for update of image skip locked
        ), retired as (
          update provider_session_images image set lifecycle_state = 'delete_pending'
          where id in (select id from candidates)
          returning id, tenant_id, storage_key
        )
        insert into object_deletion_outbox (id, tenant_id, target_type, provider_image_id, storage_key, state)
        select 'odo_' || id, tenant_id, 'provider_image', id, storage_key, 'provider_image_pending' from retired
        returning tenant_id, provider_image_id
        """, (tenant_id, tenant_id, session_id, session_id, max(1, min(int(limit), 200))),
    )
    return list(await cursor.fetchall())
