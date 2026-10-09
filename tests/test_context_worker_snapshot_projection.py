from app.context.api import project_worker_snapshot_ref


def test_worker_context_ref_counts_scoped_members_and_sanitizes_private_fields():
    def provenance(payload, **kwargs):
        return {
            "used_context_summary": {"source": kwargs["source"]},
            "referenced_materials": {
                "message_count": kwargs["message_count"],
                "file_count": kwargs["file_count"],
                "artifact_count": kwargs["artifact_count"],
                "memory_record_count": kwargs["memory_record_count"],
            },
            "latest_artifact_version": None,
            "execution_tier": "ordinary",
            "context_pack_version": "v1",
            "context_pack_generated_at": "now",
        }

    row = {
        "id": "snapshot",
        "included_message_ids": ["message-a", "message-b"],
        "payload_json": {
            "file_count": "1", "memory_record_count": "bad",
            "storage_key": "private-storage-key",
            "memory_policy": {"source": "unknown", "retention_days": -1,
                              "long_term_memory_enabled": True},
            "context_manifest": {"schema_version": "v1", "runtime_path": "/private"},
        },
    }
    projected = project_worker_snapshot_ref(
        row, public_provenance=provenance,
        sanitize_manifest=lambda manifest: {"schema_version": manifest["schema_version"]},
        snapshot_schema_version="v1", manifest_schema_version="v1",
    )
    assert projected["message_count"] == 2
    assert projected["file_count"] == 1
    assert projected["memory_record_count"] == 0
    assert projected["memory_policy"] == {
        "source": "stored", "memory_enabled": True,
        "long_term_memory_enabled": False, "retention_days": 90,
    }
    assert projected["context_manifest"] == {"schema_version": "v1"}
    assert "private-storage-key" not in str(projected)
    assert "/private" not in str(projected)
