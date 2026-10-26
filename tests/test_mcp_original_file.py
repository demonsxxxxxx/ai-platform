import base64
import json
import pytest
from mcp_types import BlobResourceContents, CallToolResult, EmbeddedResource, TextContent

from app.mcp.domain.original_file import (
    ORIGINAL_FILE_RESOURCE_URI,
    OriginalFileResultError,
    extract_original_file_result,
)


def result(metadata, blob, *, uri=ORIGINAL_FILE_RESOURCE_URI, mime_type="application/octet-stream"):
    return CallToolResult(
        content=[
            TextContent(type="text", text=json.dumps(metadata)),
            EmbeddedResource(
                resource=BlobResourceContents(uri=uri, mimeType=mime_type, blob=blob),
            ),
        ],
        is_error=False,
    )


def metadata(raw=b"bytes"):
    return {
        "format": "profile-drive.original-file.v1",
        "source": "profile",
        "path": "share/file.bin",
        "name": "file.bin",
        "sizeBytes": len(raw),
        "contentType": "application/octet-stream",
    }


def test_extract_original_file_accepts_empty_opaque_bytes():
    payload, extracted = extract_original_file_result(
        result(metadata(b""), base64.b64encode(b"").decode())
    )
    assert payload["size_bytes"] == 0
    assert extracted == b""


def test_extract_original_file_preserves_opaque_bytes_and_only_safe_metadata():
    raw = b"\x00Unicode \xe5\x8e\x9f\xe4\xbb\xb6\xff"
    payload, extracted = extract_original_file_result(result(metadata(raw), base64.b64encode(raw).decode()))
    assert payload == {
        "format": "profile-drive.original-file.v1",
        "source": "profile",
        "path": "share/file.bin",
        "name": "file.bin",
        "size_bytes": len(raw),
        "content_type": "application/octet-stream",
    }
    assert extracted == raw


@pytest.mark.parametrize(
    "changed, expected",
    [
        ({"format": "wrong"}, "original_file_metadata_invalid"),
        ({"source": "private"}, "original_file_metadata_invalid"),
        ({"path": "../escape"}, "original_file_metadata_invalid"),
        ({"sizeBytes": 99}, "original_file_size_mismatch"),
    ],
)
def test_extract_original_file_rejects_bad_metadata(changed, expected):
    value = metadata()
    value.update(changed)
    with pytest.raises(OriginalFileResultError, match=expected):
        extract_original_file_result(result(value, base64.b64encode(b"bytes").decode()))


@pytest.mark.parametrize(
    "blob, expected",
    [("not-base64", "original_file_blob_invalid"), (base64.b64encode(b"other!").decode(), "original_file_size_mismatch")],
)
def test_extract_original_file_rejects_bad_blob(blob, expected):
    with pytest.raises(OriginalFileResultError, match=expected):
        extract_original_file_result(result(metadata(), blob))


def test_extract_original_file_rejects_duplicate_metadata_keys_and_noncanonical_blob():
    duplicate = '{"format":"profile-drive.original-file.v1","format":"profile-drive.original-file.v1","source":"profile","path":"share/file.bin","name":"file.bin","sizeBytes":5,"contentType":"application/octet-stream"}'
    duplicate_result = CallToolResult(
        content=[TextContent(type="text", text=duplicate), result(metadata(), base64.b64encode(b"bytes").decode()).content[1]],
        is_error=False,
    )
    with pytest.raises(OriginalFileResultError, match="original_file_metadata_invalid"):
        extract_original_file_result(duplicate_result)
    with pytest.raises(OriginalFileResultError, match="original_file_blob_invalid"):
        extract_original_file_result(result(metadata(), "Ynl0ZXM=\n"))


def test_extract_original_file_enforces_requested_lower_max_bytes():
    with pytest.raises(OriginalFileResultError, match="original_file_max_bytes_exceeded"):
        extract_original_file_result(
            result(metadata(), base64.b64encode(b"bytes").decode()), max_bytes=4
        )


def test_extract_original_file_rejects_remote_error_and_wrong_resource():
    with pytest.raises(OriginalFileResultError, match="original_file_result_not_success"):
        extract_original_file_result(result(metadata(), base64.b64encode(b"bytes").decode()).model_copy(update={"is_error": True}))
    with pytest.raises(OriginalFileResultError, match="original_file_resource_invalid"):
        extract_original_file_result(result(metadata(), base64.b64encode(b"bytes").decode(), uri="profile-drive://other"))
