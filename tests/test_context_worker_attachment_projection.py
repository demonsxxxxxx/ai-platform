from types import SimpleNamespace

from app.context.api import manifest_with_worker_attachment_metadata
from app.context_manifest import CONTEXT_MANIFEST_SCHEMA_VERSION, available_context_retrieval_tools


def test_context_attachment_metadata_enriches_only_matching_authorized_refs():
    manifest = {
        "schema_version": CONTEXT_MANIFEST_SCHEMA_VERSION,
        "files": [
            {"file_id": "file-a", "name": "original"},
            {"file_id": "file-b", "name": "unrelated"},
        ],
        "available_retrieval_tools": ["stage_context_file_to_workspace"],
    }
    projected = manifest_with_worker_attachment_metadata(
        manifest,
        [SimpleNamespace(file_id="file-a", file_name="safe.txt",
                         content_type="text/plain", size_bytes=4)],
    )
    assert projected["files"] == [
        {"file_id": "file-a", "name": "safe.txt", "content_type": "text/plain",
         "size_bytes": 4, "requires_retrieval": False},
        {"file_id": "file-b", "name": "unrelated"},
    ]
    assert manifest["files"][0]["name"] == "original"
    assert available_context_retrieval_tools(projected) == ["stage_context_file_to_workspace"]
    assert available_context_retrieval_tools({**projected, "files": projected["files"][:1]}) == []
    assert available_context_retrieval_tools(manifest) == ["stage_context_file_to_workspace"]
