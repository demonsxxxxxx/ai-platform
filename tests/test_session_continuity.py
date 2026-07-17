from uuid import UUID

import pytest

from app.session_continuity import sdk_session_id_for_run


def test_sdk_session_id_is_stable_for_one_run_and_reconstructable_after_worker_restart():
    first_worker_id = sdk_session_id_for_run("run-a")
    restarted_worker_id = sdk_session_id_for_run("run-a")

    assert first_worker_id == restarted_worker_id
    UUID(first_worker_id)


def test_sdk_session_id_is_distinct_between_runs_even_for_one_platform_session():
    assert sdk_session_id_for_run("run-a") != sdk_session_id_for_run("run-b")


def test_sdk_session_id_requires_an_immutable_run_identity():
    with pytest.raises(ValueError, match="run_id_required_for_sdk_session"):
        sdk_session_id_for_run("")
