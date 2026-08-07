"""Strict, pure resolver for the producer manifest behind an immutable OCI subject."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any


_DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
_OCI_INDEX_MEDIA_TYPES = frozenset(
    {
        "application/vnd.oci.image.index.v1+json",
        "application/vnd.docker.distribution.manifest.list.v2+json",
    }
)
_OCI_IMAGE_MANIFEST_MEDIA_TYPES = frozenset(
    {
        "application/vnd.oci.image.manifest.v1+json",
        "application/vnd.docker.distribution.manifest.v2+json",
    }
)
_OCI_CONFIG_MEDIA_TYPES = frozenset(
    {
        "application/vnd.oci.image.config.v1+json",
        "application/vnd.docker.container.image.v1+json",
    }
)
_OCI_LAYER_MEDIA_TYPES = frozenset(
    {
        "application/vnd.oci.image.layer.v1.tar",
        "application/vnd.oci.image.layer.v1.tar+gzip",
        "application/vnd.oci.image.layer.v1.tar+zstd",
        "application/vnd.oci.image.layer.nondistributable.v1.tar",
        "application/vnd.oci.image.layer.nondistributable.v1.tar+gzip",
        "application/vnd.oci.image.layer.nondistributable.v1.tar+zstd",
        "application/vnd.docker.image.rootfs.diff.tar",
        "application/vnd.docker.image.rootfs.diff.tar.gzip",
        "application/vnd.docker.image.rootfs.foreign.diff.tar.gzip",
    }
)
_OCI_DOCUMENT_COMMON_KEYS = {
    "schemaVersion",
    "mediaType",
    "annotations",
    "artifactType",
    "subject",
}
_OCI_IMAGE_MANIFEST_KEYS = _OCI_DOCUMENT_COMMON_KEYS | {"config", "layers"}
_OCI_INDEX_KEYS = _OCI_DOCUMENT_COMMON_KEYS | {"manifests"}
_OCI_DESCRIPTOR_KEYS = {
    "mediaType",
    "digest",
    "size",
    "urls",
    "annotations",
    "platform",
    "artifactType",
    "data",
}
_OCI_PLATFORM_KEYS = {"architecture", "os", "variant"}
_OCI_PLATFORM_VALUE = re.compile(r"[a-z0-9][a-z0-9._-]*")


class _DuplicateJsonKey(ValueError):
    pass


def _reject_duplicate_json_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise _DuplicateJsonKey(key)
        value[key] = item
    return value


def _loads_json(payload: bytes) -> Any:
    try:
        return json.loads(payload.decode("utf-8"), object_pairs_hook=_reject_duplicate_json_keys)
    except _DuplicateJsonKey as exc:
        raise ValueError("json_duplicate_key") from exc
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValueError("oci_document") from exc


def _object(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{name}_object")
    return value


def _validate_descriptor(
    value: Any,
    *,
    allowed_media_types: frozenset[str] = _OCI_IMAGE_MANIFEST_MEDIA_TYPES,
) -> dict[str, Any]:
    descriptor = _object(value, "oci_descriptor")
    if not {"mediaType", "digest", "size"}.issubset(descriptor) or not set(descriptor).issubset(
        _OCI_DESCRIPTOR_KEYS
    ):
        raise ValueError("oci_descriptor")
    if descriptor["mediaType"] not in allowed_media_types:
        raise ValueError("oci_descriptor_media_type")
    digest = descriptor["digest"]
    if not isinstance(digest, str) or _DIGEST.fullmatch(digest) is None:
        raise ValueError("oci_descriptor_digest")
    if (
        not isinstance(descriptor["size"], int)
        or isinstance(descriptor["size"], bool)
        or descriptor["size"] < 0
    ):
        raise ValueError("oci_descriptor_size")
    platform = descriptor.get("platform")
    if platform is not None:
        platform = _object(platform, "oci_descriptor_platform")
        if not {"architecture", "os"}.issubset(platform) or not set(platform).issubset(
            _OCI_PLATFORM_KEYS
        ):
            raise ValueError("oci_descriptor_platform")
        if any(
            not isinstance(platform[key], str)
            or _OCI_PLATFORM_VALUE.fullmatch(platform[key]) is None
            for key in ("architecture", "os")
        ):
            raise ValueError("oci_descriptor_platform")
        if "variant" in platform and (
            not isinstance(platform["variant"], str)
            or _OCI_PLATFORM_VALUE.fullmatch(platform["variant"]) is None
        ):
            raise ValueError("oci_descriptor_platform")
    if "urls" in descriptor and (
        not isinstance(descriptor["urls"], list)
        or any(not isinstance(url, str) or not url for url in descriptor["urls"])
    ):
        raise ValueError("oci_descriptor")
    if "annotations" in descriptor and (
        not isinstance(descriptor["annotations"], dict)
        or any(
            not isinstance(key, str) or not isinstance(item, str)
            for key, item in descriptor["annotations"].items()
        )
    ):
        raise ValueError("oci_descriptor")
    return descriptor


def resolve_authenticated_producer_digest(raw_document: bytes, *, requested_digest: str) -> str:
    """Authenticate OCI bytes and return their sole permitted linux/amd64 producer.

    A direct image manifest is its own producer. An index/list must contain exactly
    one canonical linux/amd64 descriptor; valid non-selected platforms are retained
    as syntax only and cannot influence the selected digest.
    """
    if f"sha256:{hashlib.sha256(raw_document).hexdigest()}" != requested_digest:
        raise ValueError("oci_document_digest")
    document = _object(_loads_json(raw_document), "oci_document")
    if not {"schemaVersion", "mediaType"}.issubset(document):
        raise ValueError("oci_document")
    if document["schemaVersion"] != 2:
        raise ValueError("oci_document_schema")
    media_type = document["mediaType"]
    if media_type in _OCI_IMAGE_MANIFEST_MEDIA_TYPES:
        if not {"config", "layers"}.issubset(document) or not set(document).issubset(
            _OCI_IMAGE_MANIFEST_KEYS
        ):
            raise ValueError("oci_image_manifest")
        _validate_descriptor(document["config"], allowed_media_types=_OCI_CONFIG_MEDIA_TYPES)
        if not isinstance(document["layers"], list):
            raise ValueError("oci_image_manifest")
        for layer in document["layers"]:
            _validate_descriptor(layer, allowed_media_types=_OCI_LAYER_MEDIA_TYPES)
        return requested_digest
    if media_type not in _OCI_INDEX_MEDIA_TYPES:
        raise ValueError("oci_document_media_type")
    if not set(document).issubset(_OCI_INDEX_KEYS):
        raise ValueError("oci_index")
    manifests = document.get("manifests")
    if not isinstance(manifests, list) or not manifests:
        raise ValueError("oci_index")
    candidates = []
    for value in manifests:
        descriptor = _validate_descriptor(value)
        platform = descriptor.get("platform")
        if platform == {"architecture": "amd64", "os": "linux"}:
            candidates.append(descriptor)
    if len(candidates) != 1:
        raise ValueError("oci_linux_amd64_descriptor")
    return str(candidates[0]["digest"])
