"""In-memory Runs settlement port for composition unit tests (no alternate storage)."""
from copy import deepcopy


class MemoryAttemptSettlement:
    def __init__(self):
        self.details = {}

    @staticmethod
    def _key(scope):
        return tuple(scope[key] for key in ("tenant_id", "run_id", "attempt_id"))

    async def lock_sources_in_transaction(self, _conn, **_scope):
        return None

    async def initialize_attempt_in_transaction(self, _conn, **scope):
        self.details.setdefault(self._key(scope), {"mapping_state": "mapped", "settlement_phase": "awaiting_receipt"})
        return True

    async def refresh_snapshot_in_transaction(self, _conn, **scope):
        return True

    async def read_attempt(self, _conn, **scope):
        return deepcopy(self.details.get(self._key(scope)))

    async def record_accepted_context_in_transaction(self, conn, **scope):
        await self.initialize_attempt_in_transaction(conn, **scope)
        self.details[self._key(scope)].update(accepted_context_json=scope["accepted_context"], dispatch_state="accepted")
        return True

    async def record_terminal_receipt_in_transaction(self, conn, **scope):
        await self.initialize_attempt_in_transaction(conn, **scope)
        self.details[self._key(scope)].update(receipt_json=scope["receipt"], receipt_owner_generation=scope["receipt_owner_generation"])
        return True


async def read_session_continuation(conn, **scope):
    from app.bootstrap.attempt_settlement_projection import build_attempt_settlement_projection_service
    from app.context.infrastructure.provider_continuation import get_session_continuation
    return await get_session_continuation(conn, **scope, settlement_reader=build_attempt_settlement_projection_service().read_attempt)
