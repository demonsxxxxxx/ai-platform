"""Resolve and deploy the latest qualified deployment Release."""

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
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


if __name__ == "__main__" and not sys.flags.isolated:
    raise SystemExit("run latest-main quickstart through the approved host wrapper")

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools import release_authority  # noqa: E402
from tools import release_image_manifest  # noqa: E402
from tools import sandbox_quickstart  # noqa: E402


REPOSITORY = "demonsxxxxxx/ai-platform"
REPOSITORY_URL = f"https://github.com/{REPOSITORY}.git"
API_ROOT = "https://api.github.com"
API_VERSION = "2022-11-28"
MANIFEST_NAME = "release-image-manifest.json"
TOKEN_VARIABLES = ("GH_TOKEN", "GITHUB_TOKEN")
ENV_PATH_VARIABLE = "AI_PLATFORM_QUICKSTART_ENV_FILE"
DEPLOYMENT_RELEASE_TAG_RE = re.compile(
    r"deployment-(?P<commit>[0-9a-f]{40})-(?P<run_id>[1-9][0-9]*)-"
    r"(?P<run_attempt>[1-9][0-9]*)\Z"
)
DEPLOYMENT_RELEASE_ASSET_NAME = MANIFEST_NAME
DEPLOYMENT_RELEASE_ASSET_LABEL_PREFIX = "release-image-manifest"
DEPLOYMENT_RELEASE_UPLOADER = "github-actions[bot]"
DEPLOYMENT_RELEASE_ASSET_URL_RE = re.compile(
    rf"https://github\.com/{re.escape(REPOSITORY)}/releases/download/"
    rf"deployment-[0-9a-f]{{40}}-[1-9][0-9]*-[1-9][0-9]*/"
    rf"{re.escape(DEPLOYMENT_RELEASE_ASSET_NAME)}\Z"
)
API_RESPONSE_MAX_BYTES = 4 * 1024 * 1024
MANIFEST_MAX_BYTES = 4 * 1024 * 1024
HTTP_TIMEOUT_SECONDS = 30
HTTP_ATTEMPTS = 3
DOWNLOAD_ATTEMPT_TIMEOUT_SECONDS = 125
CURL_DOWNLOAD_TIMEOUT_SECONDS = 120
COMMIT_RE = re.compile(r"[0-9a-f]{40}\Z")
DIGEST_RE = re.compile(r"sha256:[0-9a-f]{64}\Z")


class LatestMainError(RuntimeError):
    """A bounded latest-main admission or deployment failure."""


class _GitHubNotFoundError(LatestMainError):
    pass


class _DuplicateJsonKey(ValueError):
    pass


@dataclass(frozen=True)
class ReleaseAsset:
    name: str
    label: str
    size_bytes: int
    digest: str
    download_url: str


@dataclass(frozen=True)
class DeploymentRelease:
    tag: str
    source_commit: str
    run_id: int
    run_attempt: int
    asset: ReleaseAsset


def _release_asset_url(tag: str) -> str:
    return (
        f"https://github.com/{REPOSITORY}/releases/download/"
        f"{tag}/{DEPLOYMENT_RELEASE_ASSET_NAME}"
    )


def _release_asset_label(commit: str, run_id: int, run_attempt: int) -> str:
    return f"{DEPLOYMENT_RELEASE_ASSET_LABEL_PREFIX}-{commit}-{run_id}-{run_attempt}"


class GitHubAPI(Protocol):
    def get_json(
        self,
        path: str,
        *,
        query: Mapping[str, str] | None = None,
        timeout_seconds: float | None = None,
    ) -> Any: ...

    def download_public_asset(self, url: str, destination: Path) -> str: ...


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
    return lowered in {
        "api.github.com",
        "github.com",
        "release-assets.githubusercontent.com",
    }


