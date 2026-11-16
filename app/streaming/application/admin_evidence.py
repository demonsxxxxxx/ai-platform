"""Private append and bounded administrator read ports for the committed event ledger."""
from typing import Any, Callable

_reader: Callable[..., Any] | None = None
_writer: Callable[..., Any] | None = None
_trigger_writer: Callable[..., Any] | None = None


def configure_admin_event_evidence(*, read: Callable[..., Any], append: Callable[..., Any], trigger: Callable[..., Any]) -> None:
    global _reader, _writer, _trigger_writer
    _reader, _writer, _trigger_writer = read, append, trigger


async def read_admin_detail_events(conn: Any, *, tenant_id: str, run_id: str) -> dict[str, Any]:
    if _reader is None:
        raise RuntimeError("admin_event_reader_unavailable")
    return await _reader(conn, tenant_id=tenant_id, run_id=run_id)


async def append_run_trigger(conn: Any, *, tenant_id: str, run_id: str, source: str) -> None:
    if _trigger_writer is None:
        raise RuntimeError("run_trigger_writer_unavailable")
    await _trigger_writer(conn, tenant_id=tenant_id, run_id=run_id, source=source)


async def append_private_model_call(conn: Any, *, tenant_id: str, run_id: str, attempt_id: str, evidence: dict) -> None:
    if _writer is None:
        raise RuntimeError("private_model_evidence_writer_unavailable")
    await _writer(conn, tenant_id=tenant_id, run_id=run_id, attempt_id=attempt_id, evidence=evidence)
