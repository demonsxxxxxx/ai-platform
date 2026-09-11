from contextlib import asynccontextmanager

import pytest

from app.identity import api
from app.identity.application.profile_metadata import validate_profile_metadata
from app.identity.infrastructure import postgres


def _scope():
    return {
        "tenant_id": "tenant-a",
        "user_id": "W001",
        "display_name": "张三",
    }


@pytest.mark.asyncio
async def test_profile_metadata_merge_is_scoped_locked_and_bounds_final_value(monkeypatch):
    metadata = {"theme": "dark"}
    calls = []

    @asynccontextmanager
    async def fake_transaction():
        yield object()

    async def ensure_submission_principal(_conn, **scope):
        calls.append(("ensure", scope))

    async def lock_user_profile_metadata(_conn, **scope):
        calls.append(("lock", scope))
        return dict(metadata)

    async def update_user_profile_metadata(_conn, *, metadata, **scope):
        calls.append(("update", {**scope, "metadata": metadata}))
        return dict(metadata)

    monkeypatch.setattr(postgres, "ensure_submission_principal", ensure_submission_principal)
    monkeypatch.setattr(postgres, "lock_user_profile_metadata", lock_user_profile_metadata)
    monkeypatch.setattr(postgres, "update_user_profile_metadata", update_user_profile_metadata)
    service = api.ProfileMetadataService(
        postgres.PostgresProfileMetadataStore(fake_transaction)
    )

    result = await service.merge(
        **_scope(),
        patch={"company_navigation_favorite_ids": ["AI:Gemini"]},
    )

    assert result == {
        "theme": "dark",
        "company_navigation_favorite_ids": ["AI:Gemini"],
    }
    assert calls == [
        ("ensure", _scope()),
        ("lock", {"tenant_id": "tenant-a", "user_id": "W001"}),
        (
            "update",
            {
                "tenant_id": "tenant-a",
                "user_id": "W001",
                "metadata": result,
            },
        ),
    ]

    metadata = {"existing": "x" * api.PROFILE_METADATA_MAX_BYTES}
    with pytest.raises(api.ProfileMetadataValidationError, match="profile_metadata_too_large"):
        await service.merge(**_scope(), patch={"theme": "light"})
    assert calls[-1] == ("lock", {"tenant_id": "tenant-a", "user_id": "W001"})


@pytest.mark.parametrize("reserved_key", ["display_name", "source"])
def test_profile_metadata_rejects_principal_projection_keys(reserved_key):
    with pytest.raises(api.ProfileMetadataValidationError, match="profile_metadata_reserved_key"):
        validate_profile_metadata({reserved_key: "spoofed"})


def test_profile_metadata_bounds_company_navigation_favorites():
    with pytest.raises(
        api.ProfileMetadataValidationError,
        match="company_navigation_favorites_invalid",
    ):
        validate_profile_metadata(
            {
                api.COMPANY_NAVIGATION_FAVORITES_KEY: [
                    "内网登录:OA"
                ]
                * (api.COMPANY_NAVIGATION_FAVORITES_MAX_ITEMS + 1)
            }
        )
