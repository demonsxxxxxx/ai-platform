import base64
import copy
import hashlib
import json

import pytest

from tools import oci_image_manifest
from tools.oci_image_manifest import MAX_OCI_DOCUMENT_BYTES, resolve_authenticated_producer_digest


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


def _padded_oci_index_bytes(size: int) -> bytes:
    document = json.loads(_oci_index_bytes())
    document["annotations"] = {"org.example.padding": ""}
    unpadded = json.dumps(document, separators=(",", ":")).encode("utf-8")
    padding = size - len(unpadded)
    assert padding >= 0
    document["annotations"]["org.example.padding"] = "x" * padding
    payload = json.dumps(document, separators=(",", ":")).encode("utf-8")
    assert len(payload) == size
    return payload


def _resolve_document(document: dict[str, object]) -> str:
    raw_document = json.dumps(document, separators=(",", ":")).encode("utf-8")
    return resolve_authenticated_producer_digest(
        raw_document,
        requested_digest=_requested_digest(raw_document),
    )


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


def test_resolver_rejects_manifests_on_a_direct_image_manifest():
    document = {
        "schemaVersion": 2,
        "mediaType": "application/vnd.oci.image.manifest.v1+json",
        "config": {
            "mediaType": "application/vnd.oci.image.config.v1+json",
            "digest": "sha256:" + "1" * 64,
            "size": 1,
        },
        "layers": [],
        "manifests": [],
    }
    raw_document = json.dumps(document, separators=(",", ":")).encode("utf-8")

    with pytest.raises(ValueError, match="oci_image_manifest"):
        resolve_authenticated_producer_digest(
            raw_document,
            requested_digest=_requested_digest(raw_document),
        )


def test_resolver_rejects_config_on_an_image_index():
    document = json.loads(_oci_index_bytes())
    document["config"] = {
        "mediaType": "application/vnd.oci.image.config.v1+json",
        "digest": "sha256:" + "1" * 64,
        "size": 1,
    }
    raw_document = json.dumps(document, separators=(",", ":")).encode("utf-8")

    with pytest.raises(ValueError, match="oci_index"):
        resolve_authenticated_producer_digest(
            raw_document,
            requested_digest=_requested_digest(raw_document),
        )


def test_resolver_rejects_empty_nonselected_platform_architecture_and_os():
    document = json.loads(_oci_index_bytes())
    document["manifests"].insert(
        0,
        {
            "mediaType": "application/vnd.oci.image.manifest.v1+json",
            "digest": "sha256:" + "1" * 64,
            "size": 1,
            "platform": {"architecture": "", "os": ""},
        },
    )
    raw_document = json.dumps(document, separators=(",", ":")).encode("utf-8")

    with pytest.raises(ValueError, match="oci_descriptor_platform"):
        resolve_authenticated_producer_digest(
            raw_document,
            requested_digest=_requested_digest(raw_document),
        )


def test_resolver_rejects_present_variant_on_selected_linux_amd64():
    for variant, reason in (
        ("", "oci_descriptor_platform"),
        ("v1", "oci_linux_amd64_descriptor"),
    ):
        document = json.loads(_oci_index_bytes())
        document["manifests"][0]["platform"]["variant"] = variant
        raw_document = json.dumps(document, separators=(",", ":")).encode("utf-8")

        with pytest.raises(ValueError, match=reason):
            resolve_authenticated_producer_digest(
                raw_document,
                requested_digest=_requested_digest(raw_document),
            )


def test_resolver_rejects_explicit_null_descriptor_platform():
    document = json.loads(_oci_index_bytes())
    document["manifests"].insert(
        0,
        {
            "mediaType": "application/vnd.oci.image.manifest.v1+json",
            "digest": "sha256:" + "1" * 64,
            "size": 1,
            "platform": None,
        },
    )

    with pytest.raises(ValueError, match="oci_descriptor_platform"):
        _resolve_document(document)


def test_resolver_rejects_nonmapping_document_annotations():
    document = json.loads(_oci_index_bytes())
    document["annotations"] = []

    with pytest.raises(ValueError, match="oci_document_annotations"):
        _resolve_document(document)


def test_resolver_rejects_nonstring_document_artifact_type():
    document = json.loads(_oci_index_bytes())
    document["artifactType"] = 7

    with pytest.raises(ValueError, match="oci_document_artifact_type"):
        _resolve_document(document)


def test_resolver_rejects_nondescriptor_document_subject():
    document = json.loads(_oci_index_bytes())
    document["subject"] = "not-a-descriptor"

    with pytest.raises(ValueError, match="oci_descriptor_object"):
        _resolve_document(document)


