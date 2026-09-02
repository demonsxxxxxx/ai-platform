from __future__ import annotations

from contextlib import nullcontext
import hashlib
import io
import json
from pathlib import Path
import stat
import subprocess
import time
from urllib.error import HTTPError
from urllib.request import Request
import zipfile

import pytest

from tools import latest_main_quickstart as latest
from tools import sandbox_quickstart


COMMIT = "1" * 40
OLD_COMMIT = "2" * 40
BACKEND = sandbox_quickstart.BACKEND_REPOSITORY + "@sha256:" + "3" * 64
FRONTEND = sandbox_quickstart.FRONTEND_REPOSITORY + "@sha256:" + "4" * 64
OLD_BACKEND = sandbox_quickstart.BACKEND_REPOSITORY + "@sha256:" + "5" * 64
OLD_FRONTEND = sandbox_quickstart.FRONTEND_REPOSITORY + "@sha256:" + "6" * 64
RUN_IDS = {
    latest.BACKEND_WORKFLOW.file_name: 101,
    latest.FRONTEND_WORKFLOW.file_name: 102,
    latest.PACKAGING_WORKFLOW.file_name: 103,
}


class StepClock:
    def __init__(self, advance_after: int) -> None:
        self.calls = 0
        self.advance_after = advance_after

    def __call__(self) -> float:
        self.calls += 1
        return 0.0 if self.calls <= self.advance_after else 1.0


def _manifest(*, run_id: int = 103, run_attempt: int = 1) -> dict[str, object]:
    return {
        "schema_version": "ai-platform.release-image-manifest.v1",
        "source_commit": COMMIT,
        "repository": latest.REPOSITORY_URL,
        "workflow": {
            "repository": latest.REPOSITORY,
            "workflow_ref": latest.MANIFEST_WORKFLOW_REF,
            "run_id": str(run_id),
            "run_attempt": run_attempt,
            "head_sha": COMMIT,
        },
        "subjects": [
            {"role": "backend", "image": {"immutable_ref": BACKEND}},
            {"role": "frontend", "image": {"immutable_ref": FRONTEND}},
        ],
    }


def _archive_bytes(manifest: dict[str, object] | None = None) -> bytes:
    target = io.BytesIO()
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as bundle:
        bundle.writestr(
            latest.MANIFEST_NAME,
            json.dumps(manifest or _manifest()),
        )
    return target.getvalue()


def _managed_root(tmp_path: Path) -> tuple[Path, Path, Path]:
    root = tmp_path / "managed"
    incoming = root / "incoming"
    env_file = root / "config" / "stable" / ".env"
    incoming.mkdir(parents=True, mode=0o700)
    env_file.parent.mkdir(parents=True)
    env_file.write_text("SECRET=value\n", encoding="utf-8")
    env_file.chmod(0o600)
    subject = incoming / "latest-main.json"
    subject.write_text(
        json.dumps(
            {
                "source_commit": OLD_COMMIT,
                "backend_image": OLD_BACKEND,
                "frontend_image": OLD_FRONTEND,
                "env_file": str(env_file),
                "ci_success": True,
            }
        ),
        encoding="utf-8",
    )
    subject.chmod(0o600)
    return root, env_file, subject


