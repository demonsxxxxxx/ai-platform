import importlib.util
import subprocess
import sys

import pytest

import app.runtime as runtime_package
from app.executors.registry import AdapterRegistry


def test_production_executor_package_does_not_ship_test_stubs():
    assert importlib.util.find_spec("app.executors.fake") is None


def test_runtime_facade_does_not_import_or_advertise_experimental_kernel():
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys; import app.runtime; "
                "assert 'app.runtime.embedded_poco_kernel' not in sys.modules"
            ),
        ],
        capture_output=True,
        check=False,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr or completed.stdout
    assert "InProcessEmbeddedPocoKernel" not in runtime_package.__all__
    assert not hasattr(runtime_package, "InProcessEmbeddedPocoKernel")
    assert importlib.util.find_spec("app.runtime.embedded_poco_kernel") is not None


@pytest.mark.parametrize("executor_type", ["fake", "embedded-poco-kernel"])
def test_default_registry_rejects_non_production_executor_types(executor_type):
    with pytest.raises(KeyError, match=f"Unknown executor_type: {executor_type}"):
        AdapterRegistry().get(executor_type)
