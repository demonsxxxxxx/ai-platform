"""Current Attempt settlement schema for deliberately reduced PostgreSQL fixtures."""
from pathlib import Path


def settlement_authority_sql() -> str:
    source = Path("app/schema.sql").read_text()
    start = source.index("create table if not exists run_attempt_settlements (")
    end = source.index("-- A per-Run claim", start)
    return (
        "create unique index if not exists uq_test_attempt_settlement_scope "
        "on run_attempts(tenant_id,run_id,id);\n" + source[start:end]
    )
