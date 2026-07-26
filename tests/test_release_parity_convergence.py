import re
import sys
from urllib.error import HTTPError, URLError

import pytest

from tools.release_authority import ReleaseAuthorityError
from tools import release_authority
from tools.release_parity_convergence import (
    bounded_parity_attempt_timeout,
    converge_final_parity,
)


def test_convergence_retries_transient_os_error_then_succeeds():
    attempts: list[int] = []
    sleeps: list[float] = []
    outcomes = [OSError(104, "connection reset from https://private.invalid"), {"verified": True}]

    def collect(_: float):
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

    def collect(_: float):
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

    def collect(_: float):
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
    monotonic_values = iter((10.0, 10.0, 10.0, 11.0))

    with pytest.raises(ReleaseAuthorityError, match="^final parity did not converge$") as exc_info:
        converge_final_parity(
            lambda _: {
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


def test_convergence_rejects_verified_report_arriving_at_deadline():
    monotonic_values = iter((10.0, 10.0, 11.0, 11.0))

    with pytest.raises(ReleaseAuthorityError, match="^final parity did not converge$") as exc_info:
        converge_final_parity(
            lambda _: {"verified": True},
            authority_error_type=ReleaseAuthorityError,
            timeout_seconds=1,
            monotonic=lambda: next(monotonic_values),
            sleep=lambda _: (_ for _ in ()).throw(AssertionError("late result must not sleep")),
        )

    assert exc_info.value.parity_attempts == 1
    assert exc_info.value.parity_last_failure_kind == "attempt-timeout"


def test_convergence_bounds_the_in_flight_collector_to_remaining_budget():
    observed: list[float] = []

    def collect(remaining: float):
        observed.append(remaining)
        assert bounded_parity_attempt_timeout(300) == remaining
        return {"verified": True}

    report = converge_final_parity(
        collect,
        authority_error_type=ReleaseAuthorityError,
        timeout_seconds=5,
        monotonic=lambda: 10.0,
    )

    assert report == {"verified": True}
    assert observed == [5]


def test_convergence_fails_fast_for_http_error():
    failure = HTTPError("https://private.invalid/parity", 503, "unavailable", None, None)
    attempts: list[int] = []

    def collect(_: float):
        attempts.append(1)
        raise failure

    with pytest.raises(HTTPError):
        converge_final_parity(
            collect,
            authority_error_type=ReleaseAuthorityError,
            timeout_seconds=30,
            monotonic=lambda: 10.0,
            sleep=lambda _: (_ for _ in ()).throw(AssertionError("HTTP error must fail fast")),
        )

    assert attempts == [1]


@pytest.mark.parametrize(
    "message",
    ("frontend provenance schema mismatch", "unexpected deterministic parity authority failure"),
)
def test_convergence_fails_fast_for_structural_authority_errors(message):
    attempts: list[int] = []
    sleeps: list[float] = []

    def collect(_: float):
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


def test_auto_final_parity_uses_the_bounded_convergence_collector(monkeypatch, tmp_path):
    commit = "a" * 40
    checkout = tmp_path / commit
    observed: list[float] = []

    monkeypatch.setattr(release_authority, "resolve_managed_env_file", lambda *args: tmp_path / ".env")
    monkeypatch.setattr(release_authority, "materialize_main_checkout", lambda *args: checkout)
    monkeypatch.setattr(release_authority, "assert_managed_target_checkout", lambda *args: commit)
    monkeypatch.setattr(release_authority, "resolve_compose_files", lambda *args: object())
    monkeypatch.setattr(release_authority, "_docker_base", lambda *args: ["docker"])
    monkeypatch.setattr(
        release_authority,
        "_verified_current_runtime",
        lambda *args, **kwargs: {"commit": "b" * 40, "references": {}},
    )
    monkeypatch.setattr(release_authority, "_auto_release_plan", lambda *args: object())
    monkeypatch.setattr(release_authority, "deploy_clean_commit", lambda *args, **kwargs: {})

    def collect(*args, **kwargs):
        observed.append(bounded_parity_attempt_timeout(300))
        return {"verified": True}

    monkeypatch.setattr(release_authority, "collect_live_parity", collect)
    result = release_authority._deploy_main_commit_after_authority(
        tmp_path,
        commit,
        docker_cmd="docker",
        env_file=None,
        replace_known_manual_frontend=False,
        strategy="auto",
    )

    assert result["parity"] == {"verified": True}
    assert len(observed) == 1
    assert 0 < observed[0] <= 45


def test_canonical_deploy_and_verify_each_collect_parity_once(monkeypatch, capsys, tmp_path):
    commit = "c" * 40
    checkout = tmp_path / commit
    calls: list[tuple[object, ...]] = []

    monkeypatch.setattr(release_authority, "resolve_managed_env_file", lambda *args: tmp_path / ".env")
    monkeypatch.setattr(release_authority, "materialize_main_checkout", lambda *args: checkout)
    monkeypatch.setattr(release_authority, "deploy_clean_commit", lambda *args, **kwargs: {})
    monkeypatch.setattr(
        release_authority,
        "collect_live_parity",
        lambda *args, **kwargs: calls.append(args) or {"verified": True},
    )

    release_authority._deploy_main_commit_after_authority(
        tmp_path,
        commit,
        docker_cmd="docker",
        env_file=None,
        replace_known_manual_frontend=False,
        strategy="canonical",
    )
    assert len(calls) == 1

    monkeypatch.setattr(
        sys,
        "argv",
        ["release_authority.py", "verify", "--repo-root", str(checkout), "--commit", commit],
    )
    assert release_authority.main() == 0
    assert len(calls) == 2
    assert capsys.readouterr().out
