import importlib.util

import pytest

from app.executors.registry import AdapterRegistry


@pytest.mark.parametrize(
    "module_name",
    [
        "app.executors.fake",
        "app.executors.embedded_poco",
        "app.runtime.embedded_poco_kernel",
    ],
)
def test_production_package_does_not_ship_unsupported_executors(module_name):
    assert importlib.util.find_spec(module_name) is None


@pytest.mark.parametrize("executor_type", ["fake", "embedded-poco-kernel"])
def test_default_registry_rejects_non_production_executor_types(executor_type):
    with pytest.raises(KeyError, match=f"Unknown executor_type: {executor_type}"):
        AdapterRegistry().get(executor_type)
