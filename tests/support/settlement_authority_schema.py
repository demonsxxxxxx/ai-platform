"""Minimal Runs settlement authority for reduced-schema PostgreSQL fixtures.

Attempt terminalization joins the settlement writer fence and reads the durable
storage/writer markers. Reduced fixtures install the exact marker tables (and
their legacy seeds) from ``app/schema.sql`` plus lock-only placeholders for the
fenced source relations; the shadow writer stays disabled, so no projection
detail rows are written.
"""
from pathlib import Path

_MARKERS = (
    "run_attempt_settlement_retirement",
    "run_attempt_settlement_storage",
    "run_attempt_settlement_writer",
)
_LOCK_ONLY = (
    "create table if not exists sandbox_execution_dispatches(id text)",
    "create table if not exists run_attempt_settlements(tenant_id text, run_id text, attempt_id text)",
    "create table if not exists run_stale_reconciliation_claims(tenant_id text, run_id text)",
)


def settlement_authority_sql() -> str:
    source = Path("app/schema.sql").read_text()
    parts = []
    for name in _MARKERS:
        start = source.index(f"create table if not exists {name} (")
        seed = "on conflict (singleton) do nothing;"
        end = source.index(seed, start) + len(seed)
        parts.append(source[start:end])
    return ";\n".join(_LOCK_ONLY) + ";\n" + "\n".join(parts) + "\n"
