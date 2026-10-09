"""Context manifest enrichment after authorized file staging."""

from typing import Any


def manifest_with_worker_attachment_metadata(
    manifest: dict[str, Any] | None,
    metadata: list[Any],
) -> dict[str, Any]:
    result = dict(manifest or {})
    raw_files = result.get("files")
    if not metadata or not isinstance(raw_files, list):
        return result
    metadata_by_file_id = {item.file_id: item for item in metadata}
    enriched_files: list[Any] = []
    for raw_file in raw_files:
        if not isinstance(raw_file, dict):
            enriched_files.append(raw_file)
            continue
        file_ref = dict(raw_file)
        item = metadata_by_file_id.get(str(file_ref.get("file_id") or ""))
        if item is not None:
            file_ref.update({
                "name": item.file_name,
                "content_type": item.content_type,
                "size_bytes": item.size_bytes,
                "requires_retrieval": True,
            })
        enriched_files.append(file_ref)
    result["files"] = enriched_files
    return result