class ApprovedClient:
    def __init__(self, *, archive: bytes | None = None) -> None:
        self.archive = archive or _archive_bytes()
        self.calls: list[tuple[str, dict[str, str]]] = []
        self.downloads: list[str] = []
        self.job_changes: dict[str, object] = {}
        self.run_changes: dict[str, object] = {}
        self.asset_changes: dict[str, object] = {}
        self.main_commits = [COMMIT]

    def get_json(
        self,
        path: str,
        *,
        query: dict[str, str] | None = None,
        timeout_seconds: float | None = None,
    ) -> object:
        assert timeout_seconds is None or timeout_seconds > 0
        self.calls.append((path, query or {}))
        if path.endswith("/git/ref/heads/main"):
            commit = (
                self.main_commits.pop(0)
                if len(self.main_commits) > 1
                else self.main_commits[0]
            )
            return {"ref": latest.MAIN_REF, "object": {"type": "commit", "sha": commit}}
        if path.endswith("/actions/runs"):
            runs = []
            for spec in latest.WORKFLOWS:
                runs.append(
                    {
                        "id": RUN_IDS[spec.file_name],
                        "run_attempt": 1,
                        "head_sha": COMMIT,
                        "head_branch": "main",
                        "event": "push",
                        "path": spec.path,
                        "status": "completed",
                        "conclusion": "success",
                        **self.run_changes,
                    }
                )
            return {"total_count": len(runs), "workflow_runs": runs}
        if path.endswith("/jobs"):
            run_id = int(path.split("/actions/runs/", 1)[1].split("/jobs", 1)[0])
            spec = next(
                item for item in latest.WORKFLOWS if RUN_IDS[item.file_name] == run_id
            )
            job = {
                "name": spec.required_job,
                "head_sha": COMMIT,
                "run_attempt": 1,
                "status": "completed",
                "conclusion": "success",
                **self.job_changes,
            }
            return {"total_count": 1, "jobs": [job]}
        if path.endswith(
            f"/releases/tags/{latest.PUBLIC_EVIDENCE_RELEASE_TAG}"
        ):
            label = f"release-image-evidence-{COMMIT}-103-1"
            return {
                "tag_name": latest.PUBLIC_EVIDENCE_RELEASE_TAG,
                "draft": False,
                "prerelease": True,
                "assets": [
                    {
                        "name": latest.PUBLIC_EVIDENCE_ASSET_NAME,
                        "label": label,
                        "size": len(self.archive),
                        "state": "uploaded",
                        "digest": "sha256:"
                        + hashlib.sha256(self.archive).hexdigest(),
                        "browser_download_url": latest.PUBLIC_EVIDENCE_ASSET_URL,
                        "uploader": {"login": latest.PUBLIC_EVIDENCE_UPLOADER},
                        **self.asset_changes,
                    }
                ],
            }
        raise AssertionError(path)

    def download_public_asset(self, url: str, destination: Path) -> str:
        self.downloads.append(url)
        destination.write_bytes(self.archive)
        destination.chmod(0o600)
        return hashlib.sha256(self.archive).hexdigest()


def _candidate() -> latest.ReleaseCandidate:
    runs = {
        spec.file_name: latest.WorkflowRun(spec, RUN_IDS[spec.file_name], 1, COMMIT)
        for spec in latest.WORKFLOWS
    }
    return latest.ReleaseCandidate(COMMIT, runs)


def test_wait_requires_three_exact_sha_workflows_and_final_jobs() -> None:
    client = ApprovedClient()

    candidate = latest.wait_for_release_candidate(client, timeout_seconds=1)

    assert candidate.source_commit == COMMIT
    assert set(candidate.runs) == {spec.file_name for spec in latest.WORKFLOWS}
    workflow_calls = [
        call for call in client.calls if call[0].endswith("/actions/runs")
    ]
    assert len(workflow_calls) == 1
    assert workflow_calls[0][1] == {
        "branch": "main",
        "event": "push",
        "head_sha": COMMIT,
        "per_page": "100",
    }
    assert len([call for call in client.calls if call[0].endswith("/jobs")]) == 3


@pytest.mark.parametrize("conclusion", ["failure", "cancelled", "skipped"])
def test_failed_workflow_blocks_release(conclusion: str) -> None:
    client = ApprovedClient()
    client.run_changes = {"conclusion": conclusion}

    with pytest.raises(latest.LatestMainError, match="failed for current main"):
        latest.wait_for_release_candidate(client, timeout_seconds=1)


def test_missing_exact_sha_workflow_times_out_without_final_job_admission() -> None:
    client = ApprovedClient()
    client.run_changes = {"head_sha": OLD_COMMIT}
    clock = StepClock(advance_after=5)

    with pytest.raises(latest.LatestMainError, match="did not finish successfully"):
        latest.wait_for_release_candidate(
            client,
            timeout_seconds=1,
            monotonic=clock,
            sleep=lambda _seconds: None,
        )

    assert not any(path.endswith("/jobs") for path, _query in client.calls)


def test_required_final_job_must_match_run_attempt_and_succeed() -> None:
    client = ApprovedClient()
    client.job_changes = {"run_attempt": 2}

    with pytest.raises(
        latest.LatestMainError, match="required final job did not succeed"
    ):
        latest.wait_for_release_candidate(client, timeout_seconds=1)


def test_main_advance_restarts_exact_sha_admission() -> None:
    client = ApprovedClient()
    client.main_commits = [COMMIT, OLD_COMMIT]

    with pytest.raises(latest.LatestMainError, match="did not finish successfully"):
        latest.wait_for_release_candidate(
            client,
            timeout_seconds=1,
            monotonic=StepClock(advance_after=12),
            sleep=lambda _seconds: None,
        )

    queried_shas = [
        query["head_sha"]
        for path, query in client.calls
        if path.endswith("/actions/runs")
    ]
    assert COMMIT in queried_shas and OLD_COMMIT in queried_shas


