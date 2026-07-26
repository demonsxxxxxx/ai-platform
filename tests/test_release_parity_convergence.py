import re
from urllib.error import URLError

import pytest

from tools.release_authority import ReleaseAuthorityError
from tools.release_parity_convergence import converge_final_parity


def test_convergence_retries_transient_os_error_then_succeeds():
    attempts: list[int] = []
    sleeps: list[float] = []
    outcomes = [OSError(104, "connection reset from https://private.invalid"), {"verified": True}]

    def collect():
        attempts.append(1)
        outcome = outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    report = converge_final_parity(
        collect,
        authority_error_type=ReleaseAuthorityError,
        timeout_seconds=30,
        poll_interval_seconds=2,
        monotonic=lambda: 100.0,
        sleep=sleeps.append,
    )

    assert report == {"verified": True}
    assert attempts == [1, 1]
    assert sleeps == [2]


def test_convergence_retries_unverified_report_then_succeeds():
    attempts: list[int] = []
    sleeps: list[float] = []
    outcomes = [
        {"verified": False, "mismatches": ["api_runtime_commit_mismatch"]},
        {"verified": True, "mismatches": []},
    ]

    def collect():
        attempts.append(1)
        return outcomes.pop(0)

    report = converge_final_parity(
        collect,
        authority_error_type=ReleaseAuthorityError,
        timeout_seconds=30,
        poll_interval_seconds=3,
        monotonic=lambda: 100.0,
        sleep=sleeps.append,
    )

    assert report == {"verified": True, "mismatches": []}
    assert attempts == [1, 1]
    assert sleeps == [3]


@pytest.mark.parametrize(
    "failure",
    (
        ReleaseAuthorityError("worker runtime heartbeat is stale"),
        URLError("connection reset from https://private.invalid"),
    ),
)
def test_convergence_retries_explicit_startup_readiness_and_url_errors(failure):
    attempts: list[int] = []
    sleeps: list[float] = []
    outcomes = [failure, {"verified": True}]

    def collect():
        attempts.append(1)
        outcome = outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    report = converge_final_parity(
        collect,
        authority_error_type=ReleaseAuthorityError,
        timeout_seconds=30,
        poll_interval_seconds=2,
        monotonic=lambda: 100.0,
        sleep=sleeps.append,
    )

    assert report == {"verified": True}
    assert attempts == [1, 1]
    assert sleeps == [2]


def test_convergence_deadline_is_sanitized_and_bounded():
    monotonic_values = iter((10.0, 11.0))

    with pytest.raises(ReleaseAuthorityError, match="^final parity did not converge$") as exc_info:
        converge_final_parity(
            lambda: {
                "verified": False,
                "mismatches": ["https://private.invalid/live-parity?token=private-marker"],
            },
            authority_error_type=ReleaseAuthorityError,
            timeout_seconds=0.5,
            poll_interval_seconds=2,
            monotonic=lambda: next(monotonic_values),
            sleep=lambda seconds: (_ for _ in ()).throw(AssertionError("must not sleep past deadline")),
        )

    exc = exc_info.value
    assert exc.parity_attempts == 1
    assert exc.parity_last_failure_kind == "unverified-parity"
    assert "private.invalid" not in str(exc)
    assert "private-marker" not in str(exc)


@pytest.mark.parametrize(
    "message",
    ("frontend provenance schema mismatch", "unexpected deterministic parity authority failure"),
)
def test_convergence_fails_fast_for_structural_authority_errors(message):
    attempts: list[int] = []
    sleeps: list[float] = []

    def collect():
        attempts.append(1)
        raise ReleaseAuthorityError(message)

    with pytest.raises(ReleaseAuthorityError, match=f"^{re.escape(message)}$"):
        converge_final_parity(
            collect,
            authority_error_type=ReleaseAuthorityError,
            timeout_seconds=30,
            poll_interval_seconds=2,
            monotonic=lambda: 100.0,
            sleep=sleeps.append,
        )

    assert attempts == [1]
    assert sleeps == []
