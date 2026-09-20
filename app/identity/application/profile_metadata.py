from __future__ import annotations

import json
from typing import Any, Protocol


PROFILE_METADATA_MAX_BYTES = 16 * 1024
PROFILE_METADATA_MAX_KEYS = 64
COMPANY_NAVIGATION_FAVORITES_KEY = "company_navigation_favorite_ids"
COMPANY_NAVIGATION_FAVORITES_MAX_ITEMS = 256
_PROFILE_METADATA_RESERVED_KEYS = frozenset({"display_name", "source"})


class ProfileMetadataValidationError(ValueError):
    pass


class ProfileMetadataScopeError(RuntimeError):
    pass


class ProfileMetadataStore(Protocol):
    async def get(
        self,
        *,
        tenant_id: str,
        user_id: str,
        display_name: str,
    ) -> dict[str, Any] | None: ...

    async def merge(
        self,
        *,
        tenant_id: str,
        user_id: str,
        display_name: str,
        patch: dict[str, Any],
    ) -> dict[str, Any] | None: ...


def validate_profile_metadata(metadata: object) -> dict[str, Any]:
    if not isinstance(metadata, dict):
        raise ProfileMetadataValidationError("profile_metadata_invalid")
    if len(metadata) > PROFILE_METADATA_MAX_KEYS:
        raise ProfileMetadataValidationError("profile_metadata_too_many_keys")
    if any(
        not isinstance(key, str) or not key or len(key) > 128
        for key in metadata
    ):
        raise ProfileMetadataValidationError("profile_metadata_key_invalid")
    if _PROFILE_METADATA_RESERVED_KEYS.intersection(metadata):
        raise ProfileMetadataValidationError("profile_metadata_reserved_key")

    favorite_ids = metadata.get(COMPANY_NAVIGATION_FAVORITES_KEY)
    if favorite_ids is not None and (
        not isinstance(favorite_ids, list)
        or len(favorite_ids) > COMPANY_NAVIGATION_FAVORITES_MAX_ITEMS
        or any(
            not isinstance(item, str) or not item or len(item) > 256
            for item in favorite_ids
        )
        or len(set(favorite_ids)) != len(favorite_ids)
    ):
        raise ProfileMetadataValidationError("company_navigation_favorites_invalid")

    try:
        encoded = json.dumps(
            metadata,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ProfileMetadataValidationError("profile_metadata_invalid") from exc
    if len(encoded) > PROFILE_METADATA_MAX_BYTES:
        raise ProfileMetadataValidationError("profile_metadata_too_large")
    return dict(metadata)


class ProfileMetadataService:
    def __init__(self, store: ProfileMetadataStore) -> None:
        self._store = store

    async def get(
        self,
        *,
        tenant_id: str,
        user_id: str,
        display_name: str,
    ) -> dict[str, Any]:
        metadata = await self._store.get(
            tenant_id=tenant_id,
            user_id=user_id,
            display_name=display_name,
        )
        if metadata is None:
            raise ProfileMetadataScopeError("profile_user_scope_mismatch")
        return metadata

    async def merge(
        self,
        *,
        tenant_id: str,
        user_id: str,
        display_name: str,
        patch: object,
    ) -> dict[str, Any]:
        metadata = await self._store.merge(
            tenant_id=tenant_id,
            user_id=user_id,
            display_name=display_name,
            patch=validate_profile_metadata(patch),
        )
        if metadata is None:
            raise ProfileMetadataScopeError("profile_user_scope_mismatch")
        return metadata
