from __future__ import annotations

import asyncio
from collections.abc import Callable
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
