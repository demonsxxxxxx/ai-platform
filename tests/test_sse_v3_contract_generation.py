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
