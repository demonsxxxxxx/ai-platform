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

import pytest

from tools import latest_main_quickstart as latest
from tools import release_image_manifest
from tools import sandbox_quickstart


COMMIT = "1" * 40
OLD_COMMIT = "2" * 40
BACKEND = sandbox_quickstart.BACKEND_REPOSITORY + "@sha256:" + "3" * 64
FRONTEND = sandbox_quickstart.FRONTEND_REPOSITORY + "@sha256:" + "4" * 64
OLD_BACKEND = sandbox_quickstart.BACKEND_REPOSITORY + "@sha256:" + "5" * 64
OLD_FRONTEND = sandbox_quickstart.FRONTEND_REPOSITORY + "@sha256:" + "6" * 64
ASSET_URL = latest._release_asset_url(f"deployment-{COMMIT}-103-1")


def _manifest(*, run_id: int = 103, run_attempt: int = 1) -> dict[str, object]:
    def subject(role: str, repository: str, immutable_ref: str) -> dict[str, object]:
        digest = immutable_ref.rsplit("@", 1)[1]
        artifact = (
            f"release-image-subject-{COMMIT}-{run_id}-{run_attempt}-{role}"
        )
        ready_artifact = f"release-image-evidence-{COMMIT}-{run_id}-{run_attempt}"
        dockerfile = "Dockerfile" if role == "backend" else "frontend/web/Dockerfile"
        return {
            "role": role,
            "platform": "linux/amd64",
            "build": {
                "context": {"path": ".", "source_commit": COMMIT},
                "dockerfile": {"path": dockerfile, "sha256": "7" * 64},
            },
            "image": {
                "subject": repository,
                "source_tag": f"{repository}:{COMMIT}",
                "manifest_digest": digest,
                "immutable_ref": immutable_ref,
            },
            "evidence": {
                "sbom": {
                    "format": "spdx-json",
                    "ref": f"oci://{repository}@{digest}#sbom-spdx-attestation",
                    "sha256": "8" * 64,
                    "unbound_content_sha256": "9" * 64,
                },
                "provenance": {
                    "predicate_type": "https://slsa.dev/provenance/v1",
                    "attestation_id": f"attestation-{role}",
                    "ref": (
                        f"https://github.com/{latest.REPOSITORY}/attestations/"
                        f"attestation-{role}"
                    ),
                    "bundle_ref": (
                        f"github-artifact://{artifact}/provenance-{role}.bundle.json"
                    ),
                    "bundle_sha256": "a" * 64,
                    "verification_ref": (
                        f"github-artifact://{artifact}/provenance-{role}.verified.json"
                    ),
                    "verification_sha256": "b" * 64,
                    "reverification_ref": (
                        f"github-artifact://{ready_artifact}/"
                        f"provenance-{role}.assembly-verified.json"
                    ),
                    "reverification_sha256": "c" * 64,
                },
                "signature": {
                    "identity": (
                        f"https://github.com/{release_image_manifest.WORKFLOW_REPOSITORY}/"
                        ".github/workflows/ai-platform-packaging-publish.yml@refs/heads/main"
                    ),
                    "issuer": "https://token.actions.githubusercontent.com",
                    "ref": f"oci://{repository}@{digest}#cosign-keyless-signature",
                },
                "scan": {
                    "blocking_severities": ["HIGH", "CRITICAL"],
                    "ref": f"github-artifact://{artifact}/trivy-{role}.json",
                    "result": "passed",
                    "scanner": "trivy@0.70.0",
                    "sha256": "d" * 64,
                },
            },
        }

    return {
        "schema_version": release_image_manifest.SCHEMA_VERSION,
        "source_commit": COMMIT,
        "repository": latest.REPOSITORY_URL,
        "workflow": {
            "repository": latest.REPOSITORY,
            "workflow_ref": (
                f"{release_image_manifest.WORKFLOW_REPOSITORY}/.github/workflows/"
                "ai-platform-packaging-publish.yml@refs/heads/main"
            ),
            "run_id": str(run_id),
            "run_attempt": run_attempt,
            "head_sha": COMMIT,
        },
        "subjects": [
            subject("backend", sandbox_quickstart.BACKEND_REPOSITORY, BACKEND),
            subject("frontend", sandbox_quickstart.FRONTEND_REPOSITORY, FRONTEND),
        ],
    }