def test_public_evidence_is_bound_to_packaging_run_attempt() -> None:
    client = ApprovedClient()
    asset = latest.find_public_evidence_asset(client, _candidate())

    assert asset is not None
    assert asset.name == latest.PUBLIC_EVIDENCE_ASSET_NAME
    assert asset.label == f"release-image-evidence-{COMMIT}-103-1"
    assert asset.download_url == latest.PUBLIC_EVIDENCE_ASSET_URL


def test_public_evidence_requires_github_archive_digest() -> None:
    client = ApprovedClient()
    client.asset_changes = {"digest": None}

    with pytest.raises(latest.LatestMainError, match="evidence digest is invalid"):
        latest.find_public_evidence_asset(client, _candidate())


def test_stale_public_evidence_label_waits_for_selected_run() -> None:
    client = ApprovedClient()
    client.asset_changes = {"label": f"release-image-evidence-{OLD_COMMIT}-99-1"}

    assert latest.find_public_evidence_asset(client, _candidate()) is None


def test_anonymous_api_404_is_classified_as_missing() -> None:
    class MissingOpener:
        def open(self, request: Request, **_kwargs: object) -> object:
            raise HTTPError(request.full_url, 404, "not found", {}, io.BytesIO())

    client = latest.GitHubClient(
        opener=MissingOpener(),
        sleep=lambda _seconds: None,
    )

    with pytest.raises(latest._GitHubNotFoundError):
        client.get_json(
            f"/repos/{latest.REPOSITORY}/releases/tags/"
            f"{latest.PUBLIC_EVIDENCE_RELEASE_TAG}"
        )


def test_missing_public_release_is_retried_within_appearance_window() -> None:
    class AppearingClient(ApprovedClient):
        missing = True

        def get_json(self, path: str, **kwargs: object) -> object:
            if path.endswith(
                f"/releases/tags/{latest.PUBLIC_EVIDENCE_RELEASE_TAG}"
            ) and self.missing:
                self.missing = False
                raise latest._GitHubNotFoundError("not found")
            return super().get_json(path, **kwargs)

    sleeps: list[float] = []
    asset = latest.wait_for_public_evidence_asset(
        AppearingClient(),
        _candidate(),
        monotonic=lambda: 0.0,
        sleep=sleeps.append,
    )

    assert asset.name == latest.PUBLIC_EVIDENCE_ASSET_NAME
    assert sleeps == [latest.POLL_INTERVAL_SECONDS]


def test_public_evidence_requires_actions_uploader_and_exact_url() -> None:
    client = ApprovedClient()
    client.asset_changes = {"uploader": {"login": "maintainer"}}
    with pytest.raises(latest.LatestMainError, match="evidence is invalid"):
        latest.find_public_evidence_asset(client, _candidate())

    client.asset_changes = {
        "browser_download_url": "https://example.invalid/release-image-evidence.zip"
    }
    with pytest.raises(latest.LatestMainError, match="evidence is invalid"):
        latest.find_public_evidence_asset(client, _candidate())


def test_safe_archive_extraction_rejects_path_escape_and_symlink(
    tmp_path: Path,
) -> None:
    escaping = tmp_path / "escaping.zip"
    with zipfile.ZipFile(escaping, "w") as bundle:
        bundle.writestr("../outside.json", "{}")
    with pytest.raises(latest.LatestMainError, match="unsafe archive path"):
        latest.extract_ready_artifact(escaping, tmp_path / "escaping")

    linked = tmp_path / "linked.zip"
    info = zipfile.ZipInfo("release-image-manifest.json")
    info.create_system = 3
    info.external_attr = (stat.S_IFLNK | 0o777) << 16
    with zipfile.ZipFile(linked, "w") as bundle:
        bundle.writestr(info, "target")
    with pytest.raises(latest.LatestMainError, match="link or special file"):
        latest.extract_ready_artifact(linked, tmp_path / "linked")


def test_safe_archive_extraction_rejects_casefold_duplicate(tmp_path: Path) -> None:
    archive = tmp_path / "duplicate.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("subject-backend.json", "{}")
        bundle.writestr("SUBJECT-BACKEND.JSON", "{}")

    with pytest.raises(latest.LatestMainError, match="duplicate paths"):
        latest.extract_ready_artifact(archive, tmp_path / "evidence")


