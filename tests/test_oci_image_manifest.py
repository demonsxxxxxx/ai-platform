import copy
import hashlib
import json

import pytest

from tools.oci_image_manifest import resolve_authenticated_producer_digest


PRODUCER_DIGEST = (
    "sha256:973f2d3bed36feb5625f8a5f31cf0c7277c37d097654207e57b2fad46adfeac0"
)


def _oci_index_bytes(*, child_digest: str = PRODUCER_DIGEST) -> bytes:
    return json.dumps(
        {
            "schemaVersion": 2,
            "mediaType": "application/vnd.oci.image.index.v1+json",
            "manifests": [
                {
                    "mediaType": "application/vnd.oci.image.manifest.v1+json",
                    "digest": child_digest,
                    "size": 123,
                    "platform": {"architecture": "amd64", "os": "linux"},
                }
            ],
        },
        separators=(",", ":"),
    ).encode("utf-8")


def _requested_digest(raw_document: bytes) -> str:
    return f"sha256:{hashlib.sha256(raw_document).hexdigest()}"


def test_resolver_authenticates_raw_index_and_selects_linux_amd64_child():
    raw_document = _oci_index_bytes()

    assert (
        resolve_authenticated_producer_digest(
            raw_document,
            requested_digest=_requested_digest(raw_document),
        )
        == PRODUCER_DIGEST
    )


def test_resolver_ignores_a_valid_foreign_platform_variant():
    document = json.loads(_oci_index_bytes())
    document["manifests"].insert(
        0,
        {
            "mediaType": "application/vnd.oci.image.manifest.v1+json",
            "digest": "sha256:" + "1" * 64,
            "size": 1,
            "platform": {"architecture": "arm64", "os": "linux", "variant": "v8"},
        },
    )
    raw_document = json.dumps(document, separators=(",", ":")).encode("utf-8")

    assert (
        resolve_authenticated_producer_digest(
            raw_document,
            requested_digest=_requested_digest(raw_document),
        )
        == PRODUCER_DIGEST
    )


@pytest.mark.parametrize(
    "document",
    [
        {"schemaVersion": 2, "mediaType": "application/example", "manifests": []},
        {
            "schemaVersion": 2,
            "mediaType": "application/vnd.oci.image.index.v1+json",
            "manifests": [
                {
                    "mediaType": "application/vnd.oci.image.manifest.v1+json",
                    "digest": PRODUCER_DIGEST,
                    "platform": {"architecture": "amd64", "os": "linux"},
                }
            ],
        },
        {
            "schemaVersion": 2,
            "mediaType": "application/vnd.oci.image.index.v1+json",
            "manifests": [
                {
                    "mediaType": "application/vnd.oci.image.manifest.v1+json",
                    "digest": PRODUCER_DIGEST,
                    "size": 1,
                    "platform": {"architecture": "arm64", "os": "linux"},
                }
            ],
        },
        {
            "schemaVersion": 2,
            "mediaType": "application/vnd.oci.image.index.v1+json",
            "manifests": [
                {
                    "mediaType": "application/vnd.oci.image.manifest.v1+json",
                    "digest": PRODUCER_DIGEST,
                    "size": 1,
                    "annotations": {"org.opencontainers.image.architecture": "amd64"},
                }
            ],
        },
        {
            "schemaVersion": 2,
            "mediaType": "application/vnd.oci.image.index.v1+json",
            "manifests": [
                {
                    "mediaType": "application/vnd.oci.image.manifest.v1+json",
                    "digest": "sha256:" + "A" * 64,
                    "size": 1,
                    "platform": {"architecture": "amd64", "os": "linux"},
                }
            ],
        },
    ],
)
def test_resolver_rejects_closed_world_invalid_oci_contours(document: dict[str, object]):
    raw_document = json.dumps(document, separators=(",", ":")).encode("utf-8")

    with pytest.raises(ValueError):
        resolve_authenticated_producer_digest(
            raw_document,
            requested_digest=_requested_digest(raw_document),
        )


def test_resolver_rejects_tampering_duplicate_keys_and_duplicate_platforms():
    raw_document = _oci_index_bytes()
    with pytest.raises(ValueError, match="oci_document_digest"):
        resolve_authenticated_producer_digest(
            raw_document,
            requested_digest="sha256:" + "0" * 64,
        )
    with pytest.raises(ValueError, match="json_duplicate_key"):
        duplicate_key_document = (
            b'{"schemaVersion":2,"schemaVersion":2,'
            b'"mediaType":"application/vnd.oci.image.index.v1+json","manifests":[]}'
        )
        resolve_authenticated_producer_digest(
            duplicate_key_document,
            requested_digest=_requested_digest(duplicate_key_document),
        )
    document = json.loads(raw_document)
    document["manifests"].append(copy.deepcopy(document["manifests"][0]))
    duplicate_platform = json.dumps(document, separators=(",", ":")).encode("utf-8")
    with pytest.raises(ValueError, match="oci_linux_amd64_descriptor"):
        resolve_authenticated_producer_digest(
            duplicate_platform,
            requested_digest=_requested_digest(duplicate_platform),
        )


def test_resolver_accepts_an_authenticated_direct_image_manifest():
    raw_document = json.dumps(
        {
            "schemaVersion": 2,
            "mediaType": "application/vnd.oci.image.manifest.v1+json",
            "config": {
                "mediaType": "application/vnd.oci.image.config.v1+json",
                "digest": "sha256:" + "1" * 64,
                "size": 1,
            },
            "layers": [],
        },
        separators=(",", ":"),
    ).encode("utf-8")

    assert (
        resolve_authenticated_producer_digest(
            raw_document,
            requested_digest=_requested_digest(raw_document),
        )
        == _requested_digest(raw_document)
    )