def _manifest_bytes(manifest: dict[str, object] | None = None) -> bytes:
    return json.dumps(manifest or _manifest()).encode("utf-8")


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
    def __init__(self, *, manifest: dict[str, object] | None = None) -> None:
        self.manifest = _manifest_bytes(manifest)
        self.calls: list[tuple[str, dict[str, str]]] = []
        self.downloads: list[str] = []
        self.release_changes: dict[str, object] = {}
        self.asset_changes: dict[str, object] = {}
        self.leading_releases: list[dict[str, object]] = []
        self.downloaded_digest: str | None = None

    def get_json(
        self,
        path: str,
        *,
        query: dict[str, str] | None = None,
        timeout_seconds: float | None = None,
    ) -> object:
        assert timeout_seconds is None or timeout_seconds > 0
        self.calls.append((path, query or {}))
        assert path == f"/repos/{latest.REPOSITORY}/releases"
        assert query == {"per_page": "100"}
        tag = f"deployment-{COMMIT}-103-1"
        return [
            *self.leading_releases,
            {
                "tag_name": tag,
                "target_commitish": COMMIT,
                "draft": False,
                "prerelease": False,
                "immutable": True,
                "published_at": "2026-09-02T00:00:00Z",
                "author": {"login": latest.DEPLOYMENT_RELEASE_UPLOADER},
                "assets": [
                    {
                        "name": latest.DEPLOYMENT_RELEASE_ASSET_NAME,
                        "label": latest._release_asset_label(COMMIT, 103, 1),
                        "size": len(self.manifest),
                        "state": "uploaded",
                        "digest": "sha256:"
                        + hashlib.sha256(self.manifest).hexdigest(),
                        "browser_download_url": latest._release_asset_url(tag),
                        "uploader": {
                            "login": latest.DEPLOYMENT_RELEASE_UPLOADER
                        },
                        **self.asset_changes,
                    }
                ],
                **self.release_changes,
            },
        ]

    def download_public_asset(self, url: str, destination: Path) -> str:
        self.downloads.append(url)
        destination.write_bytes(self.manifest)
        destination.chmod(0o600)
        return self.downloaded_digest or hashlib.sha256(self.manifest).hexdigest()


def _release() -> latest.DeploymentRelease:
    return latest.resolve_deployment_release(ApprovedClient())


def test_resolve_deployment_release_uses_one_release_list_request() -> None:
    client = ApprovedClient()

    release = latest.resolve_deployment_release(client)

    assert release == latest.DeploymentRelease(
        f"deployment-{COMMIT}-103-1",
        COMMIT,
        103,
        1,
        latest.ReleaseAsset(
            latest.DEPLOYMENT_RELEASE_ASSET_NAME,
            latest._release_asset_label(COMMIT, 103, 1),
            len(client.manifest),
            "sha256:" + hashlib.sha256(client.manifest).hexdigest(),
            latest._release_asset_url(f"deployment-{COMMIT}-103-1"),
        ),
    )
    assert client.calls == [
        (f"/repos/{latest.REPOSITORY}/releases", {"per_page": "100"})
    ]


def test_resolve_deployment_release_skips_newer_mutable_release() -> None:
    client = ApprovedClient()
    client.leading_releases = [
        {
            "tag_name": f"deployment-{OLD_COMMIT}-102-1",
            "draft": False,
            "prerelease": False,
            "immutable": False,
        },
        {"tag_name": "latest-main-evidence", "immutable": False},
    ]

    assert latest.resolve_deployment_release(client).source_commit == COMMIT


@pytest.mark.parametrize(
    "change",
    [
        {"tag_name": "latest-main-evidence"},
        {"target_commitish": OLD_COMMIT},
        {"draft": True},
        {"prerelease": True},
        {"immutable": False},
        {"author": {"login": "maintainer"}},
    ],
)
def test_deployment_release_requires_exact_published_bot_metadata(
    change: dict[str, object],
) -> None:
    client = ApprovedClient()
    client.release_changes = change

    with pytest.raises(latest.LatestMainError):
        latest.resolve_deployment_release(client)


@pytest.mark.parametrize(
    "change",
    [
        {"label": "stale"},
        {"digest": None},
        {"uploader": {"login": "maintainer"}},
        {"browser_download_url": "https://example.invalid/manifest.json"},
    ],
)
def test_deployment_release_requires_exact_manifest_asset(
    change: dict[str, object],
) -> None:
    client = ApprovedClient()
    client.asset_changes = change

    with pytest.raises(latest.LatestMainError):
        latest.resolve_deployment_release(client)


def test_anonymous_api_404_is_fail_closed() -> None:
    class MissingOpener:
        def open(self, request: Request, **_kwargs: object) -> object:
            raise HTTPError(request.full_url, 404, "not found", {}, io.BytesIO())

    client = latest.GitHubClient(
        opener=MissingOpener(),
        sleep=lambda _seconds: None,
    )

    with pytest.raises(latest._GitHubNotFoundError):
        client.get_json(f"/repos/{latest.REPOSITORY}/releases")


def test_manifest_is_strict_and_bound_to_release_and_images(tmp_path: Path) -> None:
    manifest_path = tmp_path / latest.MANIFEST_NAME
    manifest_path.write_bytes(_manifest_bytes())

    assert latest.validate_release_manifest(manifest_path, _release()) == (
        BACKEND,
        FRONTEND,
    )

    mutated = _manifest(run_attempt=2)
    manifest_path.write_bytes(_manifest_bytes(mutated))
    with pytest.raises(latest.LatestMainError, match="binding is invalid"):
        latest.validate_release_manifest(manifest_path, _release())

    wrong_image = _manifest()
    wrong_image["subjects"][0]["image"]["immutable_ref"] = FRONTEND
    manifest_path.write_bytes(_manifest_bytes(wrong_image))
    with pytest.raises(latest.LatestMainError, match="manifest is invalid"):
        latest.validate_release_manifest(manifest_path, _release())

    failed_scan = _manifest()
    failed_scan["subjects"][0]["evidence"]["scan"]["result"] = "failed"
    manifest_path.write_bytes(_manifest_bytes(failed_scan))
    with pytest.raises(latest.LatestMainError, match="manifest is invalid"):
        latest.validate_release_manifest(manifest_path, _release())

    unknown_key = _manifest()
    unknown_key["unexpected"] = True
    manifest_path.write_bytes(_manifest_bytes(unknown_key))
    with pytest.raises(latest.LatestMainError, match="manifest is invalid"):
        latest.validate_release_manifest(manifest_path, _release())


