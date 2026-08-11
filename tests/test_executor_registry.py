import importlib.util

import pytest

from app.executors.registry import AdapterRegistry


def test_production_executor_package_does_not_ship_test_stubs():
    assert importlib.util.find_spec("app.executors.fake") is None


@pytest.mark.parametrize("executor_type", ["fake", "embedded-poco-kernel"])
def test_default_registry_rejects_non_production_executor_types(executor_type):
    with pytest.raises(KeyError, match=f"Unknown executor_type: {executor_type}"):
        AdapterRegistry().get(executor_type)
