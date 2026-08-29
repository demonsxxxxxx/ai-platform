"""Resolve and deploy the latest fully approved main image subject."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import dataclass
import fcntl
from http.client import HTTPException, IncompleteRead
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
from typing import Any, Callable, Iterator, Mapping, MutableMapping, Protocol, Sequence
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
import zipfile


if __name__ == "__main__" and not sys.flags.isolated:
    raise SystemExit("run latest-main quickstart through the approved host wrapper")

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools import release_authority  # noqa: E402
from tools import sandbox_quickstart  # noqa: E402


REPOSITORY = "demonsxxxxxx/ai-platform"
REPOSITORY_URL = f"https://github.com/{REPOSITORY}.git"
API_ROOT = "https://api.github.com"
API_VERSION = "2022-11-28"
MAIN_REF = "refs/heads/main"
MANIFEST_NAME = "release-image-manifest.json"
MANIFEST_WORKFLOW_REF = (
    f"{REPOSITORY}/.github/workflows/ai-platform-packaging-publish.yml@refs/heads/main"
)
TOKEN_VARIABLES = ("GH_TOKEN", "GITHUB_TOKEN")
ENV_PATH_VARIABLE = "AI_PLATFORM_QUICKSTART_ENV_FILE"
DEFAULT_CI_TIMEOUT_SECONDS = 30 * 60
POLL_INTERVAL_SECONDS = 15
ARTIFACT_APPEARANCE_TIMEOUT_SECONDS = 120
API_RESPONSE_MAX_BYTES = 4 * 1024 * 1024
ARCHIVE_MAX_BYTES = 128 * 1024 * 1024
ARCHIVE_MAX_FILES = 64
ARCHIVE_MAX_FILE_BYTES = 64 * 1024 * 1024
ARCHIVE_MAX_EXTRACTED_BYTES = 512 * 1024 * 1024
HTTP_TIMEOUT_SECONDS = 30
HTTP_ATTEMPTS = 3
DOWNLOAD_ATTEMPT_TIMEOUT_SECONDS = 125
CURL_DOWNLOAD_TIMEOUT_SECONDS = 120
COMMIT_RE = re.compile(r"[0-9a-f]{40}\Z")
DIGEST_RE = re.compile(r"sha256:[0-9a-f]{64}\Z")


class LatestMainError(RuntimeError):
    """A bounded latest-main admission or deployment failure."""


class _DuplicateJsonKey(ValueError):
    pass


@dataclass(frozen=True)
class WorkflowSpec:
    file_name: str
    path: str
    required_job: str


@dataclass(frozen=True)
class WorkflowRun:
    spec: WorkflowSpec
    run_id: int
    run_attempt: int
    source_commit: str


@dataclass(frozen=True)
class ReadyArtifact:
    artifact_id: int
    name: str
    size_bytes: int
    digest: str


@dataclass(frozen=True)
class ReleaseCandidate:
    source_commit: str
    runs: Mapping[str, WorkflowRun]

    @property
    def packaging_run(self) -> WorkflowRun:
        return self.runs[PACKAGING_WORKFLOW.file_name]


BACKEND_WORKFLOW = WorkflowSpec(
    "ai-platform-backend.yml",
    ".github/workflows/ai-platform-backend.yml",
    "backend required",
)
FRONTEND_WORKFLOW = WorkflowSpec(
    "ai-platform-frontend.yml",
    ".github/workflows/ai-platform-frontend.yml",
    "frontend required",
)
PACKAGING_WORKFLOW = WorkflowSpec(
    "ai-platform-packaging-publish.yml",
    ".github/workflows/ai-platform-packaging-publish.yml",
    "release image ready manifest",
)
WORKFLOWS = (BACKEND_WORKFLOW, FRONTEND_WORKFLOW, PACKAGING_WORKFLOW)


class GitHubAPI(Protocol):
    def get_json(
        self,
        path: str,
        *,
        query: Mapping[str, str] | None = None,
        timeout_seconds: float | None = None,
    ) -> Any: ...

    def download_artifact(self, artifact_id: int, destination: Path) -> str: ...


def _reject_duplicate_json_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise _DuplicateJsonKey(key)
        value[key] = item
    return value


def _loads_json(payload: str | bytes, name: str) -> Any:
    try:
        text = payload.decode("utf-8") if isinstance(payload, bytes) else payload
        return json.loads(text, object_pairs_hook=_reject_duplicate_json_keys)
    except (_DuplicateJsonKey, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise LatestMainError(f"{name} is invalid") from exc


def _mapping(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise LatestMainError(f"{name} is invalid")
    return value


def _array(value: Any, name: str) -> list[Any]:
    if not isinstance(value, list):
        raise LatestMainError(f"{name} is invalid")
    return value


def _positive_int(value: Any, name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise LatestMainError(f"{name} is invalid")
    return value


def _trusted_download_host(host: str | None) -> bool:
    if not host:
        return False
    lowered = host.lower().rstrip(".")
    return (
        lowered in {"api.github.com", "github.com", "objects.githubusercontent.com"}
        or lowered.endswith(".githubusercontent.com")
        or lowered.endswith(".actions.githubusercontent.com")
        or lowered.endswith(".blob.core.windows.net")
    )


class _ArtifactRedirectHandler(HTTPRedirectHandler):
    """Permit GitHub artifact redirects without forwarding the API token."""

    def redirect_request(
        self,
        req: Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> Request | None:
        target = urlsplit(newurl)
        if target.scheme != "https" or not _trusted_download_host(target.hostname):
            raise HTTPError(req.full_url, code, "unsafe artifact redirect", headers, fp)
        redirected = super().redirect_request(req, fp, code, msg, headers, newurl)
        if redirected is None:
            return None
        if target.hostname != "api.github.com":
            redirected.headers.pop("Authorization", None)
            redirected.unredirected_hdrs.pop("Authorization", None)
        return redirected


class _NoRedirectHandler(HTTPRedirectHandler):
    def redirect_request(
        self,
        req: Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> None:
        return None


class GitHubClient:
    def __init__(
        self,
        token: str,
        *,
        opener: Any | None = None,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        normalized = token.strip()
        if (
            not normalized
            or len(normalized) > 4096
            or any(character in normalized for character in "\r\n")
        ):
            raise LatestMainError("GitHub Actions token is missing or invalid")
        self._token = normalized
        self._opener = opener or build_opener(_ArtifactRedirectHandler())
        self._redirect_opener = build_opener(_NoRedirectHandler())
        self._curl_path = shutil.which("curl")
        self._curl_run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run
        self._sleep = sleep
        self._monotonic = monotonic

    def _api_request(self, url: str) -> Request:
        return Request(
            url,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {self._token}",
                "User-Agent": "ai-platform-latest-main-quickstart",
                "X-GitHub-Api-Version": API_VERSION,
            },
            method="GET",
        )

    def _request(
        self, url: str, *, max_bytes: int, timeout_seconds: float | None = None
    ) -> bytes:
        if not url.startswith(f"{API_ROOT}/"):
            raise LatestMainError("GitHub API path is invalid")
        if timeout_seconds is not None and timeout_seconds <= 0:
            raise LatestMainError("GitHub API request exceeded its wait budget")
        deadline = (
            self._monotonic() + timeout_seconds if timeout_seconds is not None else None
        )
        retryable_statuses = {408, 429, 500, 502, 503, 504}
        for attempt in range(HTTP_ATTEMPTS):
            remaining = deadline - self._monotonic() if deadline is not None else None
            if remaining is not None and remaining <= 0:
                raise LatestMainError("GitHub API request exceeded its wait budget")
            wall_timeout = (
                float(HTTP_TIMEOUT_SECONDS + 5)
                if remaining is None
                else min(HTTP_TIMEOUT_SECONDS + 5, remaining)
            )
            socket_timeout = (
                float(HTTP_TIMEOUT_SECONDS)
                if remaining is None
                else min(HTTP_TIMEOUT_SECONDS, remaining)
            )
            request = self._api_request(url)
            try:
                with _wall_timeout(wall_timeout):
                    with self._opener.open(request, timeout=socket_timeout) as response:
                        return _read_bounded_response(response, max_bytes)
            except HTTPError as exc:
                if exc.code in retryable_statuses and attempt + 1 < HTTP_ATTEMPTS:
                    self._bounded_retry_sleep(attempt + 1, deadline)
                    continue
                if exc.code in {401, 403}:
                    raise LatestMainError(
                        "GitHub Actions API rejected the configured credentials"
                    ) from None
                raise LatestMainError(
                    f"GitHub API request failed with status {exc.code}"
                ) from None
            except (HTTPException, IncompleteRead, OSError, TimeoutError, URLError):
                if attempt + 1 < HTTP_ATTEMPTS:
                    self._bounded_retry_sleep(attempt + 1, deadline)
                    continue
                raise LatestMainError("GitHub API request failed") from None
        raise LatestMainError("GitHub API request failed")

    def _bounded_retry_sleep(self, seconds: int, deadline: float | None) -> None:
        delay = float(seconds)
        if deadline is not None:
            remaining = deadline - self._monotonic()
            if remaining <= 0:
                raise LatestMainError("GitHub API request exceeded its wait budget")
            delay = min(delay, remaining)
        self._sleep(delay)

    def get_json(
        self,
        path: str,
        *,
        query: Mapping[str, str] | None = None,
        timeout_seconds: float | None = None,
    ) -> Any:
        if (
            not path.startswith(f"/repos/{REPOSITORY}/")
            or ".." in PurePosixPath(path).parts
        ):
            raise LatestMainError("GitHub API path is invalid")
        suffix = f"?{urlencode(query)}" if query else ""
        return _loads_json(
            self._request(
                f"{API_ROOT}{path}{suffix}",
                max_bytes=API_RESPONSE_MAX_BYTES,
                timeout_seconds=timeout_seconds,
            ),
            "GitHub API response",
        )

    def download_artifact(self, artifact_id: int, destination: Path) -> str:
        _positive_int(artifact_id, "artifact id")
        if destination.exists() or destination.is_symlink():
            raise LatestMainError("artifact destination is not empty")
        if self._curl_path is not None:
            return self._download_artifact_with_curl(artifact_id, destination)
        return self._download_artifact_with_urllib(artifact_id, destination)

    def _resolve_artifact_download_url(self, artifact_id: int) -> str:
        url = f"{API_ROOT}/repos/{REPOSITORY}/actions/artifacts/{artifact_id}/zip"
        retryable_statuses = {408, 429, 500, 502, 503, 504}
        for attempt in range(HTTP_ATTEMPTS):
            try:
                with _wall_timeout(HTTP_TIMEOUT_SECONDS + 5):
                    response = self._redirect_opener.open(
                        self._api_request(url), timeout=HTTP_TIMEOUT_SECONDS
                    )
                response.close()
                raise LatestMainError("GitHub artifact download did not redirect")
            except HTTPError as exc:
                if exc.code in {301, 302, 303, 307, 308}:
                    location = exc.headers.get("Location")
                    exc.close()
                    target = urlsplit(location or "")
                    if target.scheme != "https" or not _trusted_download_host(
                        target.hostname
                    ):
                        raise LatestMainError(
                            "GitHub artifact download redirect is invalid"
                        )
                    return location
                if exc.code in retryable_statuses and attempt + 1 < HTTP_ATTEMPTS:
                    exc.close()
                    self._sleep(float(attempt + 1))
                    continue
                if exc.code in {401, 403}:
                    exc.close()
                    raise LatestMainError(
                        "GitHub Actions API rejected the configured credentials"
                    ) from None
                code = exc.code
                exc.close()
                raise LatestMainError(
                    f"GitHub artifact URL request failed with status {code}"
                ) from None
            except LatestMainError:
                raise
            except (HTTPException, IncompleteRead, OSError, TimeoutError, URLError):
                if attempt + 1 < HTTP_ATTEMPTS:
                    self._sleep(float(attempt + 1))
                    continue
                raise LatestMainError("GitHub artifact URL request failed") from None
        raise LatestMainError("GitHub artifact URL request failed")

    def _download_artifact_with_curl(self, artifact_id: int, destination: Path) -> str:
        if self._curl_path is None:
            raise LatestMainError("curl artifact downloader is unavailable")
        destination.parent.mkdir(parents=True, exist_ok=True)
        for attempt in range(HTTP_ATTEMPTS):
            signed_url = self._resolve_artifact_download_url(artifact_id)
            config = _curl_download_config(signed_url, destination)
            environment = {
                key: os.environ[key]
                for key in (
                    "PATH",
                    "LANG",
                    "LC_ALL",
                    *sandbox_quickstart.PROXY_ENVIRONMENT,
                )
                if key in os.environ
            }
            try:
                result = self._curl_run(
                    [self._curl_path, "-q", "--config", "-"],
                    input=config,
                    env=environment,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    text=True,
                    timeout=DOWNLOAD_ATTEMPT_TIMEOUT_SECONDS,
                    check=False,
                )
            except (OSError, subprocess.SubprocessError):
                result = None
            if result is not None and result.returncode == 0:
                try:
                    metadata = destination.stat(follow_symlinks=False)
                    if (
                        not stat.S_ISREG(metadata.st_mode)
                        or destination.is_symlink()
                        or metadata.st_size < 1
                        or metadata.st_size > ARCHIVE_MAX_BYTES
                    ):
                        raise LatestMainError(
                            "downloaded release artifact is missing or unsafe"
                        )
                    destination.chmod(0o600)
                    return _sha256_file(destination, ARCHIVE_MAX_BYTES)
                except OSError:
                    pass
            if attempt + 1 < HTTP_ATTEMPTS:
                self._sleep(float(attempt + 1))
            else:
                destination.unlink(missing_ok=True)
        raise LatestMainError("GitHub artifact download failed")

    def _download_artifact_with_urllib(
        self, artifact_id: int, destination: Path
    ) -> str:
        url = f"{API_ROOT}/repos/{REPOSITORY}/actions/artifacts/{artifact_id}/zip"
        destination.parent.mkdir(parents=True, exist_ok=True)
        retryable_statuses = {408, 429, 500, 502, 503, 504}
        for attempt in range(HTTP_ATTEMPTS):
            descriptor: int | None = None
            complete = False
            try:
                with _wall_timeout(DOWNLOAD_ATTEMPT_TIMEOUT_SECONDS):
                    with self._opener.open(
                        self._api_request(url), timeout=HTTP_TIMEOUT_SECONDS
                    ) as response:
                        _validate_response(response, ARCHIVE_MAX_BYTES)
                        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
                        if hasattr(os, "O_NOFOLLOW"):
                            flags |= os.O_NOFOLLOW
                        descriptor = os.open(destination, flags, 0o600)
                        digest = hashlib.sha256()
                        total = 0
                        while True:
                            chunk = response.read(64 * 1024)
                            if not chunk:
                                break
                            total += len(chunk)
                            if total > ARCHIVE_MAX_BYTES:
                                raise LatestMainError("GitHub response is too large")
                            digest.update(chunk)
                            view = memoryview(chunk)
                            while view:
                                written = os.write(descriptor, view)
                                if written <= 0:
                                    raise OSError("short artifact write")
                                view = view[written:]
                        os.fsync(descriptor)
                        complete = True
                        return digest.hexdigest()
            except HTTPError as exc:
                if exc.code in retryable_statuses and attempt + 1 < HTTP_ATTEMPTS:
                    self._sleep(float(attempt + 1))
                    continue
                if exc.code in {401, 403}:
                    raise LatestMainError(
                        "GitHub Actions API rejected the configured credentials"
                    ) from None
                raise LatestMainError(
                    f"GitHub artifact download failed with status {exc.code}"
                ) from None
            except LatestMainError:
                raise
            except (HTTPException, IncompleteRead, OSError, TimeoutError, URLError):
                if attempt + 1 < HTTP_ATTEMPTS:
                    self._sleep(float(attempt + 1))
                    continue
                raise LatestMainError("GitHub artifact download failed") from None
            finally:
                if descriptor is not None:
                    os.close(descriptor)
                if descriptor is not None and not complete:
                    destination.unlink(missing_ok=True)
        raise LatestMainError("GitHub artifact download failed")


def _curl_escape(value: str) -> str:
    if "\r" in value or "\n" in value or "\x00" in value:
        raise LatestMainError("curl download input is invalid")
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _curl_download_config(signed_url: str, destination: Path) -> str:
    target = urlsplit(signed_url)
    if target.scheme != "https" or not _trusted_download_host(target.hostname):
        raise LatestMainError("GitHub artifact download URL is invalid")
    return "\n".join(
        (
            "silent",
            "show-error",
            "fail",
            'proto = "=https"',
            "connect-timeout = 20",
            f"max-time = {CURL_DOWNLOAD_TIMEOUT_SECONDS}",
            "speed-time = 45",
            "speed-limit = 1024",
            'continue-at = "-"',
            f"max-filesize = {ARCHIVE_MAX_BYTES}",
            f'output = "{_curl_escape(str(destination))}"',
            f'url = "{_curl_escape(signed_url)}"',
            "",
        )
    )


def _sha256_file(path: Path, max_bytes: int) -> str:
    digest = hashlib.sha256()
    total = 0
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(64 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > max_bytes:
                raise LatestMainError("downloaded release artifact is too large")
            digest.update(chunk)
    if total < 1:
        raise LatestMainError("downloaded release artifact is empty")
    return digest.hexdigest()


@contextmanager
def _wall_timeout(seconds: float) -> Iterator[None]:
    if seconds <= 0:
        raise ValueError("timeout must be positive")
    previous_handler = signal.getsignal(signal.SIGALRM)
    previous_delay, previous_interval = signal.getitimer(signal.ITIMER_REAL)
    started = time.monotonic()

    def expire(_signum: int, _frame: Any) -> None:
        raise TimeoutError

    signal.signal(signal.SIGALRM, expire)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous_handler)
        if previous_delay > 0 or previous_interval > 0:
            elapsed = time.monotonic() - started
            signal.setitimer(
                signal.ITIMER_REAL,
                max(0.000001, previous_delay - elapsed),
                previous_interval,
            )


def _read_bounded_response(response: Any, max_bytes: int) -> bytes:
    _validate_response(response, max_bytes)
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = response.read(min(64 * 1024, max_bytes + 1 - total))
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise LatestMainError("GitHub response is too large")
        chunks.append(chunk)
    return b"".join(chunks)


def _validate_response(response: Any, max_bytes: int) -> None:
    final_url = response.geturl()
    target = urlsplit(final_url)
    if target.scheme != "https" or not _trusted_download_host(target.hostname):
        raise LatestMainError("GitHub response origin is invalid")
    content_length = response.headers.get("Content-Length")
    if content_length is not None:
        try:
            declared = int(content_length)
        except (TypeError, ValueError) as exc:
            raise LatestMainError("GitHub response length is invalid") from exc
        if declared < 0 or declared > max_bytes:
            raise LatestMainError("GitHub response is too large")


def _claim_github_token(
    environment: MutableMapping[str, str],
    *,
    run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> str:
    token = ""
    for key in TOKEN_VARIABLES:
        candidate = environment.pop(key, "")
        if candidate and not token:
            token = candidate
    if token:
        return token
    gh = shutil.which("gh")
    if gh is None:
        raise LatestMainError(
            "GitHub Actions credentials are required in GH_TOKEN, GITHUB_TOKEN, or gh auth"
        )
    try:
        credential_environment = {
            key: environment[key]
            for key in (
                "PATH",
                "HOME",
                "XDG_CONFIG_HOME",
                "GH_CONFIG_DIR",
                "GH_HOST",
                "LANG",
                "LC_ALL",
                *sandbox_quickstart.PROXY_ENVIRONMENT,
            )
            if key in environment
        }
        result = run(
            [gh, "auth", "token"],
            env=credential_environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        raise LatestMainError(
            "authenticated GitHub CLI credentials are unavailable"
        ) from None
    if result.returncode != 0 or not result.stdout.strip():
        raise LatestMainError("authenticated GitHub CLI credentials are unavailable")
    return result.stdout.strip()


def resolve_main_commit(
    client: GitHubAPI, *, timeout_seconds: float | None = None
) -> str:
    payload = _mapping(
        client.get_json(
            f"/repos/{REPOSITORY}/git/ref/heads/main",
            timeout_seconds=timeout_seconds,
        ),
        "main ref response",
    )
    target = _mapping(payload.get("object"), "main ref object")
    commit = target.get("sha")
    if (
        payload.get("ref") != MAIN_REF
        or target.get("type") != "commit"
        or not isinstance(commit, str)
        or COMMIT_RE.fullmatch(commit) is None
    ):
        raise LatestMainError("authoritative main ref response is invalid")
    return commit


def _workflow_run(
    client: GitHubAPI,
    spec: WorkflowSpec,
    commit: str,
    *,
    timeout_seconds: float | None = None,
) -> WorkflowRun | None:
    encoded = quote(spec.file_name, safe="")
    payload = _mapping(
        client.get_json(
            f"/repos/{REPOSITORY}/actions/workflows/{encoded}/runs",
            query={
                "branch": "main",
                "event": "push",
                "head_sha": commit,
                "per_page": "10",
            },
            timeout_seconds=timeout_seconds,
        ),
        f"{spec.file_name} runs response",
    )
    runs = _array(payload.get("workflow_runs"), f"{spec.file_name} runs")
    exact = [
        run
        for run in runs
        if isinstance(run, dict)
        and run.get("head_sha") == commit
        and run.get("head_branch") == "main"
        and run.get("event") == "push"
        and run.get("path") == spec.path
    ]
    if not exact:
        return None
    selected = max(
        exact,
        key=lambda run: int(run.get("id", 0)) if isinstance(run.get("id"), int) else 0,
    )
    run_id = _positive_int(selected.get("id"), f"{spec.file_name} run id")
    run_attempt = _positive_int(
        selected.get("run_attempt"), f"{spec.file_name} run attempt"
    )
    status = selected.get("status")
    conclusion = selected.get("conclusion")
    if status == "completed" and conclusion != "success":
        raise LatestMainError(
            f"{spec.file_name} failed for current main ({conclusion or 'unknown'})"
        )
    if status != "completed" or conclusion != "success":
        return None
    return WorkflowRun(spec, run_id, run_attempt, commit)


def _require_final_job(
    client: GitHubAPI, run: WorkflowRun, *, timeout_seconds: float | None = None
) -> None:
    payload = _mapping(
        client.get_json(
            f"/repos/{REPOSITORY}/actions/runs/{run.run_id}/jobs",
            query={"filter": "latest", "per_page": "100"},
            timeout_seconds=timeout_seconds,
        ),
        f"{run.spec.file_name} jobs response",
    )
    jobs = _array(payload.get("jobs"), f"{run.spec.file_name} jobs")
    total = payload.get("total_count")
    if not isinstance(total, int) or isinstance(total, bool) or total != len(jobs):
        raise LatestMainError(f"{run.spec.file_name} jobs response is incomplete")
    matching = [
        job
        for job in jobs
        if isinstance(job, dict) and job.get("name") == run.spec.required_job
    ]
    if len(matching) != 1:
        raise LatestMainError(
            f"{run.spec.file_name} required final job is missing or ambiguous"
        )
    job = matching[0]
    if (
        job.get("head_sha") != run.source_commit
        or job.get("run_attempt") != run.run_attempt
        or job.get("status") != "completed"
        or job.get("conclusion") != "success"
    ):
        raise LatestMainError(
            f"{run.spec.file_name} required final job did not succeed"
        )


def wait_for_release_candidate(
    client: GitHubAPI,
    *,
    timeout_seconds: int = DEFAULT_CI_TIMEOUT_SECONDS,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> ReleaseCandidate:
    if timeout_seconds < 1 or timeout_seconds > 2 * 60 * 60:
        raise LatestMainError("Actions wait timeout is outside the supported range")
    deadline = monotonic() + timeout_seconds

    def remaining_budget() -> float:
        value = deadline - monotonic()
        if value <= 0:
            raise LatestMainError(
                "GitHub Actions did not finish within the configured wait budget"
            )
        return value

    commit = resolve_main_commit(client, timeout_seconds=remaining_budget())
    while True:
        runs: dict[str, WorkflowRun] = {}
        for spec in WORKFLOWS:
            run = _workflow_run(
                client,
                spec,
                commit,
                timeout_seconds=remaining_budget(),
            )
            if run is not None:
                runs[spec.file_name] = run
        if len(runs) == len(WORKFLOWS):
            for spec in WORKFLOWS:
                _require_final_job(
                    client,
                    runs[spec.file_name],
                    timeout_seconds=remaining_budget(),
                )
            latest = resolve_main_commit(client, timeout_seconds=remaining_budget())
            if latest == commit:
                return ReleaseCandidate(commit, runs)
            commit = latest
            continue
        wait_remaining = deadline - monotonic()
        if wait_remaining <= 0:
            pending = sorted(
                spec.file_name for spec in WORKFLOWS if spec.file_name not in runs
            )
            raise LatestMainError(
                "GitHub Actions did not finish successfully for current main: "
                + ", ".join(pending)
            )
        sleep(min(POLL_INTERVAL_SECONDS, wait_remaining))


def _ready_artifact_name(candidate: ReleaseCandidate) -> str:
    run = candidate.packaging_run
    return (
        f"release-image-evidence-{candidate.source_commit}-"
        f"{run.run_id}-{run.run_attempt}"
    )


def find_ready_artifact(
    client: GitHubAPI,
    candidate: ReleaseCandidate,
    *,
    timeout_seconds: float | None = None,
) -> ReadyArtifact | None:
    run = candidate.packaging_run
    expected_name = _ready_artifact_name(candidate)
    payload = _mapping(
        client.get_json(
            f"/repos/{REPOSITORY}/actions/runs/{run.run_id}/artifacts",
            query={"name": expected_name, "per_page": "10"},
            timeout_seconds=timeout_seconds,
        ),
        "ready artifact response",
    )
    artifacts = _array(payload.get("artifacts"), "ready artifacts")
    exact = [
        artifact
        for artifact in artifacts
        if isinstance(artifact, dict) and artifact.get("name") == expected_name
    ]
    if not exact:
        return None
    if len(exact) != 1:
        raise LatestMainError("ready release artifact is ambiguous")
    artifact = exact[0]
    workflow = _mapping(artifact.get("workflow_run"), "ready artifact workflow")
    size = _positive_int(artifact.get("size_in_bytes"), "ready artifact size")
    if (
        artifact.get("expired") is not False
        or workflow.get("id") != run.run_id
        or workflow.get("head_sha") != candidate.source_commit
        or workflow.get("head_branch") != "main"
        or size > ARCHIVE_MAX_BYTES
    ):
        raise LatestMainError("ready release artifact is invalid or expired")
    digest = artifact.get("digest")
    if not isinstance(digest, str) or DIGEST_RE.fullmatch(digest) is None:
        raise LatestMainError("ready release artifact digest is invalid")
    return ReadyArtifact(
        _positive_int(artifact.get("id"), "ready artifact id"),
        expected_name,
        size,
        digest,
    )


def wait_for_ready_artifact(
    client: GitHubAPI,
    candidate: ReleaseCandidate,
    *,
    timeout_seconds: int = ARTIFACT_APPEARANCE_TIMEOUT_SECONDS,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> ReadyArtifact:
    deadline = monotonic() + timeout_seconds
    while True:
        remaining = deadline - monotonic()
        if remaining <= 0:
            raise LatestMainError("ready release artifact did not appear")
        artifact = find_ready_artifact(
            client,
            candidate,
            timeout_seconds=remaining,
        )
        if artifact is not None:
            return artifact
        remaining = deadline - monotonic()
        if remaining <= 0:
            raise LatestMainError("ready release artifact did not appear")
        sleep(min(POLL_INTERVAL_SECONDS, remaining))


def _safe_archive_member(info: zipfile.ZipInfo) -> PurePosixPath:
    name = info.filename
    if (
        not name
        or "\x00" in name
        or "\\" in name
        or info.flag_bits & 0x1
        or info.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}
    ):
        raise LatestMainError("release artifact contains an unsafe archive member")
    pure = PurePosixPath(name.rstrip("/"))
    if (
        pure.is_absolute()
        or not pure.parts
        or len(pure.parts) != 1
        or any(part in {"", ".", ".."} or ":" in part for part in pure.parts)
    ):
        raise LatestMainError("release artifact contains an unsafe archive path")
    mode = info.external_attr >> 16
    kind = stat.S_IFMT(mode)
    if kind not in {0, stat.S_IFREG, stat.S_IFDIR}:
        raise LatestMainError("release artifact contains a link or special file")
    if info.file_size < 0 or info.file_size > ARCHIVE_MAX_FILE_BYTES:
        raise LatestMainError("release artifact member is too large")
    return pure


def extract_ready_artifact(archive: Path, destination: Path) -> None:
    try:
        metadata = archive.stat(follow_symlinks=False)
        if not stat.S_ISREG(metadata.st_mode) or archive.is_symlink():
            raise LatestMainError("release artifact archive is unsafe")
        with zipfile.ZipFile(archive) as bundle:
            entries = bundle.infolist()
            if not entries or len(entries) > ARCHIVE_MAX_FILES:
                raise LatestMainError("release artifact file count is invalid")
            normalized: set[str] = set()
            total = 0
            for info in entries:
                relative = _safe_archive_member(info)
                folded = relative.as_posix().casefold()
                if folded in normalized:
                    raise LatestMainError("release artifact contains duplicate paths")
                normalized.add(folded)
                total += info.file_size
                if total > ARCHIVE_MAX_EXTRACTED_BYTES:
                    raise LatestMainError(
                        "release artifact expands beyond its size limit"
                    )
            destination.mkdir(mode=0o700, parents=False, exist_ok=False)
            for info in entries:
                relative = _safe_archive_member(info)
                target = destination / relative.as_posix()
                if info.is_dir():
                    target.mkdir(mode=0o700, exist_ok=False)
                    continue
                descriptor = os.open(
                    target,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
                    0o600,
                )
                actual = 0
                try:
                    with bundle.open(info, "r") as source:
                        while True:
                            chunk = source.read(64 * 1024)
                            if not chunk:
                                break
                            actual += len(chunk)
                            if actual > info.file_size:
                                raise LatestMainError(
                                    "release artifact member size changed"
                                )
                            view = memoryview(chunk)
                            while view:
                                written = os.write(descriptor, view)
                                if written <= 0:
                                    raise OSError("short artifact member write")
                                view = view[written:]
                    if actual != info.file_size:
                        raise LatestMainError("release artifact member size changed")
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
    except (OSError, zipfile.BadZipFile, zipfile.LargeZipFile) as exc:
        raise LatestMainError("release artifact archive is invalid") from exc


def _verify_manifest_command(checkout: Path, evidence_root: Path) -> None:
    bootstrap = (
        "import runpy,sys; "
        "sys.path.insert(0,sys.argv.pop(1)); "
        "runpy.run_module('tools.release_image_manifest',run_name='__main__')"
    )
    command = [
        sys.executable,
        "-I",
        "-c",
        bootstrap,
        str(checkout),
        "verify",
        "--manifest",
        str(evidence_root / MANIFEST_NAME),
        "--evidence-root",
        str(evidence_root),
        "--expected-role",
        "backend",
        "--expected-role",
        "frontend",
    ]
    environment = {
        key: os.environ[key] for key in ("PATH", "LANG", "LC_ALL") if key in os.environ
    }
    try:
        result = subprocess.run(
            command,
            cwd=checkout,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=120,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        raise LatestMainError("release image manifest verifier could not run") from None
    if result.returncode != 0:
        raise LatestMainError("release image manifest verification failed")


def validate_release_manifest(
    checkout: Path,
    evidence_root: Path,
    candidate: ReleaseCandidate,
    *,
    verify: Callable[[Path, Path], None] = _verify_manifest_command,
) -> tuple[str, str]:
    manifest_path = evidence_root / MANIFEST_NAME
    try:
        metadata = manifest_path.stat(follow_symlinks=False)
        if not stat.S_ISREG(metadata.st_mode) or manifest_path.is_symlink():
            raise LatestMainError("ready release manifest is missing or unsafe")
        manifest = _mapping(
            _loads_json(manifest_path.read_bytes(), "ready release manifest"),
            "ready release manifest",
        )
    except OSError as exc:
        raise LatestMainError("ready release manifest is missing or unsafe") from exc
    verify(checkout, evidence_root)
    run = candidate.packaging_run
    workflow = _mapping(manifest.get("workflow"), "ready release workflow")
    if (
        manifest.get("source_commit") != candidate.source_commit
        or manifest.get("repository") != REPOSITORY_URL
        or workflow.get("repository") != REPOSITORY
        or workflow.get("workflow_ref") != MANIFEST_WORKFLOW_REF
        or workflow.get("run_id") != str(run.run_id)
        or workflow.get("run_attempt") != run.run_attempt
        or workflow.get("head_sha") != candidate.source_commit
    ):
        raise LatestMainError("ready release manifest is not bound to the selected run")
    subjects = _array(manifest.get("subjects"), "ready release subjects")
    by_role: dict[str, str] = {}
    for subject in subjects:
        value = _mapping(subject, "ready release subject")
        role = value.get("role")
        image = _mapping(value.get("image"), "ready release image")
        immutable_ref = image.get("immutable_ref")
        if (
            not isinstance(role, str)
            or not isinstance(immutable_ref, str)
            or role in by_role
        ):
            raise LatestMainError("ready release subjects are invalid")
        by_role[role] = immutable_ref
    if set(by_role) != {"backend", "frontend"}:
        raise LatestMainError("ready release subjects are incomplete")
    subject = sandbox_quickstart.Subject(
        candidate.source_commit,
        by_role["backend"],
        by_role["frontend"],
    )
    for image, repository in (
        (subject.backend_image, sandbox_quickstart.BACKEND_REPOSITORY),
        (subject.frontend_image, sandbox_quickstart.FRONTEND_REPOSITORY),
    ):
        match = sandbox_quickstart.DIGEST_REF.fullmatch(image)
        if match is None or match.group("repository") != repository:
            raise LatestMainError(
                "ready release images are not role-bound immutable digests"
            )
    return subject.backend_image, subject.frontend_image


def _validate_managed_root(root: Path) -> tuple[Path, os.stat_result]:
    supplied = Path(root)
    try:
        normalized = supplied.resolve(strict=True)
        metadata = supplied.stat(follow_symlinks=False)
    except OSError as exc:
        raise LatestMainError("managed quickstart root is missing or unsafe") from exc
    if (
        not supplied.is_absolute()
        or supplied != normalized
        or supplied.is_symlink()
        or not stat.S_ISDIR(metadata.st_mode)
        or os.name == "posix"
        and stat.S_IMODE(metadata.st_mode) & 0o022
    ):
        raise LatestMainError("managed quickstart root is missing or unsafe")
    return normalized, metadata


def _ensure_incoming(root: Path, owner: int) -> Path:
    incoming = root / "incoming"
    try:
        if not incoming.exists():
            incoming.mkdir(mode=0o700)
        metadata = incoming.stat(follow_symlinks=False)
        if (
            incoming.resolve(strict=True) != incoming
            or incoming.is_symlink()
            or not stat.S_ISDIR(metadata.st_mode)
            or os.name == "posix"
            and (metadata.st_uid != owner or stat.S_IMODE(metadata.st_mode) & 0o022)
        ):
            raise LatestMainError("managed incoming directory is unsafe")
    except OSError as exc:
        raise LatestMainError("managed incoming directory is unsafe") from exc
    return incoming


@contextmanager
def deployment_lock(root: Path) -> Iterator[None]:
    normalized, root_metadata = _validate_managed_root(root)
    lock_path = normalized / ".quickstart.lock"
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(lock_path, flags, 0o600)
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or os.name == "posix"
            and (
                metadata.st_uid != root_metadata.st_uid
                or stat.S_IMODE(metadata.st_mode) != 0o600
            )
        ):
            raise LatestMainError("managed deployment lock is unsafe")
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise LatestMainError(
                "another quickstart deployment is already running"
            ) from None
        yield
    finally:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


def resolve_managed_env(root: Path, explicit: Path | None) -> Path:
    subject_path = root / "incoming" / "latest-main.json"
    if explicit is not None:
        selected = explicit
    elif subject_path.exists() or subject_path.is_symlink():
        selected = sandbox_quickstart._load_subject(subject_path, root).env_file
    else:
        selected = None
    if selected is None:
        raise LatestMainError(
            "first latest-main deployment requires --env-file or AI_PLATFORM_QUICKSTART_ENV_FILE"
        )
    try:
        return sandbox_quickstart.Quickstart(root, root)._validate_env(Path(selected))
    except sandbox_quickstart.QuickstartError as exc:
        raise LatestMainError(
            "managed quickstart environment is missing or unsafe"
        ) from exc


def _atomic_write_subject(
    root: Path,
    *,
    commit: str,
    backend_image: str,
    frontend_image: str,
    env_file: Path,
) -> Path:
    normalized, root_metadata = _validate_managed_root(root)
    incoming = _ensure_incoming(normalized, root_metadata.st_uid)
    destination = incoming / "latest-main.json"
    payload = (
        json.dumps(
            {
                "source_commit": commit,
                "backend_image": backend_image,
                "frontend_image": frontend_image,
                "env_file": str(env_file),
                "ci_success": True,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=".latest-main.json.incoming-", dir=incoming
    )
    temporary = Path(temporary_name)
    try:
        try:
            view = memoryview(payload)
            while view:
                written = os.write(descriptor, view)
                if written <= 0:
                    raise OSError("short subject write")
                view = view[written:]
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        sandbox_quickstart._load_subject(temporary)
        os.replace(temporary, destination)
        directory = os.open(incoming, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    return destination


def _run_target_quickstart(checkout: Path) -> None:
    script = checkout / "tools" / "sandbox_quickstart.py"
    try:
        metadata = script.stat(follow_symlinks=False)
    except OSError as exc:
        raise LatestMainError("target quickstart is missing") from exc
    if not stat.S_ISREG(metadata.st_mode) or script.is_symlink():
        raise LatestMainError("target quickstart is unsafe")
    environment = dict(os.environ)
    for key in (*TOKEN_VARIABLES, ENV_PATH_VARIABLE):
        environment.pop(key, None)
    try:
        result = subprocess.run(
            [sys.executable, "-I", str(script)],
            cwd=checkout,
            env=environment,
            stdin=subprocess.DEVNULL,
            timeout=60 * 60,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        raise LatestMainError("target quickstart command failed") from None
    if result.returncode != 0:
        raise LatestMainError("target quickstart did not complete successfully")


def deploy_latest_main(
    *,
    root: Path,
    client: GitHubAPI,
    env_file: Path | None = None,
    ci_timeout_seconds: int = DEFAULT_CI_TIMEOUT_SECONDS,
    materialize: Callable[
        [Path, str], Path
    ] = release_authority.materialize_main_checkout,
    verify_manifest: Callable[[Path, Path], None] = _verify_manifest_command,
    deploy: Callable[[Path], None] = _run_target_quickstart,
) -> sandbox_quickstart.Subject:
    normalized, root_metadata = _validate_managed_root(root)
    incoming = _ensure_incoming(normalized, root_metadata.st_uid)
    selected_env = resolve_managed_env(normalized, env_file)
    candidate = wait_for_release_candidate(client, timeout_seconds=ci_timeout_seconds)
    artifact = wait_for_ready_artifact(client, candidate)
    with tempfile.TemporaryDirectory(
        prefix=".latest-main-", dir=incoming
    ) as temporary_name:
        temporary = Path(temporary_name)
        archive = temporary / "release-evidence.zip"
        downloaded_digest = client.download_artifact(artifact.artifact_id, archive)
        if artifact.digest != f"sha256:{downloaded_digest}":
            raise LatestMainError(
                "downloaded release artifact digest does not match GitHub"
            )
        evidence_root = temporary / "evidence"
        extract_ready_artifact(archive, evidence_root)
        checkout = materialize(normalized / "releases", candidate.source_commit)
        backend_image, frontend_image = validate_release_manifest(
            checkout,
            evidence_root,
            candidate,
            verify=verify_manifest,
        )
    if resolve_main_commit(client) != candidate.source_commit:
        raise LatestMainError(
            "main advanced while the release candidate was being prepared"
        )
    _atomic_write_subject(
        normalized,
        commit=candidate.source_commit,
        backend_image=backend_image,
        frontend_image=frontend_image,
        env_file=selected_env,
    )
    deploy(checkout)
    return sandbox_quickstart.Subject(
        candidate.source_commit,
        backend_image,
        frontend_image,
        selected_env,
    )


def _interrupt(*_args: object) -> None:
    raise KeyboardInterrupt


def _retry_approved_subject(root: Path) -> sandbox_quickstart.Subject:
    subject = sandbox_quickstart._load_subject(
        root / "incoming" / "latest-main.json",
        root,
    )
    checkout = root / "releases" / subject.commit
    return sandbox_quickstart.Quickstart(checkout, root).run()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Deploy a controller-prepared or latest fully approved main image subject."
    )
    parser.add_argument(
        "--latest",
        action="store_true",
        help="wait for exact-main Actions evidence, resolve image digests, and deploy",
    )
    parser.add_argument(
        "--env-file",
        type=Path,
        help="first-deployment managed env path; later deployments reuse the approved subject path",
    )
    parser.add_argument(
        "--ci-timeout-seconds",
        type=int,
        default=DEFAULT_CI_TIMEOUT_SECONDS,
        help=argparse.SUPPRESS,
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if not args.latest and (
        args.env_file is not None
        or args.ci_timeout_seconds != DEFAULT_CI_TIMEOUT_SECONDS
    ):
        print("quickstart: failed: --env-file and CI timeout require --latest")
        return 2
    root = sandbox_quickstart.MANAGED_ROOT
    previous_handlers = {
        signum: signal.signal(signum, _interrupt)
        for signum in (signal.SIGINT, signal.SIGTERM)
    }
    try:
        with deployment_lock(root):
            if args.latest:
                selected_env = args.env_file
                if selected_env is None and os.environ.get(ENV_PATH_VARIABLE):
                    selected_env = Path(os.environ.pop(ENV_PATH_VARIABLE))
                token = _claim_github_token(os.environ)
                client = GitHubClient(token)
                deploy_latest_main(
                    root=root,
                    client=client,
                    env_file=selected_env,
                    ci_timeout_seconds=args.ci_timeout_seconds,
                )
            else:
                _retry_approved_subject(root)
    except (
        LatestMainError,
        release_authority.ReleaseAuthorityError,
        sandbox_quickstart.QuickstartError,
    ) as exc:
        print(f"quickstart: failed: {exc} (no data volumes were removed)")
        return 2
    except (OSError, subprocess.SubprocessError, KeyboardInterrupt):
        print("quickstart: failed: command error (no data volumes were removed)")
        return 2
    finally:
        for signum, handler in previous_handlers.items():
            signal.signal(signum, handler)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
