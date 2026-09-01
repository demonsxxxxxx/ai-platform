from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass

import pytest

from tools.sse_candidate_gate import (
    DELIVERY_WORKFLOW_PATH,
    REPOSITORY,
    CandidateGateError,
    evaluate_candidate_acceptance,
)


BASE = "a" * 40
HEAD = "b" * 40
AUTHORITY = "c" * 40
BACKEND = (
    "ghcr.io/demonsxxxxxx/ai-platform-backend-candidate@sha256:" + "d" * 64
)
FRONTEND = (
    "ghcr.io/demonsxxxxxx/ai-platform-frontend-candidate@sha256:" + "e" * 64
)
APP = {
    "creator": {
        "login": "github-actions[bot]",
        "type": "Bot",
        "site_admin": False,
    },
    "performed_via_github_app": {"slug": "github-actions"},
}


@dataclass(frozen=True)
class ResponseSequence:
    values: tuple[object, ...]


class FakeClient:
    def __init__(self, responses: Mapping[str, object]) -> None:
        self.responses = dict(responses)
        self.calls: list[str] = []
        self.counts: defaultdict[str, int] = defaultdict(int)

    def get_json(
        self, path: str, *, query: Mapping[str, str] | None = None
    ) -> object:
        self.calls.append(path)
        response = self.responses[path]
        if isinstance(response, ResponseSequence):
            index = self.counts[path]
            self.counts[path] += 1
            return response.values[min(index, len(response.values) - 1)]
        return response


def _pr(*, head_repository: str = REPOSITORY, head: str = HEAD) -> dict[str, object]:
    return {
        "number": 42,
        "state": "open",
        "base": {"ref": "main", "sha": BASE, "repo": {"full_name": REPOSITORY}},
        "head": {"sha": head, "repo": {"full_name": head_repository}},
    }


def _payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "schema": "ai-platform.sse-candidate-deployment.v1",
        "repository": REPOSITORY,
        "pr_number": 42,
        "base_sha": BASE,
        "head_sha": HEAD,
        "authority_commit": AUTHORITY,
        "delivery_run_id": 9001,
        "delivery_run_attempt": 1,
        "backend_image": BACKEND,
        "frontend_image": FRONTEND,
        "configuration_sha256": "f" * 64,
        "evidence_sha256": "1" * 64,
        "smoke_revision": "sse-candidate-v1",
        "release_eligible": False,
    }
    payload.update(overrides)
    return payload


def _deployment(identifier: int, **overrides: object) -> dict[str, object]:
    deployment: dict[str, object] = {
        "id": identifier,
        "sha": HEAD,
        "environment": "sse-candidate",
        "task": "sse-candidate",
        "transient_environment": True,
        "production_environment": False,
        "payload": _payload(),
        **APP,
    }
    deployment.update(overrides)
    return deployment


def _status(identifier: int, state: str = "success") -> dict[str, object]:
    return {
        "id": identifier,
        "state": state,
        "environment": "sse-candidate",
        **APP,
    }


def _run(**overrides: object) -> dict[str, object]:
    run: dict[str, object] = {
        "id": 9001,
        "run_attempt": 1,
        "event": "workflow_dispatch",
        "path": DELIVERY_WORKFLOW_PATH,
        "head_branch": "main",
        "head_sha": AUTHORITY,
        "status": "completed",
        "conclusion": "success",
        "repository": {"full_name": REPOSITORY},
    }
    run.update(overrides)
    return run


def _responses(*, deployments: list[object] | None = None) -> dict[str, object]:
    return {
        f"/repos/{REPOSITORY}/pulls/42": _pr(),
        f"/repos/{REPOSITORY}/deployments": deployments
        if deployments is not None
        else [_deployment(100)],
        f"/repos/{REPOSITORY}/actions/runs/9001": _run(),
        f"/repos/{REPOSITORY}/deployments/100/statuses": [_status(1000)],
    }


