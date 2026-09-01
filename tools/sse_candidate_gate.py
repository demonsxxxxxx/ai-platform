from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping, Sequence
from typing import Protocol


REPOSITORY = "demonsxxxxxx/ai-platform"
API_URL = "https://api.github.com"
ENVIRONMENT = "sse-candidate"
TASK = "sse-candidate"
DELIVERY_WORKFLOW_PATH = ".github/workflows/ai-platform-sse-candidate-delivery.yml"
_GATE_POLICY_PATHS = frozenset(
    {
        ".github/workflows/ai-platform-trusted-governance-v2.yml",
        ".github/workflows/ai-platform-sse-candidate-delivery.yml",
        "tools/sse_candidate_gate.py",
        "tools/trusted_governance.py",
    }
)
_SSE_PATHS = frozenset(
    {
        "frontend/web/nginx.conf.template",
        "tools/generate_sse_v4_contracts.py",
    }
)
_SSE_PREFIXES = (
    "app/",
    "deploy/ai-platform/",
    "frontend/web/src/",
    "schemas/",
    "tests/test_claude_agent",
    "tests/test_lambchat_frontend_compat.py",
    "tests/test_public_answer_stream.py",
    "tests/test_streaming",
)
_SHA = re.compile(r"[0-9a-f]{40}")
_DIGEST = re.compile(r"[0-9a-f]{64}")
_BACKEND_IMAGE = re.compile(
    r"ghcr\.io/demonsxxxxxx/ai-platform-backend-candidate@sha256:[0-9a-f]{64}"
)
_FRONTEND_IMAGE = re.compile(
    r"ghcr\.io/demonsxxxxxx/ai-platform-frontend-candidate@sha256:[0-9a-f]{64}"
)
_PAYLOAD_KEYS = {
    "schema",
    "repository",
    "pr_number",
    "base_sha",
    "head_sha",
    "authority_commit",
    "delivery_run_id",
    "delivery_run_attempt",
    "backend_image",
    "frontend_image",
    "configuration_sha256",
    "evidence_sha256",
    "smoke_revision",
    "release_eligible",
}


class CandidateGateError(RuntimeError):
    pass


class GitHubAPI(Protocol):
    def get_json(
        self, path: str, *, query: Mapping[str, str] | None = None
    ) -> object: ...


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self,
        _request: urllib.request.Request,
        _file_pointer: object,
        _code: int,
        _message: str,
        _headers: object,
        _new_url: str,
    ) -> None:
        return None


