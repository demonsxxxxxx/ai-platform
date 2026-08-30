"""Provider-neutral Knowledge connection and source values."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit, urlunsplit


class KnowledgeError(ValueError):
    """A bounded, safe Knowledge error code."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def canonical_connection_name(value: str) -> str:
    normalized = " ".join(value.split())
    if not normalized or len(normalized) > 120:
        raise KnowledgeError("knowledge_connection_name_invalid")
    if any(ord(character) < 32 or ord(character) == 127 for character in normalized):
        raise KnowledgeError("knowledge_connection_name_invalid")
    return normalized


def canonical_origin(value: str) -> str:
    normalized = value.strip().rstrip("/")
    if not normalized or len(normalized) > 2048 or any(char in normalized for char in "\r\n\x00"):
        raise KnowledgeError("knowledge_connection_endpoint_invalid")
    try:
        parsed = urlsplit(normalized)
        port = parsed.port
    except ValueError as exc:
        raise KnowledgeError("knowledge_connection_endpoint_invalid") from exc
    if (
        parsed.scheme.lower() not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path.rstrip("/")
    ):
        raise KnowledgeError("knowledge_connection_endpoint_invalid")
    hostname = parsed.hostname.rstrip(".").lower()
    default_port = 443 if parsed.scheme.lower() == "https" else 80
    authority_host = f"[{hostname}]" if ":" in hostname else hostname
    authority = authority_host if port in {None, default_port} else f"{authority_host}:{port}"
    return urlunsplit((parsed.scheme.lower(), authority, "", "", ""))


@dataclass(frozen=True)
class KnowledgeConnectionDefinition:
    provider_key: str
    base_url: str
    secret_ref: str
    transport_policy: dict[str, Any]

    def content_hash(self) -> str:
        canonical = json.dumps(
            {
                "provider_key": self.provider_key,
                "base_url": self.base_url,
                "secret_ref": self.secret_ref,
                "transport_policy": self.transport_policy,
            },
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ProviderSourceRecord:
    provider_resource_id: str
    provider_name: str
    provider_metadata: dict[str, Any]

    def __post_init__(self) -> None:
        resource_id = self.provider_resource_id.strip()
        name = " ".join(self.provider_name.split())
        if not resource_id or len(resource_id.encode("utf-8")) > 512:
            raise KnowledgeError("knowledge_provider_catalog_invalid")
        if not name or len(name) > 240:
            raise KnowledgeError("knowledge_provider_catalog_invalid")
        if any(ord(character) < 32 for character in resource_id + name):
            raise KnowledgeError("knowledge_provider_catalog_invalid")
        if len(json.dumps(self.provider_metadata, ensure_ascii=False).encode("utf-8")) > 8192:
            raise KnowledgeError("knowledge_provider_catalog_invalid")
        object.__setattr__(self, "provider_resource_id", resource_id)
        object.__setattr__(self, "provider_name", name)

    def digest(self) -> str:
        canonical = json.dumps(
            {
                "provider_resource_id": self.provider_resource_id,
                "provider_name": self.provider_name,
                "provider_metadata": self.provider_metadata,
            },
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ProviderCatalogSnapshot:
    records: tuple[ProviderSourceRecord, ...]
    page_count: int

    def __post_init__(self) -> None:
        if self.page_count < 1:
            raise KnowledgeError("knowledge_provider_catalog_invalid")
        identities = [record.provider_resource_id for record in self.records]
        if len(identities) != len(set(identities)):
            raise KnowledgeError("knowledge_provider_catalog_invalid")
