"""Identity-owned failure disposition for browser authentication contexts."""


class AuthContextError(RuntimeError):
    """A safe auth-context failure that can be projected to browser clients."""

    def __init__(self, code: str, status_code: int, *, discard_orphaned_cookie: bool = False):
        super().__init__(code)
        self.code = code
        self.status_code = status_code
        self.discard_orphaned_cookie = discard_orphaned_cookie


def raise_auth_context_store_error(operation: str, status: str) -> None:
    """Translate a store outcome without restoring identity or granting access."""
    if status in {
        "stale",
        "generation_gap",
        "generation_conflict",
        "migration_conflict",
        "invalid_ticket",
    }:
        raise AuthContextError("auth_context_stale", 409)
    if status == "rebootstrap_required":
        raise AuthContextError("auth_context_rebootstrap_required", 409)
    if status == "refresh_busy":
        raise AuthContextError("auth_refresh_in_progress", 409)
    if status == "missing":
        raise AuthContextError("auth_context_missing", 401)
    if status == "superseded":
        raise AuthContextError("auth_operation_superseded", 409)
    if status == "expired":
        raise AuthContextError("auth_operation_expired", 409)
    if operation == "oauth" and status == "invalid":
        raise AuthContextError("auth_operation_superseded", 409)
    raise AuthContextError("auth_context_unavailable", 503)
