"""Content-free administrator references to files admitted by the Run snapshot."""
from typing import Any, Callable

_reader: Callable[..., Any] | None = None


def configure_admin_run_input_reader(read: Callable[..., Any]) -> None:
    global _reader
    _reader = read


async def read_admin_run_input_files(conn: Any, *, tenant_id: str, run_id: str) -> list[dict[str, Any]]:
    if _reader is None:
        raise RuntimeError("admin_run_input_reader_unavailable")
    return await _reader(conn, tenant_id=tenant_id, run_id=run_id)