def test_manifest_is_semantically_verified_and_externally_bound(tmp_path: Path) -> None:
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    (evidence / latest.MANIFEST_NAME).write_text(
        json.dumps(_manifest()), encoding="utf-8"
    )
    verification: list[tuple[Path, Path]] = []

    images = latest.validate_release_manifest(
        tmp_path,
        evidence,
        _candidate(),
        verify=lambda checkout, root: verification.append((checkout, root)),
    )

    assert images == (BACKEND, FRONTEND)
    assert verification == [(tmp_path, evidence)]

    mutated = _manifest(run_attempt=2)
    (evidence / latest.MANIFEST_NAME).write_text(json.dumps(mutated), encoding="utf-8")
    with pytest.raises(latest.LatestMainError, match="not bound to the selected run"):
        latest.validate_release_manifest(
            tmp_path,
            evidence,
            _candidate(),
            verify=lambda _checkout, _root: None,
        )

    wrong_role = _manifest()
    wrong_role["subjects"][0]["image"]["immutable_ref"] = FRONTEND
    (evidence / latest.MANIFEST_NAME).write_text(
        json.dumps(wrong_role), encoding="utf-8"
    )
    with pytest.raises(latest.LatestMainError, match="role-bound immutable digests"):
        latest.validate_release_manifest(
            tmp_path,
            evidence,
            _candidate(),
            verify=lambda _checkout, _root: None,
        )


def test_deploy_latest_materializes_verifies_atomically_writes_and_hands_off(
    tmp_path: Path,
) -> None:
    root, env_file, subject_path = _managed_root(tmp_path)
    client = ApprovedClient()
    checkout = root / "releases" / COMMIT
    calls: list[tuple[str, object]] = []

    subject = latest.deploy_latest_main(
        root=root,
        client=client,
        materialize=lambda release_root, commit: (
            calls.append(("materialize", (release_root, commit))) or checkout
        ),
        verify_manifest=lambda target, evidence: calls.append(
            ("verify", (target, evidence))
        ),
        deploy=lambda target: calls.append(("deploy", target)),
    )

    assert subject == sandbox_quickstart.Subject(COMMIT, BACKEND, FRONTEND, env_file)
    assert calls[0] == ("materialize", (root / "releases", COMMIT))
    assert calls[1][0] == "verify"
    assert calls[2] == ("deploy", checkout)
    persisted = sandbox_quickstart._load_subject(subject_path, root)
    assert persisted == subject
    assert stat.S_IMODE(subject_path.stat().st_mode) == 0o600
    assert client.downloads == [latest.PUBLIC_EVIDENCE_ASSET_URL]


def test_pre_admission_failure_preserves_previous_subject_bytes(tmp_path: Path) -> None:
    root, _env_file, subject_path = _managed_root(tmp_path)
    previous = subject_path.read_bytes()
    client = ApprovedClient()

    with pytest.raises(latest.LatestMainError, match="semantic rejection"):
        latest.deploy_latest_main(
            root=root,
            client=client,
            materialize=lambda _root, _commit: root / "releases" / COMMIT,
            verify_manifest=lambda _target, _evidence: (_ for _ in ()).throw(
                latest.LatestMainError("semantic rejection")
            ),
            deploy=lambda _target: pytest.fail("deployment must not start"),
        )

    assert subject_path.read_bytes() == previous


def test_post_admission_deploy_failure_keeps_approved_retry_subject(
    tmp_path: Path,
) -> None:
    root, _env_file, subject_path = _managed_root(tmp_path)

    with pytest.raises(latest.LatestMainError, match="deployment failed"):
        latest.deploy_latest_main(
            root=root,
            client=ApprovedClient(),
            materialize=lambda _root, _commit: root / "releases" / COMMIT,
            verify_manifest=lambda _target, _evidence: None,
            deploy=lambda _target: (_ for _ in ()).throw(
                latest.LatestMainError("deployment failed")
            ),
        )

    assert sandbox_quickstart._load_subject(subject_path, root).commit == COMMIT


