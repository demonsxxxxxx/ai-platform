"""Strict, pure resolver for the producer manifest behind an immutable OCI subject."""

from __future__ import annotations

import base64
import binascii
import hashlib
import ipaddress
import json
import re
from typing import Any
from urllib.parse import urlsplit


MAX_OCI_DOCUMENT_BYTES = 4 * 1024 * 1024
_MAX_OCI_JSON_NESTING = 64
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
_MEDIA_TYPE = re.compile(r"[a-z0-9][a-z0-9!#$&^_.+-]*/[a-z0-9][a-z0-9!#$&^_.+-]*")
_URI_SCHEME = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*")
_URI_IPV_FUTURE = re.compile(r"[vV][0-9A-Fa-f]+\.[A-Za-z0-9._~!$&'()*+,;=:-]+")
_URI_HEX_DIGITS = frozenset("0123456789ABCDEFabcdef")
_URI_UNRESERVED = frozenset(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~"
)
_URI_SUB_DELIMITERS = frozenset("!$&'()*+,;=")
_URI_PCHAR = _URI_UNRESERVED | _URI_SUB_DELIMITERS | frozenset(":@")
_URI_PATH = _URI_PCHAR | frozenset("/")
_URI_QUERY_OR_FRAGMENT = _URI_PCHAR | frozenset("/?")
_URI_USERINFO = _URI_UNRESERVED | _URI_SUB_DELIMITERS | frozenset(":")
_URI_REG_NAME = _URI_UNRESERVED | _URI_SUB_DELIMITERS


class _DuplicateJsonKey(ValueError):
    pass


def _reject_duplicate_json_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise _DuplicateJsonKey(key)
        value[key] = item
    return value


def _validate_document_size(payload: bytes) -> None:
    # Distribution registries cap manifest bodies at 4 MiB; keep this trust parser
    # bounded to the same metadata envelope rather than accepting blob-sized JSON.
    if len(payload) > MAX_OCI_DOCUMENT_BYTES:
        raise ValueError("oci_document")


def _validate_json_bounds(payload: bytes) -> None:
    _validate_document_size(payload)
    depth = 0
    in_string = False
    escaped = False
    for byte in payload:
        if in_string:
            if escaped:
                escaped = False
            elif byte == ord("\\"):
                escaped = True
            elif byte == ord('"'):
                in_string = False
            continue
        if byte == ord('"'):
            in_string = True
        elif byte in (ord("["), ord("{")):
            depth += 1
            if depth > _MAX_OCI_JSON_NESTING:
                raise ValueError("oci_document")
        elif byte in (ord("]"), ord("}")):
            depth -= 1


def _validate_unicode_scalars(value: Any) -> None:
    pending = [value]
    while pending:
        current = pending.pop()
        if isinstance(current, dict):
            pending.extend(current.keys())
            pending.extend(current.values())
            continue
        if isinstance(current, list):
            pending.extend(current)
            continue
        if not isinstance(current, str):
            continue

        index = 0
        while index < len(current):
            code_point = ord(current[index])
            if 0xD800 <= code_point <= 0xDBFF:
                if index + 1 >= len(current) or not (
                    0xDC00 <= ord(current[index + 1]) <= 0xDFFF
                ):
                    raise ValueError("oci_document")
                index += 2
            elif 0xDC00 <= code_point <= 0xDFFF:
                raise ValueError("oci_document")
            else:
                index += 1


def _loads_json(payload: bytes) -> Any:
    _validate_json_bounds(payload)
    try:
        value = json.loads(payload.decode("utf-8"), object_pairs_hook=_reject_duplicate_json_keys)
    except _DuplicateJsonKey as exc:
        raise ValueError("json_duplicate_key") from exc
    except (json.JSONDecodeError, RecursionError, UnicodeDecodeError) as exc:
        raise ValueError("oci_document") from exc
    _validate_unicode_scalars(value)
    return value