class GitHubClient:
    def __init__(self, token: str) -> None:
        if not token:
            raise CandidateGateError("github_token_missing")
        self._token = token
        self._opener = urllib.request.build_opener(_NoRedirect)

    def get_json(
        self, path: str, *, query: Mapping[str, str] | None = None
    ) -> object:
        if not path.startswith(f"/repos/{REPOSITORY}/") or ".." in path:
            raise CandidateGateError("github_api_path_invalid")
        url = API_URL + path
        if query:
            url += "?" + urllib.parse.urlencode(query)
        request = urllib.request.Request(
            url,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {self._token}",
                "User-Agent": "ai-platform-sse-candidate-gate/1",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        try:
            with self._opener.open(request, timeout=30) as response:
                if response.geturl() != url or response.status != 200:
                    raise CandidateGateError("github_api_unavailable")
                body = response.read(1_048_577)
        except (OSError, urllib.error.URLError, TimeoutError) as error:
            raise CandidateGateError("github_api_unavailable") from error
        if len(body) > 1_048_576:
            raise CandidateGateError("github_api_response_too_large")
        try:
            return json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise CandidateGateError("github_api_response_invalid") from error


def _mapping(value: object, reason: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise CandidateGateError(reason)
    return value


def _items(value: object, reason: str) -> list[object]:
    if not isinstance(value, list):
        raise CandidateGateError(reason)
    return value


def _positive_int(value: object, reason: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise CandidateGateError(reason)
    return value


def _app_owned(value: Mapping[str, object], reason: str) -> None:
    creator = _mapping(value.get("creator"), reason)
    app = _mapping(value.get("performed_via_github_app"), reason)
    if (
        creator.get("login") != "github-actions[bot]"
        or creator.get("type") != "Bot"
        or creator.get("site_admin") is not False
        or app.get("slug") != "github-actions"
    ):
        raise CandidateGateError(reason)


def _validate_pr(
    payload: object,
    *,
    pr_number: int,
    base_ref: str,
    head_ref: str,
    head_repository: str,
) -> None:
    pr = _mapping(payload, "pull_request_invalid")
    base = _mapping(pr.get("base"), "pull_request_invalid")
    head = _mapping(pr.get("head"), "pull_request_invalid")
    base_repo = _mapping(base.get("repo"), "pull_request_invalid")
    head_repo = _mapping(head.get("repo"), "pull_request_invalid")
    if (
        pr.get("number") != pr_number
        or pr.get("state") != "open"
        or base.get("ref") != "main"
        or base.get("sha") != base_ref
        or base_repo.get("full_name") != REPOSITORY
        or head.get("sha") != head_ref
        or head_repo.get("full_name") != head_repository
    ):
        raise CandidateGateError("pull_request_stale")


def _classify(changed_paths: Sequence[str]) -> str:
    if not changed_paths or any(not path or "\x00" in path for path in changed_paths):
        raise CandidateGateError("candidate_scope_invalid")
    if any(path in _GATE_POLICY_PATHS for path in changed_paths):
        raise CandidateGateError("candidate_policy_changed")
    if any(
        path in _SSE_PATHS or any(path.startswith(prefix) for prefix in _SSE_PREFIXES)
        for path in changed_paths
    ):
        return "affected"
    return "not_applicable"


def _validate_payload(
    value: object, *, pr_number: int, base_ref: str, head_ref: str
) -> Mapping[str, object]:
    payload = _mapping(value, "candidate_deployment_payload_invalid")
    if set(payload) != _PAYLOAD_KEYS:
        raise CandidateGateError("candidate_deployment_payload_invalid")
    if (
        payload.get("schema") != "ai-platform.sse-candidate-deployment.v1"
        or payload.get("repository") != REPOSITORY
        or payload.get("pr_number") != pr_number
        or payload.get("base_sha") != base_ref
        or payload.get("head_sha") != head_ref
        or not isinstance(payload.get("authority_commit"), str)
        or _SHA.fullmatch(payload["authority_commit"]) is None
        or not isinstance(payload.get("backend_image"), str)
        or _BACKEND_IMAGE.fullmatch(payload["backend_image"]) is None
        or not isinstance(payload.get("frontend_image"), str)
        or _FRONTEND_IMAGE.fullmatch(payload["frontend_image"]) is None
        or payload["backend_image"] == payload["frontend_image"]
        or not isinstance(payload.get("configuration_sha256"), str)
        or _DIGEST.fullmatch(payload["configuration_sha256"]) is None
        or not isinstance(payload.get("evidence_sha256"), str)
        or _DIGEST.fullmatch(payload["evidence_sha256"]) is None
        or payload.get("smoke_revision") != "sse-candidate-v1"
        or payload.get("release_eligible") is not False
    ):
        raise CandidateGateError("candidate_deployment_payload_invalid")
    _positive_int(payload.get("delivery_run_id"), "candidate_deployment_payload_invalid")
    _positive_int(
        payload.get("delivery_run_attempt"), "candidate_deployment_payload_invalid"
    )
    return payload


def _latest_deployment(client: GitHubAPI, *, head_ref: str) -> Mapping[str, object]:
    values = _items(
        client.get_json(
            f"/repos/{REPOSITORY}/deployments",
            query={
                "sha": head_ref,
                "environment": ENVIRONMENT,
                "task": TASK,
                "per_page": "100",
            },
        ),
        "candidate_deployments_invalid",
    )
    deployments: list[Mapping[str, object]] = []
    identifiers: set[int] = set()
    for value in values:
        deployment = _mapping(value, "candidate_deployments_invalid")
        identifier = _positive_int(
            deployment.get("id"), "candidate_deployments_invalid"
        )
        if (
            identifier in identifiers
            or deployment.get("sha") != head_ref
            or deployment.get("environment") != ENVIRONMENT
            or deployment.get("task") != TASK
        ):
            raise CandidateGateError("candidate_deployments_invalid")
        identifiers.add(identifier)
        deployments.append(deployment)
    if not deployments:
        raise CandidateGateError("candidate_deployment_missing")
    return deployments[0]


def _latest_status(
    client: GitHubAPI, *, deployment_id: int
) -> Mapping[str, object]:
    values = _items(
        client.get_json(
            f"/repos/{REPOSITORY}/deployments/{deployment_id}/statuses",
            query={"per_page": "100"},
        ),
        "candidate_deployment_statuses_invalid",
    )
    statuses: list[Mapping[str, object]] = []
    identifiers: set[int] = set()
    for value in values:
        status = _mapping(value, "candidate_deployment_statuses_invalid")
        identifier = _positive_int(
            status.get("id"), "candidate_deployment_statuses_invalid"
        )
        if (
            identifier in identifiers
            or not isinstance(status.get("state"), str)
            or status.get("environment") != ENVIRONMENT
        ):
            raise CandidateGateError("candidate_deployment_statuses_invalid")
        identifiers.add(identifier)
        statuses.append(status)
    if not statuses:
        raise CandidateGateError("candidate_deployment_status_missing")
    return statuses[0]


def _validate_success_status(status: Mapping[str, object]) -> None:
    _app_owned(status, "candidate_deployment_status_authority_invalid")
    if status.get("state") != "success":
        raise CandidateGateError("candidate_deployment_not_successful")


def evaluate_candidate_acceptance(
    client: GitHubAPI,
    *,
    repository: str,
    pr_number: int,
    base_ref: str,
    head_ref: str,
    head_repository: str,
    changed_paths: Sequence[str],
) -> str:
    if (
        repository != REPOSITORY
        or pr_number < 1
        or _SHA.fullmatch(base_ref) is None
        or _SHA.fullmatch(head_ref) is None
        or not head_repository
    ):
        raise CandidateGateError("candidate_subject_invalid")

    pr_path = f"/repos/{REPOSITORY}/pulls/{pr_number}"
    _validate_pr(
        client.get_json(pr_path),
        pr_number=pr_number,
        base_ref=base_ref,
        head_ref=head_ref,
        head_repository=head_repository,
    )
    if head_repository != REPOSITORY:
        raise CandidateGateError("fork_not_admitted")
    disposition = _classify(changed_paths)
    if disposition == "not_applicable":
        _validate_pr(
            client.get_json(pr_path),
            pr_number=pr_number,
            base_ref=base_ref,
            head_ref=head_ref,
            head_repository=head_repository,
        )
        return disposition

    deployment = _latest_deployment(client, head_ref=head_ref)
    deployment_id = _positive_int(
        deployment.get("id"), "candidate_deployments_invalid"
    )
    _app_owned(deployment, "candidate_deployment_authority_invalid")
    if (
        deployment.get("transient_environment") is not True
        or deployment.get("production_environment") is not False
    ):
        raise CandidateGateError("candidate_deployment_quarantine_invalid")
    payload = _validate_payload(
        deployment.get("payload"),
        pr_number=pr_number,
        base_ref=base_ref,
        head_ref=head_ref,
    )

    run_id = _positive_int(
        payload.get("delivery_run_id"), "candidate_deployment_payload_invalid"
    )
    run = _mapping(
        client.get_json(f"/repos/{REPOSITORY}/actions/runs/{run_id}"),
        "candidate_delivery_run_invalid",
    )
    run_repo = _mapping(run.get("repository"), "candidate_delivery_run_invalid")
    if (
        run.get("id") != run_id
        or run.get("run_attempt") != payload["delivery_run_attempt"]
        or run.get("event") != "workflow_dispatch"
        or run.get("path") != DELIVERY_WORKFLOW_PATH
        or run.get("head_branch") != "main"
        or run.get("head_sha") != payload["authority_commit"]
        or run.get("status") != "completed"
        or run.get("conclusion") != "success"
        or run_repo.get("full_name") != REPOSITORY
    ):
        raise CandidateGateError("candidate_delivery_run_invalid")

    _validate_success_status(
        _latest_status(client, deployment_id=deployment_id)
    )

    latest_deployment = _latest_deployment(client, head_ref=head_ref)
    if latest_deployment.get("id") != deployment_id:
        raise CandidateGateError("candidate_deployment_changed")
    _validate_success_status(
        _latest_status(client, deployment_id=deployment_id)
    )

    _validate_pr(
        client.get_json(pr_path),
        pr_number=pr_number,
        base_ref=base_ref,
        head_ref=head_ref,
        head_repository=head_repository,
    )
    return "accepted"


def _changed_paths(root: str, base_ref: str, head_ref: str) -> tuple[str, ...]:
    completed = subprocess.run(
        [
            "git",
            "-C",
            root,
            "diff",
            "--name-only",
            "--no-renames",
            "-z",
            base_ref,
            head_ref,
        ],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    if completed.returncode != 0:
        raise CandidateGateError("candidate_scope_unavailable")
    try:
        return tuple(path.decode("utf-8") for path in completed.stdout.split(b"\0") if path)
    except UnicodeDecodeError as error:
        raise CandidateGateError("candidate_scope_invalid") from error


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Verify exact-head SSE candidate evidence.")
    parser.add_argument("--head-root", required=True)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--pr-number", required=True, type=int)
    parser.add_argument("--base-ref", required=True)
    parser.add_argument("--head-ref", required=True)
    parser.add_argument("--head-repository", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    token = os.environ.pop("GOVERNANCE_API_TOKEN", "")
    try:
        result = evaluate_candidate_acceptance(
            GitHubClient(token),
            repository=args.repository,
            pr_number=args.pr_number,
            base_ref=args.base_ref,
            head_ref=args.head_ref,
            head_repository=args.head_repository,
            changed_paths=_changed_paths(args.head_root, args.base_ref, args.head_ref),
        )
    except CandidateGateError as error:
        print(f"sse_candidate_acceptance=failed reason={error}")
        return 2
    except Exception:
        print("sse_candidate_acceptance=failed reason=internal_error")
        return 2
    print(f"sse_candidate_acceptance=passed disposition={result}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