def test_retry_approved_subject_uses_its_materialized_release_checkout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, _env_file, _subject_path = _managed_root(tmp_path)
    observed: list[tuple[Path, Path]] = []

    class RetryQuickstart:
        def __init__(self, repo: Path, managed_root: Path) -> None:
            observed.append((repo, managed_root))

        def run(self) -> sandbox_quickstart.Subject:
            return sandbox_quickstart.Subject(
                OLD_COMMIT,
                OLD_BACKEND,
                OLD_FRONTEND,
            )

    monkeypatch.setattr(sandbox_quickstart, "Quickstart", RetryQuickstart)

    result = latest._retry_approved_subject(root)

    assert result.commit == OLD_COMMIT
    assert observed == [(root / "releases" / OLD_COMMIT, root)]


def test_main_without_latest_uses_the_approved_subject_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: list[Path] = []
    monkeypatch.setattr(latest, "deployment_lock", lambda _root: nullcontext())
    monkeypatch.setattr(
        latest,
        "_retry_approved_subject",
        lambda root: observed.append(root),
    )

    assert latest.main([]) == 0
    assert observed == [sandbox_quickstart.MANAGED_ROOT]


def test_deployment_lock_rejects_overlap(tmp_path: Path) -> None:
    root = tmp_path / "managed"
    root.mkdir(mode=0o700)

    with latest.deployment_lock(root):
        with pytest.raises(latest.LatestMainError, match="already running"):
            with latest.deployment_lock(root):
                pytest.fail("overlapping lock was acquired")

    with latest.deployment_lock(root):
        pass


def test_github_tokens_are_removed_before_anonymous_access() -> None:
    environment = {
        "GH_TOKEN": "secret-one",
        "GITHUB_TOKEN": "secret-two",
        "PATH": "/bin",
    }

    latest._drop_github_tokens(environment)

    assert environment == {"PATH": "/bin"}
    request = latest.GitHubClient()._github_request(
        f"{latest.API_ROOT}/repos/{latest.REPOSITORY}/git/ref/heads/main"
    )
    assert request.get_header("Authorization") is None