class _ReleaseRedirectHandler(HTTPRedirectHandler):
    """Permit only trusted GitHub Release asset redirects."""

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
        *,
        opener: Any | None = None,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._opener = opener or build_opener(_ReleaseRedirectHandler())
        self._redirect_opener = build_opener(_NoRedirectHandler())
        self._curl_path = shutil.which("curl")
        self._curl_run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run
        self._sleep = sleep
        self._monotonic = monotonic

    def _github_request(self, url: str) -> Request:
        return Request(
            url,
            headers={
                "Accept": "application/vnd.github+json",
                "User-Agent": "ai-platform-deployment-release-quickstart",
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
            request = self._github_request(url)
            try:
                with _wall_timeout(wall_timeout):
                    with self._opener.open(request, timeout=socket_timeout) as response:
                        return _read_bounded_response(response, max_bytes)
            except HTTPError as exc:
                if exc.code == 404:
                    exc.close()
                    raise _GitHubNotFoundError(
                        "GitHub API resource was not found"
                    ) from None
                if exc.code in retryable_statuses and attempt + 1 < HTTP_ATTEMPTS:
                    self._bounded_retry_sleep(attempt + 1, deadline)
                    continue
                if exc.code in {401, 403}:
                    raise LatestMainError(
                        "anonymous GitHub API access was rejected or rate-limited"
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

    def download_public_asset(self, url: str, destination: Path) -> str:
        if DEPLOYMENT_RELEASE_ASSET_URL_RE.fullmatch(url) is None:
            raise LatestMainError("deployment Release asset URL is invalid")
        if destination.exists() or destination.is_symlink():
            raise LatestMainError("manifest destination is not empty")
        if self._curl_path is not None:
            return self._download_public_asset_with_curl(url, destination)
        return self._download_public_asset_with_urllib(url, destination)

    def _resolve_public_asset_download_url(self, url: str) -> str:
        retryable_statuses = {408, 429, 500, 502, 503, 504}
        for attempt in range(HTTP_ATTEMPTS):
            try:
                with _wall_timeout(HTTP_TIMEOUT_SECONDS + 5):
                    response = self._redirect_opener.open(
                        self._github_request(url), timeout=HTTP_TIMEOUT_SECONDS
                    )
                response.close()
                raise LatestMainError("GitHub release asset download did not redirect")
            except HTTPError as exc:
                if exc.code in {301, 302, 303, 307, 308}:
                    location = exc.headers.get("Location")
                    exc.close()
                    target = urlsplit(location or "")
                    if target.scheme != "https" or not _trusted_download_host(
                        target.hostname
                    ):
                        raise LatestMainError(
                            "GitHub release asset redirect is invalid"
                        )
                    return location
                if exc.code in retryable_statuses and attempt + 1 < HTTP_ATTEMPTS:
                    exc.close()
                    self._sleep(float(attempt + 1))
                    continue
                code = exc.code
                exc.close()
                raise LatestMainError(
                    f"GitHub release asset URL request failed with status {code}"
                ) from None
            except LatestMainError:
                raise
            except (HTTPException, IncompleteRead, OSError, TimeoutError, URLError):
                if attempt + 1 < HTTP_ATTEMPTS:
                    self._sleep(float(attempt + 1))
                    continue
                raise LatestMainError(
                    "GitHub release asset URL request failed"
                ) from None
        raise LatestMainError("GitHub release asset URL request failed")

    def _download_public_asset_with_curl(
        self, url: str, destination: Path
    ) -> str:
        if self._curl_path is None:
            raise LatestMainError("curl manifest downloader is unavailable")
        destination.parent.mkdir(parents=True, exist_ok=True)
        for attempt in range(HTTP_ATTEMPTS):
            signed_url = self._resolve_public_asset_download_url(url)
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
                        or metadata.st_size > MANIFEST_MAX_BYTES
                    ):
                        raise LatestMainError(
                            "downloaded deployment manifest is missing or unsafe"
                        )
                    destination.chmod(0o600)
                    return _sha256_file(destination, MANIFEST_MAX_BYTES)
                except OSError:
                    pass
            if attempt + 1 < HTTP_ATTEMPTS:
                self._sleep(float(attempt + 1))
            else:
                destination.unlink(missing_ok=True)
        raise LatestMainError("GitHub Release manifest download failed")

    def _download_public_asset_with_urllib(
        self, url: str, destination: Path
    ) -> str:
        destination.parent.mkdir(parents=True, exist_ok=True)
        retryable_statuses = {408, 429, 500, 502, 503, 504}
        for attempt in range(HTTP_ATTEMPTS):
            descriptor: int | None = None
            complete = False
            try:
                with _wall_timeout(DOWNLOAD_ATTEMPT_TIMEOUT_SECONDS):
                    with self._opener.open(
                        self._github_request(url), timeout=HTTP_TIMEOUT_SECONDS
                    ) as response:
                        _validate_response(response, MANIFEST_MAX_BYTES)
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
                            if total > MANIFEST_MAX_BYTES:
                                raise LatestMainError("GitHub response is too large")
                            digest.update(chunk)
                            view = memoryview(chunk)
                            while view:
                                written = os.write(descriptor, view)
                                if written <= 0:
                                    raise OSError("short evidence write")
                                view = view[written:]
                        os.fsync(descriptor)
                        complete = True
                        return digest.hexdigest()
            except HTTPError as exc:
                if exc.code in retryable_statuses and attempt + 1 < HTTP_ATTEMPTS:
                    self._sleep(float(attempt + 1))
                    continue
                raise LatestMainError(
                    f"GitHub Release manifest download failed with status {exc.code}"
                ) from None
            except LatestMainError:
                raise
            except (HTTPException, IncompleteRead, OSError, TimeoutError, URLError):
                if attempt + 1 < HTTP_ATTEMPTS:
                    self._sleep(float(attempt + 1))
                    continue
                raise LatestMainError(
                    "GitHub Release manifest download failed"
                ) from None
            finally:
                if descriptor is not None:
                    os.close(descriptor)
                if descriptor is not None and not complete:
                    destination.unlink(missing_ok=True)
        raise LatestMainError("GitHub Release manifest download failed")


def _curl_escape(value: str) -> str:
    if "\r" in value or "\n" in value or "\x00" in value:
        raise LatestMainError("curl download input is invalid")
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _curl_download_config(signed_url: str, destination: Path) -> str:
    target = urlsplit(signed_url)
    if target.scheme != "https" or not _trusted_download_host(target.hostname):
        raise LatestMainError("GitHub manifest download URL is invalid")
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
            f"max-filesize = {MANIFEST_MAX_BYTES}",
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


def _drop_github_tokens(environment: MutableMapping[str, str]) -> None:
    for key in TOKEN_VARIABLES:
        environment.pop(key, None)


def _deployment_release(payload: Mapping[str, Any]) -> DeploymentRelease:
    tag = payload.get("tag_name")
    match = DEPLOYMENT_RELEASE_TAG_RE.fullmatch(tag) if isinstance(tag, str) else None
    if match is None:
        raise LatestMainError("GitHub Release is not a deployment Release")
    commit = match.group("commit")
    run_id = int(match.group("run_id"))
    run_attempt = int(match.group("run_attempt"))
    author = _mapping(payload.get("author"), "deployment Release author")
    if (
        payload.get("target_commitish") != commit
        or payload.get("draft") is not False
        or payload.get("prerelease") is not False
        or payload.get("immutable") is not True
        or not isinstance(payload.get("published_at"), str)
        or author.get("login") != DEPLOYMENT_RELEASE_UPLOADER
    ):
        raise LatestMainError("deployment Release metadata is invalid")
    assets = _array(payload.get("assets"), "deployment Release assets")
    exact = [
        asset
        for asset in assets
        if isinstance(asset, dict)
        and asset.get("name") == DEPLOYMENT_RELEASE_ASSET_NAME
    ]
    if len(exact) != 1:
        raise LatestMainError("deployment Release manifest is missing or ambiguous")
    asset = exact[0]
    size = _positive_int(asset.get("size"), "deployment Release manifest size")
    uploader = _mapping(asset.get("uploader"), "deployment Release asset uploader")
    expected_url = _release_asset_url(tag)
    if (
        asset.get("label") != _release_asset_label(commit, run_id, run_attempt)
        or asset.get("state") != "uploaded"
        or uploader.get("login") != DEPLOYMENT_RELEASE_UPLOADER
        or size > MANIFEST_MAX_BYTES
        or asset.get("browser_download_url") != expected_url
    ):
        raise LatestMainError("deployment Release manifest asset is invalid")
    digest = asset.get("digest")
    if not isinstance(digest, str) or DIGEST_RE.fullmatch(digest) is None:
        raise LatestMainError("deployment Release manifest digest is invalid")
    return DeploymentRelease(
        tag,
        commit,
        run_id,
        run_attempt,
        ReleaseAsset(
            DEPLOYMENT_RELEASE_ASSET_NAME,
            _release_asset_label(commit, run_id, run_attempt),
            size,
            digest,
            expected_url,
        ),
    )


def resolve_deployment_release(client: GitHubAPI) -> DeploymentRelease:
    releases = _array(
        client.get_json(
            f"/repos/{REPOSITORY}/releases",
            query={"per_page": "100"},
        ),
        "deployment Releases response",
    )
    for release in releases:
        if (
            isinstance(release, dict)
            and isinstance(release.get("tag_name"), str)
            and DEPLOYMENT_RELEASE_TAG_RE.fullmatch(release["tag_name"]) is not None
            and release.get("draft") is False
            and release.get("prerelease") is False
            and release.get("immutable") is True
        ):
            return _deployment_release(release)
    raise LatestMainError("no qualified immutable deployment Release is available")


def validate_release_manifest(
    manifest_path: Path,
    release: DeploymentRelease,
) -> tuple[str, str]:
    try:
        metadata = manifest_path.stat(follow_symlinks=False)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or manifest_path.is_symlink()
            or metadata.st_size < 1
            or metadata.st_size > MANIFEST_MAX_BYTES
        ):
            raise LatestMainError("deployment Release manifest is missing or unsafe")
        raw_manifest = _loads_json(
            manifest_path.read_bytes(), "deployment Release manifest"
        )
    except OSError as exc:
        raise LatestMainError("deployment Release manifest is missing or unsafe") from exc
    try:
        manifest = release_image_manifest.validate_manifest(raw_manifest)
    except ValueError as exc:
        raise LatestMainError("deployment Release manifest is invalid") from exc
    workflow = _mapping(manifest["workflow"], "deployment Release workflow")
    if (
        manifest["source_commit"] != release.source_commit
        or workflow["run_id"] != str(release.run_id)
        or workflow["run_attempt"] != release.run_attempt
    ):
        raise LatestMainError("deployment Release manifest binding is invalid")
    images = {
        subject["role"]: subject["image"]["immutable_ref"]
        for subject in manifest["subjects"]
    }
    return images["backend"], images["frontend"]


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
    _drop_github_tokens(environment)
    environment.pop(ENV_PATH_VARIABLE, None)
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


def deploy_latest_release(
    *,
    root: Path,
    client: GitHubAPI,
    env_file: Path | None = None,
    materialize: Callable[
        [Path, str], Path
    ] = release_authority.materialize_main_checkout,
    deploy: Callable[[Path], None] = _run_target_quickstart,
) -> sandbox_quickstart.Subject:
    normalized, root_metadata = _validate_managed_root(root)
    incoming = _ensure_incoming(normalized, root_metadata.st_uid)
    selected_env = resolve_managed_env(normalized, env_file)
    release = resolve_deployment_release(client)
    with tempfile.TemporaryDirectory(
        prefix=".deployment-release-", dir=incoming
    ) as temporary_name:
        manifest_path = Path(temporary_name) / MANIFEST_NAME
        downloaded_digest = client.download_public_asset(
            release.asset.download_url, manifest_path
        )
        if release.asset.digest != f"sha256:{downloaded_digest}":
            raise LatestMainError(
                "downloaded deployment manifest digest does not match GitHub"
            )
        backend_image, frontend_image = validate_release_manifest(
            manifest_path,
            release,
        )
        checkout = materialize(normalized / "releases", release.source_commit)
    _atomic_write_subject(
        normalized,
        commit=release.source_commit,
        backend_image=backend_image,
        frontend_image=frontend_image,
        env_file=selected_env,
    )
    deploy(checkout)
    return sandbox_quickstart.Subject(
        release.source_commit,
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
        description="Deploy a controller-prepared or latest qualified deployment Release."
    )
    parser.add_argument(
        "--latest",
        action="store_true",
        help="resolve the latest qualified deployment Release and deploy exact image digests",
    )
    parser.add_argument(
        "--env-file",
        type=Path,
        help="first-deployment managed env path; later deployments reuse the approved subject path",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if not args.latest and args.env_file is not None:
        print("quickstart: failed: --env-file requires --latest")
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
                _drop_github_tokens(os.environ)
                client = GitHubClient()
                deploy_latest_release(
                    root=root,
                    client=client,
                    env_file=selected_env,
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