def _evaluate(client: FakeClient, *, paths: tuple[str, ...], head_repository: str = REPOSITORY) -> str:
    return evaluate_candidate_acceptance(
        client,
        repository=REPOSITORY,
        pr_number=42,
        base_ref=BASE,
        head_ref=HEAD,
        head_repository=head_repository,
        changed_paths=paths,
    )


def test_unaffected_pr_has_stable_not_applicable_result() -> None:
    client = FakeClient({f"/repos/{REPOSITORY}/pulls/42": _pr()})

    assert _evaluate(client, paths=("docs/README.md",)) == "not_applicable"
    assert client.calls == [
        f"/repos/{REPOSITORY}/pulls/42",
        f"/repos/{REPOSITORY}/pulls/42",
    ]


def test_unaffected_fork_fails_closed_without_privileged_evidence() -> None:
    fork = "other/ai-platform"
    client = FakeClient({f"/repos/{REPOSITORY}/pulls/42": _pr(head_repository=fork)})

    with pytest.raises(CandidateGateError, match="fork_not_admitted"):
        _evaluate(client, paths=("README.md",), head_repository=fork)
    assert all("deployments" not in call for call in client.calls)


def test_affected_fork_fails_closed() -> None:
    fork = "other/ai-platform"
    client = FakeClient({f"/repos/{REPOSITORY}/pulls/42": _pr(head_repository=fork)})

    with pytest.raises(CandidateGateError, match="fork_not_admitted"):
        _evaluate(
            client,
            paths=("app/streaming/api.py",),
            head_repository=fork,
        )


def test_affected_pr_requires_candidate_deployment() -> None:
    client = FakeClient(_responses(deployments=[]))

    with pytest.raises(CandidateGateError, match="candidate_deployment_missing"):
        _evaluate(client, paths=("frontend/web/src/hooks/useAgent.ts",))


def test_exact_latest_candidate_evidence_is_accepted() -> None:
    client = FakeClient(_responses())

    assert _evaluate(client, paths=("app/executors/public_answer_stream.py",)) == "accepted"
    assert client.calls[-1] == f"/repos/{REPOSITORY}/pulls/42"


@pytest.mark.parametrize(
    "path",
    [
        "app/bootstrap/run_lifecycle.py",
        "app/bootstrap/streaming.py",
        "app/executor_reconciler.py",
        "app/main.py",
        "app/executors/claude/capability_policy.py",
        "app/repositories.py",
        "app/routes/runtime_callbacks.py",
        "app/run_projection.py",
        "app/runtime/sandbox/executor_app.py",
        "app/schema.sql",
        "app/worker.py",
        "deploy/ai-platform/docker-compose.yml",
        "frontend/web/nginx.conf.template",
        "frontend/web/src/components/chat/ChatMessage/index.tsx",
        "frontend/web/src/generated/publicRunStreamV4.ts",
        "schemas/public_run_stream.v4.schema.json",
        "tools/generate_sse_v4_contracts.py",
    ],
)
def test_sse_authority_paths_are_affected(path: str) -> None:
    client = FakeClient(_responses())

    assert _evaluate(client, paths=(path,)) == "accepted"


def test_candidate_deployment_must_remain_quarantined() -> None:
    deployment = _deployment(100, transient_environment=False)
    client = FakeClient(_responses(deployments=[deployment]))

    with pytest.raises(CandidateGateError, match="quarantine_invalid"):
        _evaluate(client, paths=("app/streaming/api.py",))


def test_newest_candidate_attempt_controls_acceptance() -> None:
    responses = _responses(deployments=[_deployment(101), _deployment(100)])
    responses[f"/repos/{REPOSITORY}/deployments/101/statuses"] = [
        _status(1001, "failure")
    ]
    client = FakeClient(responses)

    with pytest.raises(CandidateGateError, match="candidate_deployment_not_successful"):
        _evaluate(client, paths=("app/streaming/api.py",))


