from types import SimpleNamespace

from app.context.api import manifest_with_worker_attachment_metadata


def test_context_attachment_metadata_enriches_only_matching_authorized_refs():
    manifest = {
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
         "size_bytes": 4, "requires_retrieval": True},
        {"file_id": "file-b", "name": "unrelated"},
    ]
    assert manifest["files"][0]["name"] == "original"
    assert projected["available_retrieval_tools"] == ["stage_context_file_to_workspace"]
