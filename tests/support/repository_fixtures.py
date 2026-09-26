from datetime import datetime, timezone
import os
import pytest


class FakeCursor:
    async def fetchone(self):
        return {"count": 2, "id": "step-a"}

    async def fetchall(self):
        return []


class FakeConnection:
    def __init__(self):
        self.sql = ""
        self.params = None

    async def execute(self, sql, params):
        self.sql = sql
        self.params = params
        return FakeCursor()


class RecordingConnection:
    def __init__(self):
        self.calls = []

    async def execute(self, sql, params):
        normalized = " ".join(sql.split())
        self.calls.append((normalized, params))
        if normalized.startswith("update sessions set next_run_generation"):
            return SingleRowCursor({"next_run_generation": 1})
        if "select clock_timestamp() as authority_now" in normalized:
            return SingleRowCursor({"authority_now": datetime(2026, 7, 16, tzinfo=timezone.utc)})
        if normalized.startswith("select * from sse_stream_authorities"):
            return SingleRowCursor(None)
        return FakeCursor()


class SingleRowCursor:
    def __init__(self, row):
        self.row = row

    async def fetchone(self):
        return self.row

    async def fetchall(self):
        return [self.row] if self.row is not None else []


class SingleRowConnection:
    def __init__(self, row):
        self.row = row
        self.sql = ""
        self.params = None

    async def execute(self, sql, params):
        self.sql = " ".join(sql.split())
        self.params = params
        return SingleRowCursor(self.row)


def _run_control_postgres_dsn() -> str:
    dsn = os.getenv("AI_PLATFORM_S0A_SCHEMA_TEST_DSN", "").strip()
    if not dsn:
        pytest.skip("AI_PLATFORM_S0A_SCHEMA_TEST_DSN is not configured")
    return dsn
