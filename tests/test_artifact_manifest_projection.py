from types import SimpleNamespace

import pytest

from app.artifacts.api import persist_worker_artifacts, promote_artifact_reservations, sanitize_artifact_manifest


def test_artifact_manifest_hides_nested_storage_keys_and_runtime_paths():
    manifest = {
        "storage_key": "private-object-key",
        "files": [
            {"LOCAL_PATH": "private", "label": "Report", "size_bytes": 42},
            "C:/private/runner",
            {"notes": "/tmp/executor/private", "count": 0},
        ],
    }

    assert sanitize_artifact_manifest(manifest) == {
        "files": [
            {"label": "Report", "size_bytes": 42},
            {"count": 0},
        ],
    }


@pytest.mark.asyncio
async def test_worker_artifact_facts_share_order_and_hide_storage_in_event():
    observed = []

    async def create(conn, **fields):
        observed.append(("artifact", fields))

    async def append(conn, **fields):
        observed.append(("event", fields))

    record = {
        "id": "art-1", "artifact_type": "document", "label": "report",
        "content_type": "application/pdf", "storage_key": "private-storage",
        "size_bytes": 10, "download_url": "/download/art-1",
        "manifest_json": {"storage_key": "private-storage", "filename": "report.pdf"},
        "provisional_cleanup_id": None,
    }
    await persist_worker_artifacts(
        object(), [record], SimpleNamespace(tenant_id="tenant", run_id="run"),
        trace_id="trace", promote_cleanup=lambda *args, **kwargs: None,
        manifest_contract=lambda **kwargs: kwargs["manifest"],
        lineage_contract=lambda manifest, **kwargs: {"source_run_id": kwargs["source_run_id"]},
        create_artifact=create, append_user_event=append,
    )
    assert [kind for kind, _ in observed] == ["artifact", "event"]
    assert observed[0][1]["manifest_json"] == {"filename": "report.pdf"}
    assert "storage_key" not in observed[1][1]["payload"]
    assert observed[1][1]["payload"]["lineage"] == {"source_run_id": "run"}


@pytest.mark.asyncio
async def test_lost_artifact_cleanup_receipt_rolls_back_worker_commit():
    async def lost_receipt(conn, **kwargs):
        return False

    with pytest.raises(RuntimeError, match="executor_artifact_cleanup_receipt_lost"):
        await promote_artifact_reservations(
            object(),
            [{"provisional_cleanup_id": "cleanup", "storage_key": "private-object-key"}],
            SimpleNamespace(tenant_id="tenant", run_id="run"),
            lost_receipt,
        )
