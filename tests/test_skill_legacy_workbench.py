import app.skills.infrastructure.legacy_workbench as _owner_skills_infrastructure_legacy_workbench
import pytest

from app.platform.postgres.errors import RepositoryNotFoundError
from app.skills.infrastructure import legacy_workbench


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("function", "error_code"),
    [
        (
            legacy_workbench.list_workbench_capabilities,
            "workbench_capability_catalog_retired",
        ),
        (legacy_workbench.list_workbench_skills, "workbench_skill_catalog_retired"),
    ],
)
async def test_legacy_workbench_catalogs_are_retired(function, error_code):
    with pytest.raises(RepositoryNotFoundError, match=error_code):
        await function(object(), tenant_id="tenant-a")


def test_repository_legacy_workbench_symbols_are_identity_bridges():
    assert (
        _owner_skills_infrastructure_legacy_workbench.list_workbench_capabilities
        is legacy_workbench.list_workbench_capabilities
    )
    assert _owner_skills_infrastructure_legacy_workbench.list_workbench_skills is legacy_workbench.list_workbench_skills