def test_deploy_latest_release_materializes_writes_and_hands_off(
    tmp_path: Path,
) -> None:
    root, env_file, subject_path = _managed_root(tmp_path)
    client = ApprovedClient()
    checkout = root / "releases" / COMMIT
    calls: list[tuple[str, object]] = []

    subject = latest.deploy_latest_release(
        root=root,
        client=client,
        materialize=lambda release_root, commit: (
            calls.append(("materialize", (release_root, commit))) or checkout
        ),
        deploy=lambda target: calls.append(("deploy", target)),
    )

    assert subject == sandbox_quickstart.Subject(COMMIT, BACKEND, FRONTEND, env_file)
    assert calls == [
        ("materialize", (root / "releases", COMMIT)),
        ("deploy", checkout),
    ]
    assert sandbox_quickstart._load_subject(subject_path, root) == subject
    assert stat.S_IMODE(subject_path.stat().st_mode) == 0o600
    assert client.downloads == [
        latest._release_asset_url(f"deployment-{COMMIT}-103-1")
    ]


def test_pre_admission_failure_preserves_previous_subject_bytes(tmp_path: Path) -> None:
    root, _env_file, subject_path = _managed_root(tmp_path)
    previous = subject_path.read_bytes()
    client = ApprovedClient()
    client.downloaded_digest = "0" * 64

    with pytest.raises(latest.LatestMainError, match="digest does not match"):
        latest.deploy_latest_release(
            root=root,
            client=client,
            materialize=lambda _root, _commit: pytest.fail(
                "materialization must not start"
            ),
            deploy=lambda _target: pytest.fail("deployment must not start"),
        )

    assert subject_path.read_bytes() == previous


def test_post_admission_deploy_failure_keeps_approved_retry_subject(
    tmp_path: Path,
) -> None:
    root, _env_file, subject_path = _managed_root(tmp_path)

    with pytest.raises(latest.LatestMainError, match="deployment failed"):
        latest.deploy_latest_release(
            root=root,
            client=ApprovedClient(),
            materialize=lambda _root, _commit: root / "releases" / COMMIT,
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
        f"{latest.API_ROOT}/repos/{latest.REPOSITORY}/releases"
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
    handler = latest._ReleaseRedirectHandler()
    request = Request(
        ASSET_URL,
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


def test_release_manifest_download_retries_and_removes_partial_file(
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
    destination = tmp_path / "manifest.json"

    digest = client.download_public_asset(
        ASSET_URL, destination
    )

    assert destination.read_bytes() == b"complete"
    assert digest == hashlib.sha256(b"complete").hexdigest()
    assert len(opener.requests) == 2
    assert all(request.full_url == ASSET_URL for request in opener.requests)


def test_curl_download_receives_only_short_lived_url_through_stdin(
    tmp_path: Path,
) -> None:
    client = latest.GitHubClient(sleep=lambda _seconds: None)
    client._curl_path = "/usr/bin/curl"
    signed_url = (
        "https://release-assets.githubusercontent.com/release.zip?sig=short-lived"
    )
    client._resolve_public_asset_download_url = lambda _url: signed_url
    destination = tmp_path / "manifest.json"
    observed: list[tuple[list[str], str, dict[str, str]]] = []

    def run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        observed.append((command, kwargs["input"], kwargs["env"]))
        destination.write_bytes(b"verified manifest")
        return subprocess.CompletedProcess(command, 0)

    client._curl_run = run

    digest = client.download_public_asset(
        ASSET_URL, destination
    )

    assert digest == hashlib.sha256(b"verified manifest").hexdigest()
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
    destination = tmp_path / "manifest.json"
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
        ASSET_URL, destination
    )

    assert resolved == [ASSET_URL, ASSET_URL]
    assert destination.read_bytes() == b"partial-complete"
    assert digest == hashlib.sha256(b"partial-complete").hexdigest()


def test_wall_timeout_interrupts_a_stalled_read() -> None:
    started = time.monotonic()
    with pytest.raises(TimeoutError):
        with latest._wall_timeout(0.01):
            time.sleep(1)
    assert time.monotonic() - started < 0.5


def test_first_deployment_requires_explicit_managed_env(tmp_path: Path) -> None:
    root = tmp_path / "managed"
    root.mkdir(mode=0o700)
    (root / "incoming").mkdir(mode=0o700)

    with pytest.raises(latest.LatestMainError, match="first deployment"):
        latest.resolve_managed_env(root, None)