def test_response_order_controls_newest_evidence_with_opaque_ids() -> None:
    responses = _responses(deployments=[_deployment(50), _deployment(100)])
    responses[f"/repos/{REPOSITORY}/deployments/50/statuses"] = [
        _status(500, "failure"),
        _status(1000),
    ]
    client = FakeClient(responses)

    with pytest.raises(CandidateGateError, match="candidate_deployment_not_successful"):
        _evaluate(client, paths=("app/streaming/api.py",))


def test_newer_deployment_during_evaluation_invalidates_old_evidence() -> None:
    responses = _responses()
    responses[f"/repos/{REPOSITORY}/deployments"] = ResponseSequence(
        ([_deployment(100)], [_deployment(101), _deployment(100)])
    )
    client = FakeClient(responses)

    with pytest.raises(CandidateGateError, match="candidate_deployment_changed"):
        _evaluate(client, paths=("app/streaming/api.py",))


def test_newer_failed_status_during_evaluation_invalidates_success() -> None:
    responses = _responses()
    responses[f"/repos/{REPOSITORY}/deployments/100/statuses"] = ResponseSequence(
        ([_status(1000)], [_status(1001, "failure"), _status(1000)])
    )
    client = FakeClient(responses)

    with pytest.raises(CandidateGateError, match="candidate_deployment_not_successful"):
        _evaluate(client, paths=("app/streaming/api.py",))


def test_malformed_newer_deployment_is_not_ignored() -> None:
    client = FakeClient(
        _responses(deployments=[{"id": 101, "sha": HEAD}, _deployment(100)])
    )

    with pytest.raises(CandidateGateError, match="candidate_deployments_invalid"):
        _evaluate(client, paths=("app/streaming/api.py",))


def test_malformed_newer_status_is_not_ignored() -> None:
    responses = _responses()
    responses[f"/repos/{REPOSITORY}/deployments/100/statuses"] = [
        {"id": 1001, "state": "failure"},
        _status(1000),
    ]
    client = FakeClient(responses)

    with pytest.raises(CandidateGateError, match="candidate_deployment_statuses_invalid"):
        _evaluate(client, paths=("app/streaming/api.py",))


def test_stale_head_after_evidence_is_rejected() -> None:
    responses = _responses()
    responses[f"/repos/{REPOSITORY}/pulls/42"] = ResponseSequence(
        (_pr(), _pr(head="9" * 40))
    )
    client = FakeClient(responses)

    with pytest.raises(CandidateGateError, match="pull_request_stale"):
        _evaluate(client, paths=("app/streaming/api.py",))


@pytest.mark.parametrize(
    ("deployment", "reason"),
    [
        (_deployment(100, payload=_payload(head_sha="9" * 40)), "payload_invalid"),
        (
            _deployment(
                100,
                creator={"login": "maintainer", "type": "User", "site_admin": False},
            ),
            "authority_invalid",
        ),
        (_deployment(100, environment="production"), "deployments_invalid"),
    ],
)
def test_wrong_subject_or_authority_is_rejected(
    deployment: dict[str, object], reason: str
) -> None:
    client = FakeClient(_responses(deployments=[deployment]))

    with pytest.raises(CandidateGateError, match=reason):
        _evaluate(client, paths=("app/streaming/api.py",))


def test_wrong_delivery_workflow_is_rejected() -> None:
    responses = _responses()
    responses[f"/repos/{REPOSITORY}/actions/runs/9001"] = _run(
        path=".github/workflows/other.yml"
    )
    client = FakeClient(responses)

    with pytest.raises(CandidateGateError, match="candidate_delivery_run_invalid"):
        _evaluate(client, paths=("app/streaming/api.py",))


def test_gate_policy_change_requires_governance_migration() -> None:
    client = FakeClient({f"/repos/{REPOSITORY}/pulls/42": _pr()})

    with pytest.raises(CandidateGateError, match="candidate_policy_changed"):
        _evaluate(client, paths=("tools/sse_candidate_gate.py",))
