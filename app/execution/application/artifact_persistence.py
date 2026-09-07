from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterable
from typing import Any


def build_artifact_execution_owner(
    payload: Any,
    transaction_factory: Callable[[], Any],
    reserve_cleanup: Callable[..., Any],
    owner_factory: Callable[..., Any],
) -> Any:
    return owner_factory(
        payload.run_id,
        artifact_storage_scope=payload.attempt_id,
        reserve_artifact_storage=build_artifact_storage_reserver(
            transaction_factory=transaction_factory,
            reserve_cleanup=reserve_cleanup,
            tenant_id=payload.tenant_id,
            run_id=payload.run_id,
        ),
    )


def build_artifact_storage_reserver(
    *,
    transaction_factory: Callable[[], Any],
    reserve_cleanup: Callable[..., Any],
    tenant_id: str,
    run_id: str,
) -> Callable[[str], str]:
    loop = asyncio.get_running_loop()

    async def reserve(storage_key: str) -> str:
        async with transaction_factory() as conn:
            return await reserve_cleanup(
                conn,
                tenant_id=tenant_id,
                run_id=run_id,
                storage_key=storage_key,
            )

    def reserve_from_thread(storage_key: str) -> str:
        return asyncio.run_coroutine_threadsafe(reserve(storage_key), loop).result()

    return reserve_from_thread


def build_artifact_records(
    artifacts: Iterable[Any],
    reconciliation: bool,
    new_id: Callable[[str], str],
    download_url: Callable[[str], str],
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for artifact in artifacts:
        if reconciliation and not artifact.provisional_cleanup_id:
            raise ValueError("executor_reconciliation_artifact_cleanup_receipt_missing")
        artifact_id = new_id("art")
        records.append(
            {
                "id": artifact_id,
                "artifact_type": artifact.artifact_type,
                "label": artifact.label,
                "content_type": artifact.content_type,
                "storage_key": artifact.storage_key,
                "size_bytes": artifact.size_bytes,
                "download_url": download_url(artifact_id),
                "manifest_json": artifact.manifest,
                "provisional_cleanup_id": artifact.provisional_cleanup_id,
            }
        )
    return records


async def promote_artifact_reservations(
    conn: Any,
    artifacts: list[dict[str, Any]],
    payload: Any,
    promote_cleanup: Callable[..., Any],
) -> None:
    for artifact in artifacts:
        cleanup_id = artifact["provisional_cleanup_id"]
        if cleanup_id is None:
            continue
        promoted = await promote_cleanup(
            conn,
            artifact_id=str(cleanup_id),
            tenant_id=payload.tenant_id,
            run_id=payload.run_id,
            storage_key=artifact["storage_key"],
        )
        if not promoted:
            raise RuntimeError("executor_artifact_cleanup_receipt_lost")
