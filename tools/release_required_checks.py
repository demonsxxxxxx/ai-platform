"""Qualify a formal Release using exact-main GitHub Actions test evidence."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
from collections.abc import Callable
from typing import Any

REPOSITORY = "demonsxxxxxx/ai-platform"
REQUIRED_WORKFLOWS = {
    "ai-platform-backend.yml": "backend required",
    "ai-platform-frontend.yml": "frontend required",
}
Api = Callable[[str], dict[str, Any]]


class ReleaseChecksError(ValueError):
    """Required publication evidence is missing, ambiguous, or unsuccessful."""


def _require(condition: bool, reason: str) -> None:
    if not condition:
        raise ReleaseChecksError(reason)


def _positive(value: Any) -> bool:
    return type(value) is int and value > 0


def _pages(api: Api, endpoint: str, key: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    separator = "&" if "?" in endpoint else "?"
    total = None
    for page in range(1, 101):
        payload = api(f"{endpoint}{separator}per_page=100&page={page}")
        count = payload.get("total_count")
        batch = payload.get(key)
        _require(type(count) is int and 0 <= count <= 10000, "invalid evidence count")
        _require(isinstance(batch, list), "invalid evidence page")
        _require(all(isinstance(row, dict) for row in batch), "invalid evidence record")
        if total is None:
            total = count
        _require(count == total, "evidence changed during pagination")
        rows.extend(batch)
        _require(len(rows) <= total, "inconsistent evidence pages")
        if len(rows) == total:
            ids = [row.get("id") for row in rows]
            _require(all(_positive(value) for value in ids), "invalid evidence identity")
            _require(len(set(ids)) == len(ids), "duplicate evidence identity")
            return rows
        _require(bool(batch), "incomplete evidence pages")
    raise ReleaseChecksError("evidence pagination limit exceeded")


def _run_identity(run: dict[str, Any], workflow_id: int, filename: str, sha: str) -> None:
    _require(
        run.get("workflow_id") == workflow_id
        and run.get("path") == f".github/workflows/{filename}"
        and run.get("head_sha") == sha
        and run.get("head_branch") == "main"
        and run.get("event") in {"push", "workflow_dispatch"}
        and run.get("repository", {}).get("full_name") == REPOSITORY
        and run.get("head_repository", {}).get("full_name") == REPOSITORY
        and _positive(run.get("id"))
        and _positive(run.get("run_attempt"))
        and isinstance(run.get("updated_at"), str)
        and re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z", run["updated_at"]) is not None,
        "workflow source identity mismatch",
    )


def _snapshot(run: dict[str, Any]) -> tuple[Any, ...]:
    return tuple(run.get(key) for key in (
        "id", "run_attempt", "status", "conclusion", "updated_at",
    ))


def _success(record: dict[str, Any]) -> bool:
    return record.get("status") == "completed" and record.get("conclusion") == "success"


def verify_required_checks(api: Api, *, repository: str, sha: str) -> list[dict[str, Any]]:
    _require(repository == REPOSITORY, "publication repository mismatch")
    _require(re.fullmatch(r"[0-9a-f]{40}", sha) is not None, "invalid publication SHA")
    root = f"repos/{REPOSITORY}"
    evidence = []
    observations = []
    for filename, context in REQUIRED_WORKFLOWS.items():
        workflow = api(f"{root}/actions/workflows/{filename}")
        workflow_id = workflow.get("id")
        _require(
            _positive(workflow_id)
            and workflow.get("path") == f".github/workflows/{filename}"
            and workflow.get("state") == "active",
            "required workflow identity unavailable",
        )
        endpoint = f"{root}/actions/workflows/{workflow_id}/runs?head_sha={sha}&branch=main"
        runs = _pages(api, endpoint, "workflow_runs")
        # PR/fork results cannot qualify a main publication, even for matching code.
        runs = [run for run in runs if run.get("event") in {"push", "workflow_dispatch"}]
        _require(bool(runs), f"missing exact-main evidence: {context}")
        for run in runs:
            _run_identity(run, workflow_id, filename, sha)
            _require(run.get("status") == "completed", f"unfinished required workflow: {context}")
        latest = max(runs, key=lambda run: run["id"])
        _require(_success(latest), f"latest required workflow did not succeed: {context}")
        # A newer attempt of an older run must not hide behind a later green run.
        for run in runs:
            if run.get("updated_at", "") >= latest.get("updated_at", ""):
                _require(_success(run), f"newer unsuccessful evidence: {context}")
        run_id = latest["id"]
        detail = api(f"{root}/actions/runs/{run_id}")
        _run_identity(detail, workflow_id, filename, sha)
        _require(_snapshot(detail) == _snapshot(latest), "run changed while reading evidence")
        attempt = detail["run_attempt"]
        jobs = _pages(api, f"{root}/actions/runs/{run_id}/attempts/{attempt}/jobs", "jobs")
        matches = [job for job in jobs if job.get("name") == context]
        _require(len(matches) == 1, f"missing or ambiguous required job: {context}")
        job = matches[0]
        _require(
            job.get("run_id") == run_id
            and job.get("run_attempt") == attempt
            and job.get("head_sha") == sha
            and job.get("head_branch") == "main"
            and job.get("check_run_url") == f"https://api.github.com/{root}/check-runs/{job['id']}"
            and _success(job),
            f"required job identity or conclusion invalid: {context}",
        )
        evidence.append({"context": context, "sha": sha, "run_id": run_id,
                         "run_attempt": attempt, "job_id": job["id"]})
        observations.append((endpoint, runs, filename, workflow_id))
    # Re-read both inventories after all job reads: stale greens cannot mask reruns
    # or newly created runs during this check. Publication still rechecks main.
    for endpoint, previous, filename, workflow_id in observations:
        current = _pages(api, endpoint, "workflow_runs")
        current = [run for run in current if run.get("event") in {"push", "workflow_dispatch"}]
        for run in current:
            _run_identity(run, workflow_id, filename, sha)
        _require(
            sorted(_snapshot(run) for run in current) == sorted(_snapshot(run) for run in previous),
            "required workflow evidence changed during qualification",
        )
    return evidence


def github_api(cli: str, endpoint: str) -> dict[str, Any]:
    try:
        completed = subprocess.run(
            [cli, "api", "--hostname", "github.com", endpoint],
            check=True, capture_output=True, text=True, timeout=30,
        )
        payload = json.loads(completed.stdout)
    except (OSError, subprocess.SubprocessError, ValueError) as error:
        # Never print CLI output: authentication and server errors may contain secrets.
        raise ReleaseChecksError("GitHub Actions evidence unavailable") from error
    _require(isinstance(payload, dict), "invalid GitHub API response")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--source-commit", required=True)
    args = parser.parse_args()
    try:
        cli = os.environ.get("GH_CLI_BIN", "")
        _require(bool(cli) and bool(os.environ.get("GH_TOKEN")), "GitHub evidence client unavailable")
        evidence = verify_required_checks(
            lambda endpoint: github_api(cli, endpoint),
            repository=args.repository, sha=args.source_commit,
        )
    except ReleaseChecksError as error:
        print(f"release_required_checks=blocked ({error})")
        return 1
    except (TypeError, AttributeError, KeyError):
        print("release_required_checks=blocked (malformed GitHub evidence)")
        return 1
    print(json.dumps({"release_required_checks": "success", "evidence": evidence}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
