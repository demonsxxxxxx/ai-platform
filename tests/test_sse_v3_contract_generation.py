from pathlib import Path

from tools import generate_sse_v3_contracts


ROOT = Path(__file__).resolve().parents[1]


def test_generated_sse_v3_contracts_are_current():
    assert generate_sse_v3_contracts.generate(check=True) == []


def test_frontend_generated_contract_exposes_only_public_fields():
    source = (ROOT / "frontend/web/src/generated/publicRunStreamV3.ts").read_text(
        encoding="utf-8"
    )

    assert "PublicRunStreamEventV3" in source
    assert "tenant_scope" not in source
    assert "attempt_id" not in source
    assert "reasoning.delta" not in source
    assert "approval.required" not in source


def test_schema_keeps_internal_and_public_contracts_separate():
    source = (ROOT / "schemas/public_run_stream.v3.schema.json").read_text(
        encoding="utf-8"
    )

    assert '"InternalStreamEnvelopeV3"' in source
    assert '"PublicRunStreamEventV3"' in source
    assert '"owner_epoch"' not in source
    assert '"generation_id"' not in source


def test_public_boundary_exposes_v3_and_v4_types_without_duplicate_ownership():
    from app.streaming.domain import protocol_v4
    from app.streaming.events import (
        INTERNAL_STREAM_EVENT_SCHEMA_V4,
        PUBLIC_APPLICATION_EVENT_TYPES_V4,
        PUBLIC_RUN_STREAM_SCHEMA_V4,
        PUBLIC_STREAM_EVENT_TYPES_V4,
        STREAM_DESIGN_ID_V4,
        STREAM_PROJECTION_VERSION_V4,
        PublicRunStreamEventV3,
        PublicRunStreamEventV4,
    )

    assert PublicRunStreamEventV3 is not PublicRunStreamEventV4
    assert PublicRunStreamEventV4 is protocol_v4.PublicRunStreamEventV4
    assert PUBLIC_RUN_STREAM_SCHEMA_V4 == protocol_v4.PUBLIC_RUN_STREAM_SCHEMA
    assert INTERNAL_STREAM_EVENT_SCHEMA_V4 == protocol_v4.INTERNAL_STREAM_EVENT_SCHEMA
    assert STREAM_PROJECTION_VERSION_V4 == protocol_v4.STREAM_PROJECTION_VERSION
    assert STREAM_DESIGN_ID_V4 == protocol_v4.STREAM_DESIGN_ID
    assert PUBLIC_STREAM_EVENT_TYPES_V4 is protocol_v4.PUBLIC_STREAM_EVENT_TYPES
    assert PUBLIC_APPLICATION_EVENT_TYPES_V4 == frozenset(
        value
        for value in protocol_v4.PUBLIC_STREAM_EVENT_TYPES
        if not value.startswith("stream.")
    )