def _object(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{name}_object")
    return value


def _validate_annotations(value: Any, name: str) -> None:
    annotations = _object(value, name)
    if any(
        not isinstance(key, str) or not isinstance(item, str)
        for key, item in annotations.items()
    ):
        raise ValueError(name)


def _validate_media_type(value: Any, name: str) -> None:
    if not isinstance(value, str) or _MEDIA_TYPE.fullmatch(value) is None:
        raise ValueError(name)


def _is_uri_component(value: str, allowed: frozenset[str]) -> bool:
    index = 0
    while index < len(value):
        char = value[index]
        if char == "%":
            if (
                index + 2 >= len(value)
                or value[index + 1] not in _URI_HEX_DIGITS
                or value[index + 2] not in _URI_HEX_DIGITS
            ):
                return False
            index += 3
            continue
        if char not in allowed:
            return False
        index += 1
    return True


def _is_uri_authority(value: str) -> bool:
    userinfo, separator, host_port = value.rpartition("@")
    if separator and not _is_uri_component(userinfo, _URI_USERINFO):
        return False

    if host_port.startswith("["):
        close = host_port.find("]")
        if close < 0:
            return False
        literal = host_port[1:close]
        suffix = host_port[close + 1 :]
        if (
            not literal
            or "%" in literal
            or (suffix and not suffix.startswith(":"))
            or "]" in suffix
        ):
            return False
        if _URI_IPV_FUTURE.fullmatch(literal) is None:
            try:
                ipaddress.IPv6Address(literal)
            except ipaddress.AddressValueError:
                return False
        port = suffix[1:] if suffix else None
    else:
        if "[" in host_port or "]" in host_port or host_port.count(":") > 1:
            return False
        host, colon, port_value = host_port.rpartition(":")
        if colon:
            host_port = host
            port = port_value
        else:
            port = None
        if not _is_uri_component(host_port, _URI_REG_NAME):
            return False

    return port is None or not port or port.isascii() and port.isdecimal()


def _is_absolute_rfc3986_uri(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    try:
        value.encode("ascii")
    except UnicodeEncodeError:
        return False
    if not value or any(ord(char) <= 0x20 or ord(char) == 0x7F for char in value):
        return False
    scheme, separator, remainder = value.partition(":")
    if not separator or _URI_SCHEME.fullmatch(scheme) is None:
        return False
    try:
        parsed = urlsplit(value)
    except ValueError:
        return False
    if parsed.scheme.casefold() != scheme.casefold():
        return False

    has_authority = remainder.startswith("//")
    if has_authority:
        if not _is_uri_authority(parsed.netloc) or parsed.path and not parsed.path.startswith("/"):
            return False
    elif parsed.netloc or parsed.path.startswith("//"):
        return False
    return (
        _is_uri_component(parsed.path, _URI_PATH)
        and _is_uri_component(parsed.query, _URI_QUERY_OR_FRAGMENT)
        and _is_uri_component(parsed.fragment, _URI_QUERY_OR_FRAGMENT)
    )


def _validate_descriptor(
    value: Any,
    *,
    allowed_media_types: frozenset[str] | None = _OCI_IMAGE_MANIFEST_MEDIA_TYPES,
) -> dict[str, Any]:
    descriptor = _object(value, "oci_descriptor")
    if not {"mediaType", "digest", "size"}.issubset(descriptor) or not set(descriptor).issubset(
        _OCI_DESCRIPTOR_KEYS
    ):
        raise ValueError("oci_descriptor")
    media_type = descriptor["mediaType"]
    if allowed_media_types is None:
        _validate_media_type(media_type, "oci_descriptor_media_type")
    elif media_type not in allowed_media_types:
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
    if "platform" in descriptor:
        platform = _object(descriptor["platform"], "oci_descriptor_platform")
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
    if "urls" in descriptor:
        urls = descriptor["urls"]
        if not isinstance(urls, list) or any(
            not _is_absolute_rfc3986_uri(url) for url in urls
        ):
            raise ValueError("oci_descriptor_urls")
    if "annotations" in descriptor:
        _validate_annotations(descriptor["annotations"], "oci_descriptor_annotations")
    if "artifactType" in descriptor:
        _validate_media_type(descriptor["artifactType"], "oci_descriptor_artifact_type")
    if "data" in descriptor:
        data = descriptor["data"]
        if not isinstance(data, str):
            raise ValueError("oci_descriptor_data")
        try:
            decoded = base64.b64decode(data, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ValueError("oci_descriptor_data") from exc
        if (
            base64.b64encode(decoded).decode("ascii") != data
            or len(decoded) != descriptor["size"]
            or f"sha256:{hashlib.sha256(decoded).hexdigest()}" != digest
        ):
            raise ValueError("oci_descriptor_data")
    return descriptor


def _validate_document_optionals(document: dict[str, Any]) -> None:
    if "annotations" in document:
        _validate_annotations(document["annotations"], "oci_document_annotations")
    if "artifactType" in document:
        _validate_media_type(document["artifactType"], "oci_document_artifact_type")
    if "subject" in document:
        _validate_descriptor(document["subject"], allowed_media_types=None)


def resolve_authenticated_producer_digest(raw_document: bytes, *, requested_digest: str) -> str:
    """Authenticate OCI bytes and return their sole permitted linux/amd64 producer.

    A direct image manifest is its own producer. An index/list must contain exactly
    one canonical linux/amd64 descriptor; valid non-selected platforms are retained
    as syntax only and cannot influence the selected digest.
    """
    _validate_document_size(raw_document)
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
        _validate_document_optionals(document)
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
    _validate_document_optionals(document)
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
