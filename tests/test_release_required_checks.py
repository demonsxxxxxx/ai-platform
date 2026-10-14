"""Offline GitHub API regressions for formal Release test qualification."""

from copy import deepcopy
import json
import subprocess
from urllib.parse import parse_qs

import pytest

from tools import release_required_checks as gate

SHA = "a" * 40
ROOT = f"repos/{gate.REPOSITORY}"


class FakeApi:
    def __init__(self):
        self.workflows = {}
        self.runs = {}
        self.jobs = {}
        self.calls = []
        self.before_read = lambda endpoint: None
        for index, (filename, context) in enumerate(gate.REQUIRED_WORKFLOWS.items(), 1):
            workflow_id, run_id = index, index * 100
            self.workflows[filename] = {
                "id": workflow_id, "path": f".github/workflows/{filename}", "state": "active",
            }
            run = {
                "id": run_id, "workflow_id": workflow_id, "path": f".github/workflows/{filename}",
                "head_sha": SHA, "head_branch": "main", "event": "push", "run_attempt": 1,
                "repository": {"full_name": gate.REPOSITORY},
                "head_repository": {"full_name": gate.REPOSITORY},
                "status": "completed", "conclusion": "success", "updated_at": "2026-10-11T12:00:00Z",
            }
            self.runs[workflow_id] = [run]
            self.jobs[run_id] = [{
                "id": run_id + 1, "name": context, "run_id": run_id, "run_attempt": 1,
                "head_sha": SHA, "head_branch": "main", "status": "completed", "conclusion": "success",
                "check_run_url": f"https://api.github.com/{ROOT}/check-runs/{run_id + 1}",
            }]

    def __call__(self, endpoint):
        self.calls.append(endpoint)
        self.before_read(endpoint)
        path, _, query = endpoint.partition("?")
        parts = path.split("/")
        if path.endswith(".yml"):
            return deepcopy(self.workflows[parts[-1]])
        if "/actions/workflows/" in path:
            rows, key = self.runs[int(parts[-2])], "workflow_runs"
        elif path.endswith("/jobs"):
            rows, key = self.jobs[int(parts[-4])], "jobs"
        else:
            return deepcopy(next(run for runs in self.runs.values() for run in runs if run["id"] == int(parts[-1])))
        page = int(parse_qs(query)["page"][0])
        return {"total_count": len(rows), key: deepcopy(rows[(page - 1) * 100:page * 100])}

    def verify(self):
        return gate.verify_required_checks(self, repository=gate.REPOSITORY, sha=SHA)


def test_qualifies_only_both_exact_main_workflow_jobs():
    api = FakeApi()
    evidence = api.verify()
    assert [row["context"] for row in evidence] == list(gate.REQUIRED_WORKFLOWS.values())
    assert all(row["sha"] == SHA and row["run_attempt"] == 1 for row in evidence)
    assert all("trusted" not in call and "/check-runs" not in call for call in api.calls)


@pytest.mark.parametrize(("field", "value"), [
    ("head_sha", "b" * 40), ("head_branch", "feature"),
    ("workflow_id", 99), ("path", ".github/workflows/spoof.yml"),
    ("head_repository", {"full_name": "fork/ai-platform"}),
    ("repository", {"full_name": "fork/ai-platform"}), ("run_attempt", 0), ("updated_at", None), ("updated_at", "bad"),
    ("status", "in_progress"), ("status", "queued"),
    ("conclusion", "failure"), ("conclusion", "cancelled"),
    ("conclusion", "skipped"), ("conclusion", "neutral"),
    ("event", "pull_request"), ("event", "pull_request_target"),
])
def test_wrong_or_unsuccessful_run_cannot_qualify(field, value):
    api = FakeApi()
    api.runs[1][0][field] = value
    with pytest.raises(gate.ReleaseChecksError):
        api.verify()


@pytest.mark.parametrize(("field", "value"), [
    ("head_sha", "b" * 40), ("head_branch", "feature"), ("run_id", 99),
    ("run_attempt", 2), ("conclusion", "cancelled"), ("conclusion", "neutral"),
    ("status", "in_progress"), ("name", "other check"),
    ("check_run_url", "https://example.com/spoof"),
])
def test_job_identity_and_result_are_bound_to_current_attempt(field, value):
    api = FakeApi()
    api.jobs[100][0][field] = value
    with pytest.raises(gate.ReleaseChecksError):
        api.verify()