def test_target_quickstart_child_environment_excludes_github_credentials(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    checkout = tmp_path / "checkout"
    script = checkout / "tools" / "sandbox_quickstart.py"
    script.parent.mkdir(parents=True)
    script.write_text("raise SystemExit(0)\n", encoding="utf-8")
    observed: list[dict[str, str]] = []

    def run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        observed.append(kwargs["env"])
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setenv("GH_TOKEN", "secret-one")
    monkeypatch.setenv("GITHUB_TOKEN", "secret-two")
    monkeypatch.setenv(latest.ENV_PATH_VARIABLE, str(tmp_path / ".env"))
    monkeypatch.setattr(latest.subprocess, "run", run)

    latest._run_target_quickstart(checkout)

    assert len(observed) == 1
    forbidden = {*latest.TOKEN_VARIABLES, latest.ENV_PATH_VARIABLE}
    assert not forbidden & set(observed[0])


def test_release_redirect_accepts_only_github_https() -> None:
    handler = latest._ArtifactRedirectHandler()
    request = Request(
        latest.PUBLIC_EVIDENCE_ASSET_URL,
        headers={"User-Agent": "test"},
    )
    redirected = handler.redirect_request(
        request,
        None,
        302,
        "Found",
        {},
        "https://release-assets.githubusercontent.com/release.zip",
    )
    assert redirected is not None

    for rejected in (
        "http://release-assets.githubusercontent.com/release.zip",
        "https://results.actions.githubusercontent.com/release.zip",
        "https://example.blob.core.windows.net/release.zip",
    ):
        with pytest.raises(HTTPError):
            handler.redirect_request(
                request,
                None,
                302,
                "Found",
                {},
                rejected,
            )


def test_public_evidence_download_retries_and_removes_partial_file(
    tmp_path: Path,
) -> None:
    class Response:
        def __init__(self, chunks: list[bytes | BaseException]) -> None:
            self.chunks = iter(chunks)
            self.headers: dict[str, str] = {}

        def __enter__(self) -> "Response":
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def geturl(self) -> str:
            return "https://release-assets.githubusercontent.com/release.zip"

        def read(self, _size: int) -> bytes:
            value = next(self.chunks, b"")
            if isinstance(value, BaseException):
                raise value
            return value

    class Opener:
        def __init__(self) -> None:
            self.responses = iter(
                [
                    Response([b"partial", OSError("connection lost")]),
                    Response([b"complete", b""]),
                ]
            )
            self.requests: list[Request] = []

        def open(self, request: Request, **_kwargs: object) -> Response:
            self.requests.append(request)
            return next(self.responses)

    opener = Opener()
    client = latest.GitHubClient(opener=opener, sleep=lambda _seconds: None)
    client._curl_path = None
    destination = tmp_path / "evidence.zip"

    digest = client.download_public_asset(
        latest.PUBLIC_EVIDENCE_ASSET_URL, destination
    )

    assert destination.read_bytes() == b"complete"
    assert digest == hashlib.sha256(b"complete").hexdigest()
    assert len(opener.requests) == 2
    assert all(
        request.full_url == latest.PUBLIC_EVIDENCE_ASSET_URL
        for request in opener.requests
    )


def test_curl_download_receives_only_short_lived_url_through_stdin(
    tmp_path: Path,
) -> None:
    client = latest.GitHubClient(sleep=lambda _seconds: None)
    client._curl_path = "/usr/bin/curl"
    signed_url = (
        "https://release-assets.githubusercontent.com/release.zip?sig=short-lived"
    )
    client._resolve_public_asset_download_url = lambda _url: signed_url
    destination = tmp_path / "evidence.zip"
    observed: list[tuple[list[str], str, dict[str, str]]] = []

    def run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        observed.append((command, kwargs["input"], kwargs["env"]))
        destination.write_bytes(b"verified archive")
        return subprocess.CompletedProcess(command, 0)

    client._curl_run = run

    digest = client.download_public_asset(
        latest.PUBLIC_EVIDENCE_ASSET_URL, destination
    )

    assert digest == hashlib.sha256(b"verified archive").hexdigest()
    assert len(observed) == 1
    command, config, environment = observed[0]
    assert command == ["/usr/bin/curl", "-q", "--config", "-"]
    assert signed_url not in command
    assert "Authorization" not in config
    assert signed_url in config
    assert "\nlocation\n" not in config
    assert 'continue-at = "-"' in config
    assert not set(latest.TOKEN_VARIABLES) & set(environment)


def test_curl_download_reacquires_url_and_resumes_temporary_partial(
    tmp_path: Path,
) -> None:
    client = latest.GitHubClient(sleep=lambda _seconds: None)
    client._curl_path = "/usr/bin/curl"
    urls = iter(
        [
            "https://release-assets.githubusercontent.com/release.zip?sig=one",
            "https://release-assets.githubusercontent.com/release.zip?sig=two",
        ]
    )
    resolved: list[str] = []

    def resolve(url: str) -> str:
        resolved.append(url)
        return next(urls)

    client._resolve_public_asset_download_url = resolve
    destination = tmp_path / "evidence.zip"
    attempts = 0

    def run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            destination.write_bytes(b"partial-")
            return subprocess.CompletedProcess(command, 28)
        assert destination.read_bytes() == b"partial-"
        with destination.open("ab") as handle:
            handle.write(b"complete")
        return subprocess.CompletedProcess(command, 0)

    client._curl_run = run

    digest = client.download_public_asset(
        latest.PUBLIC_EVIDENCE_ASSET_URL, destination
    )

    assert resolved == [
        latest.PUBLIC_EVIDENCE_ASSET_URL,
        latest.PUBLIC_EVIDENCE_ASSET_URL,
    ]
    assert destination.read_bytes() == b"partial-complete"
    assert digest == hashlib.sha256(b"partial-complete").hexdigest()


def test_wall_timeout_interrupts_a_stalled_read() -> None:
    started = time.monotonic()
    with pytest.raises(TimeoutError):
        with latest._wall_timeout(0.01):
            time.sleep(1)
    assert time.monotonic() - started < 0.5


def test_actions_wait_budget_bounds_blocked_api_request() -> None:
    class BlockingOpener:
        def open(self, _request: Request, **_kwargs: object) -> object:
            time.sleep(5)
            raise AssertionError("wall timeout did not interrupt the request")

    client = latest.GitHubClient(
        opener=BlockingOpener(),
        sleep=lambda _seconds: None,
    )
    started = time.monotonic()

    with pytest.raises(latest.LatestMainError, match="wait budget"):
        latest.wait_for_release_candidate(client, timeout_seconds=1)

    assert time.monotonic() - started < 1.5


def test_first_deployment_requires_explicit_managed_env(tmp_path: Path) -> None:
    root = tmp_path / "managed"
    root.mkdir(mode=0o700)
    (root / "incoming").mkdir(mode=0o700)

    with pytest.raises(latest.LatestMainError, match="first latest-main deployment"):
        latest.resolve_managed_env(root, None)
