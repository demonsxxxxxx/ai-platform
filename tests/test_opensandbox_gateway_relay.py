from services.opensandbox_gateway.gateway import EXPECTED_EXECUTOR_IDENTITY
from services.opensandbox_gateway.relay import RELAY_SOURCE


def test_relay_request_directory_requires_canonical_executor_and_rejects_legacy_uid() -> None:
    executor_uid = EXPECTED_EXECUTOR_IDENTITY.split(":")[0]

    assert f"require_dir(REQ_FD, {executor_uid}, BROKER_GID, 0o2770)" in RELAY_SOURCE
    assert "require_dir(REQ_FD, 1000, BROKER_GID, 0o2770)" not in RELAY_SOURCE
