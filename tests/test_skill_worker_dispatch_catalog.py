from types import SimpleNamespace

import pytest

from app.skills.api import (
    materialize_worker_locked_skill_snapshots,
    merged_worker_pinned_manifests,
    worker_catalog_binding,
    worker_catalog_public_metadata,
    worker_payload_with_authorized_catalog,
)
from app.skills.catalog import AuthorizedSkillCatalogError


@pytest.mark.asyncio
async def test_locked_worker_skill_snapshot_conflict_returns_no_manifests():
    async def conflicting_repository(conn, **kwargs):
        assert kwargs["skill_manifest_refs"] == [{"skill_id": "skill"}]
        raise ValueError("invalid_snapshot")

    result = await materialize_worker_locked_skill_snapshots(
        object(), tenant_id="tenant", run_id="run",
        skill_manifest_refs=[{"skill_id": "skill"}],
        materialize=conflicting_repository, conflict_error=ValueError,
    )
    assert result is None


def test_worker_catalog_pin_merge_rejects_mismatched_current_content():
    payload = SimpleNamespace(skill_manifests=[{"skill_id": "skill", "content_hash": "hash-one"}])
    catalog = SimpleNamespace(
        manifests=[{"skill_id": "skill", "content_hash": "hash-two"}],
        snapshot=SimpleNamespace(entries=[SimpleNamespace(
            skill_id="skill", name="Available", version="v2", availability="available",
        )]),
    )
    with pytest.raises(AuthorizedSkillCatalogError, match="authorized_skill_catalog_pin_mismatch"):
        merged_worker_pinned_manifests(payload, catalog)
    assert worker_catalog_public_metadata(catalog) == {
        "skill": {"name": "Available", "version": "v2", "availability": "available"}
    }


def test_worker_catalog_projection_replaces_only_authorized_runtime_input():
    identity = {
        "tenant_id": "tenant", "workspace_id": "workspace", "user_id": "user",
        "session_id": "session", "run_id": "run", "agent_id": "agent", "skill_id": "skill",
    }
    binding = worker_catalog_binding(identity, binding_type=SimpleNamespace)
    assert binding.selected_skill_id == "skill"
    assert binding.run_id == "run"

    class Payload:
        input = {"catalog": "stale", "manifests": "private", "message": "hello"}
        skill_manifests = [{"skill_id": "skill"}]

        def model_copy(self, *, update):
            return update["input"]

    class Resolution:
        def runtime_input_updates(self, *, pinned_manifests):
            assert pinned_manifests == Payload.skill_manifests
            return {"catalog": "authorized"}

    projected = worker_payload_with_authorized_catalog(
        Payload(), resolution=Resolution(),
        catalog_key="catalog", manifests_key="manifests",
    )
    assert projected == {"message": "hello", "catalog": "authorized"}