def test_resolver_rejects_nonstring_descriptor_artifact_type():
    document = json.loads(_oci_index_bytes())
    document["manifests"][0]["artifactType"] = 7

    with pytest.raises(ValueError, match="oci_descriptor_artifact_type"):
        _resolve_document(document)


def test_resolver_rejects_nonstring_descriptor_data():
    document = json.loads(_oci_index_bytes())
    document["manifests"][0]["data"] = 7

    with pytest.raises(ValueError, match="oci_descriptor_data"):
        _resolve_document(document)


@pytest.mark.parametrize("field", ["annotations", "artifactType", "subject"])
def test_resolver_rejects_explicit_null_document_optionals(field: str):
    document = json.loads(_oci_index_bytes())
    document[field] = None

    with pytest.raises(ValueError):
        _resolve_document(document)


@pytest.mark.parametrize(
    ("data", "size", "digest"),
    [
        ("***", 7, "sha256:" + "1" * 64),
        (
            base64.b64encode(b"payload").decode("ascii"),
            8,
            "sha256:" + hashlib.sha256(b"payload").hexdigest(),
        ),
        (base64.b64encode(b"payload").decode("ascii"), 7, "sha256:" + "1" * 64),
    ],
)
def test_resolver_rejects_descriptor_data_not_bound_to_size_and_digest(
    data: str,
    size: int,
    digest: str,
):
    document = json.loads(_oci_index_bytes(child_digest=digest))
    descriptor = document["manifests"][0]
    descriptor["data"] = data
    descriptor["size"] = size

    with pytest.raises(ValueError, match="oci_descriptor_data"):
        _resolve_document(document)


def test_resolver_accepts_valid_document_and_descriptor_optional_fields():
    producer_data = b"authenticated producer manifest"
    producer_digest = f"sha256:{hashlib.sha256(producer_data).hexdigest()}"
    subject_data = b"subject manifest"
    subject_digest = f"sha256:{hashlib.sha256(subject_data).hexdigest()}"
    document = json.loads(_oci_index_bytes(child_digest=producer_digest))
    document.update(
        {
            "annotations": {
                "org.opencontainers.image.ref.name": "release",
                "org.example.empty": "",
            },
            "artifactType": "application/vnd.example.release.v1+json",
            "subject": {
                "mediaType": "application/vnd.example.subject.v1+json",
                "digest": subject_digest,
                "size": len(subject_data),
                "urls": ["https://registry.example/v2/subject"],
                "annotations": {"org.example.kind": "subject"},
                "artifactType": "application/vnd.example.subject.v1+json",
                "data": base64.b64encode(subject_data).decode("ascii"),
            },
        }
    )
    document["manifests"][0].update(
        {
            "size": len(producer_data),
            "urls": ["https://registry.example/v2/producer"],
            "annotations": {"org.example.kind": "producer"},
            "artifactType": "application/vnd.example.release.v1+json",
            "data": base64.b64encode(producer_data).decode("ascii"),
        }
    )

    assert _resolve_document(document) == producer_digest


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("annotations", {"org.example.invalid": 7}),
        ("artifactType", None),
        ("artifactType", ""),
        ("artifactType", "not-a-media-type"),
        (
            "subject",
            {
                "mediaType": "application/vnd.example.subject.v1+json",
                "digest": "sha256:" + "1" * 64,
            },
        ),
    ],
)
def test_resolver_rejects_invalid_document_optional_values(field: str, value: object):
    document = json.loads(_oci_index_bytes())
    document[field] = value

    with pytest.raises(ValueError):
        _resolve_document(document)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("urls", None),
        ("urls", [7]),
        ("urls", [""]),
        ("urls", ["relative/reference"]),
        ("annotations", None),
        ("annotations", {"org.example.invalid": 7}),
        ("artifactType", None),
        ("artifactType", "not-a-media-type"),
        ("data", None),
        ("data", "cGF5bG9hZA"),
    ],
)
def test_resolver_rejects_invalid_descriptor_optional_values(field: str, value: object):
    document = json.loads(_oci_index_bytes())
    document["manifests"][0][field] = value

    with pytest.raises(ValueError):
        _resolve_document(document)


@pytest.mark.parametrize(
    "uri",
    [
        "https://example/\N{LATIN SMALL LETTER E WITH ACUTE}",
        "https://example/%ZZ",
        "https://[bad",
        "scheme:%",
    ],
)
def test_resolver_rejects_non_rfc3986_descriptor_urls(uri: str):
    document = json.loads(_oci_index_bytes())
    document["manifests"][0]["urls"] = [uri]

    with pytest.raises(ValueError, match="oci_descriptor_urls"):
        _resolve_document(document)


