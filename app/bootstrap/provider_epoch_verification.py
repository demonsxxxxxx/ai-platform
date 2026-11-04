"""Compose callback authorization with Context's lock-free media verification."""
from contextlib import asynccontextmanager

from app.context.api import provider_session_verification_scope


@asynccontextmanager
async def provider_callback_transaction(transaction_factory, *, authorize, run_id: str,
                                        attempt_id: str, callback_token_id: str,
                                        allow_provider_tail: bool = False):
    binding = dict(run_id=run_id, attempt_id=attempt_id, callback_token_id=callback_token_id,
                   allow_provider_tail=allow_provider_tail)
    async with transaction_factory() as conn:
        owner, _lease = await authorize(conn, **binding)
    async with provider_session_verification_scope(
        transaction_factory, tenant_id=owner["tenant_id"], run_id=run_id, user_id=owner["user_id"],
    ):
        async with transaction_factory() as conn:
            # The caller rechecks its exact callback/lease before consuming the proof.
            yield conn
