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