def test_duplicate_context_cannot_mask_cancelled_job():
    api = FakeApi()
    duplicate = {**api.jobs[100][0], "id": 999, "conclusion": "cancelled"}
    api.jobs[100].append(duplicate)
    with pytest.raises(gate.ReleaseChecksError, match="ambiguous"):
        api.verify()


def test_later_cancelled_run_cannot_fall_back_to_green():
    api = FakeApi()
    api.runs[1].append({**api.runs[1][0], "id": 110, "conclusion": "cancelled"})
    with pytest.raises(gate.ReleaseChecksError, match="latest"):
        api.verify()


def test_older_cancelled_run_does_not_block_later_success():
    api = FakeApi()
    api.runs[1].append({**api.runs[1][0], "id": 90, "conclusion": "cancelled", "updated_at": "2026-10-11T11:00:00Z"})
    assert api.verify()


@pytest.mark.parametrize("status,conclusion", [("in_progress", None), ("completed", "failure")])
def test_newer_retry_of_older_run_cannot_hide_behind_green(status, conclusion):
    api = FakeApi()
    api.runs[1].append({**api.runs[1][0], "id": 90, "run_attempt": 2, "status": status,
                       "conclusion": conclusion, "updated_at": "2026-10-11T13:00:00Z"})
    with pytest.raises(gate.ReleaseChecksError):
        api.verify()


def test_paginates_jobs_to_find_required_context():
    api = FakeApi()
    api.jobs[100][0:0] = [{"id": 1000 + i, "name": f"shard {i}"} for i in range(100)]
    assert api.verify()
    assert any("/jobs?per_page=100&page=2" in call for call in api.calls)


@pytest.mark.parametrize("mutate", ["rerun", "new_run"])
def test_rechecks_inventory_after_both_workflows_are_read(mutate):
    api = FakeApi()
    def change(endpoint):
        if "/actions/runs/200/attempts/" in endpoint:
            if mutate == "rerun":
                api.runs[1][0]["run_attempt"] = 2
            else:
                api.runs[1].append({**api.runs[1][0], "id": 110})
    api.before_read = change
    with pytest.raises(gate.ReleaseChecksError, match="changed"):
        api.verify()


@pytest.mark.parametrize("payload", [
    {"total_count": 1, "jobs": []}, {"total_count": 0, "jobs": [{}]},
    {"total_count": 2, "jobs": [{"id": 1}, {"id": 1}]},
    {"total_count": 1, "jobs": [{"id": True}]},
])
def test_malformed_or_incomplete_pages_fail_closed(payload):
    with pytest.raises(gate.ReleaseChecksError):
        gate._pages(lambda _: payload, "endpoint", "jobs")


def test_missing_run_and_disabled_workflow_fail_closed():
    api = FakeApi()
    api.runs[1] = []
    with pytest.raises(gate.ReleaseChecksError, match="missing"):
        api.verify()
    api = FakeApi()
    api.workflows["ai-platform-backend.yml"]["state"] = "disabled_manually"
    with pytest.raises(gate.ReleaseChecksError, match="identity"):
        api.verify()


@pytest.mark.parametrize("repository,sha", [("fork/repo", SHA), (gate.REPOSITORY, "main")])
def test_rejects_noncanonical_publication_identity(repository, sha):
    with pytest.raises(gate.ReleaseChecksError):
        gate.verify_required_checks(FakeApi(), repository=repository, sha=sha)


def test_api_errors_do_not_expose_cli_output(monkeypatch):
    def fail(*args, **kwargs):
        raise subprocess.CalledProcessError(1, ["gh"], stderr="synthetic-secret")
    monkeypatch.setattr(subprocess, "run", fail)
    with pytest.raises(gate.ReleaseChecksError, match="evidence unavailable") as error:
        gate.github_api("/pinned/gh", "repos/example")
    assert "synthetic-secret" not in str(error.value)


def test_api_client_is_read_only_host_fixed_and_bounded(monkeypatch):
    def run(command, **kwargs):
        assert command == ["/pinned/gh", "api", "--hostname", "github.com", "repos/example"]
        assert kwargs["timeout"] == 30 and kwargs["check"] is True
        return subprocess.CompletedProcess(command, 0, json.dumps({"id": 1}))
    monkeypatch.setattr(subprocess, "run", run)
    assert gate.github_api("/pinned/gh", "repos/example") == {"id": 1}
