import pytest

from app.runtime.sandbox.filesystem_contract import encode_execd_mode


@pytest.mark.parametrize(
    ("mode", "wire_mode"),
    [
        (0o700, 700),
        (0o600, 600),
    ],
)
def test_encode_execd_mode_preserves_private_workspace_permissions(mode, wire_mode):
    assert encode_execd_mode(mode) == wire_mode


@pytest.mark.parametrize("mode", [None, True, "0700", -1, 700, 0o1000])
def test_encode_execd_mode_rejects_malformed_or_out_of_range_values(mode):
    with pytest.raises(ValueError, match="OpenSandbox filesystem mode is invalid"):
        encode_execd_mode(mode)