@pytest.mark.parametrize(
    "uri",
    [
        "https://registry.example:443/v2/image/%E2%82%AC?source=ci#digest",
        "https://[2001:db8::1]:443/v2/image",
        "urn:example:animal:ferret:nose",
        "mailto:release@example.com",
        "file:///var/lib/oci/index.json",
        "scheme:/absolute/path",
    ],
)
def test_resolver_accepts_valid_absolute_rfc3986_descriptor_urls(uri: str):
    document = json.loads(_oci_index_bytes())
    document["manifests"][0]["urls"] = [uri]

    assert _resolve_document(document) == PRODUCER_DIGEST


@pytest.mark.parametrize(
    "uri",
    [
        "https://example:bad/path",
        "https://[2001:db8::1]tail/path",
        "https://[vG.bad]/path",
        "https://user@@example/path",
        "https://example/path with space",
        "https://example/control\x01",
        "https://example/100%",
        "relative/reference",
    ],
)
def test_resolver_rejects_structurally_invalid_absolute_uris(uri: str):
    document = json.loads(_oci_index_bytes())
    document["manifests"][0]["urls"] = [uri]

    with pytest.raises(ValueError, match="oci_descriptor_urls"):
        _resolve_document(document)


@pytest.mark.parametrize(
    "uri",
    [
        "data:text/plain,hello",
        "scheme:",
        "scheme://user:info@example:/path",
        "https://[v1.a]:443/",
    ],
)
def test_resolver_preserves_other_valid_rfc3986_uri_forms(uri: str):
    document = json.loads(_oci_index_bytes())
    document["manifests"][0]["urls"] = [uri]

    assert _resolve_document(document) == PRODUCER_DIGEST


def test_resolver_accepts_document_at_raw_json_byte_limit():
    raw_document = _padded_oci_index_bytes(MAX_OCI_DOCUMENT_BYTES)

    assert (
        resolve_authenticated_producer_digest(
            raw_document,
            requested_digest=_requested_digest(raw_document),
        )
        == PRODUCER_DIGEST
    )


def test_resolver_rejects_document_over_raw_json_byte_limit():
    raw_document = _padded_oci_index_bytes(MAX_OCI_DOCUMENT_BYTES + 1)

    with pytest.raises(ValueError, match="^oci_document$"):
        resolve_authenticated_producer_digest(
            raw_document,
            requested_digest=_requested_digest(raw_document),
        )


def test_resolver_rejects_oversize_before_json_parse(monkeypatch: pytest.MonkeyPatch):
    raw_document = b"x" * (MAX_OCI_DOCUMENT_BYTES + 1)

    def fail_if_called(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("json parser must not receive oversized input")

    monkeypatch.setattr(oci_image_manifest.json, "loads", fail_if_called)
    with pytest.raises(ValueError, match="^oci_document$"):
        resolve_authenticated_producer_digest(
            raw_document,
            requested_digest=_requested_digest(raw_document),
        )


def test_resolver_classifies_excessive_json_nesting_as_oci_document():
    raw_document = (
        b'{"schemaVersion":2,"mediaType":"application/vnd.oci.image.index.v1+json",'
        b'"manifests":'
        + b"[" * 10_000
        + b"0"
        + b"]" * 10_000
        + b"}"
    )

    with pytest.raises(ValueError, match="^oci_document$"):
        resolve_authenticated_producer_digest(
            raw_document,
            requested_digest=_requested_digest(raw_document),
        )


def test_resolver_normalizes_json_parser_recursion_error(monkeypatch: pytest.MonkeyPatch):
    raw_document = _oci_index_bytes()

    def raise_recursion(*_args: object, **_kwargs: object) -> object:
        raise RecursionError("synthetic parser recursion")

    monkeypatch.setattr(oci_image_manifest.json, "loads", raise_recursion)
    with pytest.raises(ValueError, match="^oci_document$"):
        resolve_authenticated_producer_digest(
            raw_document,
            requested_digest=_requested_digest(raw_document),
        )


@pytest.mark.parametrize("raw_document", [b"{", b"\xff"])
def test_resolver_classifies_invalid_json_and_unicode_as_oci_document(raw_document: bytes):
    with pytest.raises(ValueError, match="^oci_document$"):
        resolve_authenticated_producer_digest(
            raw_document,
            requested_digest=_requested_digest(raw_document),
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
