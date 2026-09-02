"""Deploy a controller-approved main subject to an OpenSandbox internal-test stack."""

from __future__ import annotations

import sys

if __name__ == "__main__" and not sys.flags.isolated:
    raise SystemExit("run sandbox quickstart through an approved host wrapper")

from dataclasses import dataclass
from http.client import HTTPException
from importlib.util import module_from_spec, spec_from_file_location
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import signal
import stat
import subprocess
import time
from typing import Any, Sequence
from urllib.request import urlopen

if __package__:
    from tools.release_parity_convergence import compose_identity_mismatches
else:
    _parity_path = Path(__file__).with_name("release_parity_convergence.py")
    _parity_spec = spec_from_file_location("release_parity_convergence", _parity_path)
    if _parity_spec is None or _parity_spec.loader is None:
        raise ImportError(f"cannot load release parity helper from {_parity_path}")
    _parity_module = module_from_spec(_parity_spec)
    _parity_spec.loader.exec_module(_parity_module)
    compose_identity_mismatches = _parity_module.compose_identity_mismatches


MANAGED_ROOT = Path("/data/ai-platform-internal-test")
PROJECT = "ai-platform-internal"
COMPOSE_FILES = (
    Path("deploy/ai-platform/docker-compose.yml"),
    Path("deploy/ai-platform/docker-compose.opensandbox-internal-test.yml"),
)
BACKEND_REPOSITORY = "ghcr.io/demonsxxxxxx/ai-platform-backend"
FRONTEND_REPOSITORY = "ghcr.io/demonsxxxxxx/ai-platform-frontend"
ORIGIN_URL = "https://github.com/demonsxxxxxx/ai-platform.git"
OPENSANDBOX_HEALTH_PROBE = (
    "import os, sys, urllib.request; "
    "base_url = os.environ.get('OPENSANDBOX_BASE_URL', '').strip() or "
    "os.environ['OPENSANDBOX_PROTOCOL'] + '://' + os.environ['OPENSANDBOX_DOMAIN']; "
    "url = base_url.rstrip('/') + '/health'; "
    "opener = urllib.request.build_opener(urllib.request.ProxyHandler({})); "
    "response = opener.open(url, timeout=10); "
    "raise SystemExit(0 if response.status == 200 and len(response.read(65537)) <= 65536 else 1)"
)
WORKER_RUNTIME_HEARTBEAT_PROBE = """
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import time

expected_commit = sys.argv[1]
not_before = float(sys.argv[2])
path = Path(os.environ.get("TMPDIR") or "/tmp") / "ai-platform-worker-runtime-heartbeat.json"
payload = json.loads(path.read_text(encoding="utf-8"))
if set(payload) != {"schema_version", "worker_id", "runtime_commit", "pid", "observed_at"}:
    raise SystemExit(2)
pid = payload["pid"]
worker_id = payload["worker_id"]
observed = datetime.fromisoformat(payload["observed_at"])
if (
    payload["schema_version"] != "ai-platform.worker-runtime-heartbeat.v1"
    or not isinstance(worker_id, str)
    or not worker_id.strip()
    or payload["runtime_commit"] != expected_commit
    or isinstance(pid, bool)
    or not isinstance(pid, int)
    or pid <= 0
    or observed.tzinfo is None
):
    raise SystemExit(2)
observed_at = observed.astimezone(timezone.utc).timestamp()
now = time.time()
if observed_at < not_before or observed_at > now + 5 or now - observed_at > 30:
    raise SystemExit(2)
os.kill(pid, 0)
print(json.dumps({
    "status": "ok",
    "worker_id": worker_id,
    "pid": pid,
    "observed_at": observed_at,
}, sort_keys=True))
"""
COMMIT = re.compile(r"[0-9a-f]{40}\Z")
CONTAINER_ID = re.compile(r"[0-9a-f]{64}\Z")
DIGEST_REF = re.compile(r"(?P<repository>[^@]+)@sha256:[0-9a-f]{64}\Z")
SERVICES = ("api", "worker", "frontend", "postgres", "redis", "minio")
PERSISTENT_SERVICES = ("postgres", "redis", "minio")
PROJECT_SERVICES = (*SERVICES, "workspace-init", "migrate")
ROLLBACK_QUEUE_KEY_PREFIX = "ai-platform:runs"
ROLLBACK_PROCESSING_META_KEY = f"{ROLLBACK_QUEUE_KEY_PREFIX}:processing-meta"
ROLLBACK_RETRY_META_KEY = f"{ROLLBACK_QUEUE_KEY_PREFIX}:retry-meta"
ROLLBACK_LEASE_SCAN_LIMIT = 10_000
ROLLBACK_PROTOCOL_V2_LEASE_PROBE = """
-- ai-platform:rollback-protocol-v2-lease-probe:v1
local scan_limit = tonumber(ARGV[1])
if not scan_limit or scan_limit < 1 then
  return cjson.encode({status = "unproven", reason = "invalid_scan_limit"})
end

local seen = {}
local processing_count = 0
local retry_count = 0

for key_index, metadata_key in ipairs(KEYS) do
  if redis.call("hlen", metadata_key) > scan_limit then
    return cjson.encode({status = "unproven", reason = "scan_limit_exceeded"})
  end
  local entries = redis.call("hgetall", metadata_key)
  for index = 1, #entries, 2 do
    local message_id = tostring(entries[index] or "")
    local ok, metadata = pcall(cjson.decode, entries[index + 1] or "")
    if not ok or type(metadata) ~= "table" then
      return cjson.encode({status = "unproven", reason = "invalid_metadata"})
    end
    local raw_protocol = metadata["lease_protocol_version"]
    local protocol = tonumber(raw_protocol)
    if raw_protocol ~= nil and (not protocol or protocol ~= math.floor(protocol)) then
      return cjson.encode({status = "unproven", reason = "invalid_protocol"})
    end
    if metadata["owner_token_v2"] ~= nil and (not protocol or protocol < 2) then
      return cjson.encode({status = "unproven", reason = "unversioned_v2_owner"})
    end
    if protocol and protocol >= 2 then
      local owner_token = tostring(metadata["owner_token_v2"] or "")
      local attempt_id = tostring(metadata["attempt_id"] or "")
      if tostring(metadata["message_id"] or "") ~= message_id
        or #message_id ~= 64 or not string.match(message_id, "^[0-9a-f]+$")
        or #owner_token ~= 69 or not string.match(owner_token, "^qown_[0-9a-f]+$")
        or #attempt_id ~= 68 or not string.match(attempt_id, "^qat_[0-9a-f]+$") then
        return cjson.encode({status = "unproven", reason = "invalid_v2_identity"})
      end
      seen[message_id] = true
      if key_index == 1 then
        processing_count = processing_count + 1
      else
        retry_count = retry_count + 1
      end
    end
  end
end

local total = 0
for _ in pairs(seen) do
  total = total + 1
end
return cjson.encode({
  status = "ok",
  processing = processing_count,
  retry = retry_count,
  total = total,
})
"""


class QuickstartError(RuntimeError): ...


class RollbackBlockedError(QuickstartError): ...


class RollbackInterruptedError(QuickstartError): ...


@dataclass(frozen=True)
class Subject:
    commit: str
    backend_image: str
    frontend_image: str
    env_file: Path | None = None
    persistent_compose_config: str = ""

    @property
    def executor_image(self) -> str:
        return self.backend_image

    @property
    def executor_image_digest(self) -> str:
        return self.executor_image.rsplit("@", 1)[1]


@dataclass(frozen=True)
class RuntimeContainer:
    container_id: str
    commit: str
    image: str
    restart_count: int
    status: str
    health: str
    project: str
    service: str
    config_files: str
    release_owner: str
    release_role: str
    source_dirty: str
    working_dir: str
    one_off: str
    config_hash: str

    @property
    def compose_labels(self) -> dict[str, str]:
        return {
            "ai-platform.release-owner": self.release_owner,
            "ai-platform.release-role": self.release_role,
            "com.docker.compose.project.working_dir": self.working_dir,
            "com.docker.compose.project.config_files": self.config_files,
            "com.docker.compose.project": self.project,
            "com.docker.compose.service": self.service,
            "com.docker.compose.oneoff": self.one_off,
            "com.docker.compose.config-hash": self.config_hash,
        }


@dataclass(frozen=True)
class WorkerRuntimeSample:
    container_id: str
    restart_count: int
    config_hash: str
    worker_id: str
    pid: int
    observed_at: float


class Runner:
    def run(self, command: Sequence[str], *, cwd: Path | None = None,
            output: bool = False, timeout: int = 300,
            environment: dict[str, str] | None = None,
            strip_output: bool = True) -> str:
        command_env = dict(os.environ if environment is None else environment)
        command_env["GIT_TERMINAL_PROMPT"] = "0"
        result = subprocess.run(
            list(command), cwd=cwd, env=command_env,
            stdin=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            stdout=subprocess.PIPE if output else subprocess.DEVNULL,
            text=True, timeout=timeout,
            start_new_session=os.name == "posix",
        )
        if result.returncode:
            raise QuickstartError("command failed")
        if not output:
            return ""
        return result.stdout.strip() if strip_output else result.stdout.rstrip("\r\n")


def _load_subject(path: Path, managed_root: Path | None = None) -> Subject:
    try:
        metadata = path.stat(follow_symlinks=False)
        pairs = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=list)
        if not stat.S_ISREG(metadata.st_mode) or (
            os.name == "posix" and stat.S_IMODE(metadata.st_mode) & 0o022
        ):
            raise ValueError
        if not isinstance(pairs, list) or any(not isinstance(item, tuple) for item in pairs):
            raise ValueError
        if len({key for key, _ in pairs}) != len(pairs):
            raise ValueError
        value = dict(pairs)
        if set(value) != {
            "source_commit", "backend_image", "frontend_image", "env_file", "ci_success"
        }:
            raise ValueError
        if value["ci_success"] is not True or COMMIT.fullmatch(value["source_commit"]) is None:
            raise ValueError
        env_value = value["env_file"]
        if not isinstance(env_value, str) or not PurePosixPath(env_value).is_absolute():
            raise ValueError
        env_file = Path(env_value)
        images = {
            "backend": (value["backend_image"], BACKEND_REPOSITORY),
            "frontend": (value["frontend_image"], FRONTEND_REPOSITORY),
        }
        if any(
            not isinstance(ref, str)
            or (match := DIGEST_REF.fullmatch(ref)) is None
            or match.group("repository") != repository
            for ref, repository in images.values()
        ):
            raise ValueError
        if managed_root is not None:
            root = managed_root.resolve()
            parent_metadata = path.parent.stat(follow_symlinks=False)
            root_owner = root.stat(follow_symlinks=False).st_uid
            if (
                path != root / "incoming" / "latest-main.json"
                or path.parent.resolve(strict=True) != path.parent
                or os.name == "posix"
                and (
                    metadata.st_uid != root_owner or parent_metadata.st_uid != root_owner
                    or stat.S_IMODE(metadata.st_mode) & 0o022
                    or stat.S_IMODE(parent_metadata.st_mode) & 0o022
                )
            ):
                raise ValueError
    except (OSError, UnicodeError, TypeError, ValueError, json.JSONDecodeError):
        raise QuickstartError("latest main subject is invalid") from None
    return Subject(
        value["source_commit"], value["backend_image"], value["frontend_image"], env_file
    )


def _compose_command(docker: Sequence[str], repo: Path, env_file: Path,
                     subject: Subject) -> list[str]:
    overrides = [
        f"AI_PLATFORM_IMAGE={subject.backend_image}",
        f"AI_PLATFORM_FRONTEND_IMAGE={subject.frontend_image}",
        f"AI_PLATFORM_SOURCE_COMMIT={subject.commit}",
        f"OPENSANDBOX_EXECUTOR_IMAGE={subject.executor_image}",
        f"OPENSANDBOX_EXECUTOR_IMAGE_DIGEST={subject.executor_image_digest}",
    ]
    prefix = [*docker[:2], "env", "-i", *overrides, *docker[2:]] if docker[:2] == ["sudo", "-n"] else ["env", "-i", *overrides, *docker]
    files = [item for path in COMPOSE_FILES for item in ("-f", str(repo / path))]
    return [*prefix, "compose", "-p", PROJECT, "--env-file", str(env_file), *files]


PROXY_ENVIRONMENT = (
    "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "http_proxy", "https_proxy", "no_proxy",
)


def _git_local_environment() -> dict[str, str]:
    result = {
        key: os.environ[key] for key in ("PATH", "LANG", "LC_ALL") if key in os.environ
    }
    result.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull)
    return result


def _docker_environment() -> dict[str, str]:
    allowed = (
        "PATH", "LANG", "LC_ALL", "HOME", "DOCKER_CONFIG",
        *PROXY_ENVIRONMENT,
    )
    return {key: os.environ[key] for key in allowed if key in os.environ}


class TerminationPolicy:
    """Keep runtime transitions recoverable while honoring termination requests."""

    def __init__(self) -> None:
        self._runtime_transition = False
        self._pending_signals: list[int] = []
        self._previous_handlers: dict[int, Any] = {}

    @property
    def pending_count(self) -> int:
        return len(self._pending_signals)

    def __enter__(self) -> "TerminationPolicy":
        if self._previous_handlers:
            raise RuntimeError("termination policy is already installed")
        self._runtime_transition = False
        self._pending_signals.clear()
        try:
            for signum in (signal.SIGINT, signal.SIGTERM):
                self._previous_handlers[signum] = signal.signal(signum, self._handle)
        except BaseException:
            for signum, handler in self._previous_handlers.items():
                signal.signal(signum, handler)
            self._previous_handlers.clear()
            raise
        return self

    def __exit__(self, _exc_type: object, _exc: object, _tb: object) -> None:
        for signum, handler in self._previous_handlers.items():
            signal.signal(signum, handler)
        self._previous_handlers.clear()

    def _handle(self, signum: int, _frame: object) -> None:
        if self._runtime_transition:
            self._pending_signals.append(signum)
            return
        raise KeyboardInterrupt

    def protect_runtime_transition(self) -> None:
        self._runtime_transition = True

    def mark_safe_runtime(self) -> None:
        self._runtime_transition = False
        if self._pending_signals:
            raise RollbackInterruptedError(
                "termination request was honored after a runtime was verified"
            )


class Quickstart:
    def __init__(self, repo: Path, managed_root: Path = MANAGED_ROOT,
                 subject_path: Path | None = None, *, runner: Runner | None = None,
                 health_timeout: int = 120) -> None:
        self.repo = repo.resolve()
        self.root = managed_root.resolve()
        self.subject_path = subject_path or self.root / "incoming" / "latest-main.json"
        self.runner = runner or Runner()
        self.health_timeout = health_timeout
        self.docker: list[str] = []
        self.termination = TerminationPolicy()

    def _detect_docker(self) -> None:
        for candidate in (
            ["docker", "--context", "default"],
            ["sudo", "-n", "docker", "--context", "default"],
        ):
            try:
                self.runner.run(
                    [*candidate, "version"], timeout=30, environment=_docker_environment()
                )
                self.runner.run(
                    [*candidate, "compose", "version"], timeout=30,
                    environment=_docker_environment(),
                )
            except (OSError, subprocess.SubprocessError, QuickstartError):
                continue
            self.docker = list(candidate)
            return
        raise QuickstartError("Docker with Compose is unavailable")

    def _inspect(self, service: str) -> RuntimeContainer:
        fmt = "\t".join((
            "{{.Id}}",
            '{{index .Config.Labels "ai-platform.source-commit"}}',
            "{{.Config.Image}}",
            "{{.RestartCount}}",
            "{{.State.Status}}",
            '{{if index .State "Health"}}{{(index .State "Health").Status}}{{else}}none{{end}}',
            '{{index .Config.Labels "com.docker.compose.project"}}',
            '{{index .Config.Labels "com.docker.compose.service"}}',
            '{{index .Config.Labels "com.docker.compose.project.config_files"}}',
            '{{index .Config.Labels "ai-platform.release-owner"}}',
            '{{index .Config.Labels "ai-platform.release-role"}}',
            '{{index .Config.Labels "ai-platform.source-dirty"}}',
            '{{index .Config.Labels "com.docker.compose.project.working_dir"}}',
            '{{index .Config.Labels "com.docker.compose.oneoff"}}',
            '{{index .Config.Labels "com.docker.compose.config-hash"}}',
        ))
        fields = self.runner.run(
            [*self.docker, "container", "inspect", f"ai-platform-{service}", "--format", fmt],
            output=True, timeout=30, environment=_docker_environment(), strip_output=False,
        ).split("\t")
        try:
            (
                container_id,
                commit,
                image,
                raw_restart_count,
                status,
                health,
                project,
                compose_service,
                config_files,
                release_owner,
                release_role,
                source_dirty,
                working_dir,
                one_off,
                config_hash,
            ) = fields
            restart_count = int(raw_restart_count)
        except (TypeError, ValueError):
            raise QuickstartError("runtime metadata is invalid") from None
        if CONTAINER_ID.fullmatch(container_id) is None or restart_count < 0:
            raise QuickstartError("runtime metadata is invalid")
        return RuntimeContainer(
            container_id=container_id,
            commit=commit,
            image=image,
            restart_count=restart_count,
            status=status,
            health=health,
            project=project,
            service=compose_service,
            config_files=config_files,
            release_owner=release_owner,
            release_role=release_role,
            source_dirty=source_dirty,
            working_dir=working_dir,
            one_off=one_off,
            config_hash=config_hash,
        )

    def _expected_compose_config(self, repo: Path | None = None) -> str:
        selected_repo = self.repo if repo is None else repo
        return ",".join(str(selected_repo / path) for path in COMPOSE_FILES)

    def _managed_persistent_compose_config(
        self,
        containers: dict[str, RuntimeContainer],
    ) -> str | None:
        configs = {containers[service].config_files for service in PERSISTENT_SERVICES}
        working_dirs = {containers[service].working_dir for service in PERSISTENT_SERVICES}
        if len(configs) != 1 or len(working_dirs) != 1:
            return None
        config = next(iter(configs))
        working_dir = next(iter(working_dirs))
        if config == self._expected_compose_config():
            release = self.repo
        else:
            try:
                release = Path(config.split(",", 1)[0]).parents[2]
            except IndexError:
                return None
            if (
                release.parent != self.root / "releases"
                or COMMIT.fullmatch(release.name) is None
            ):
                return None
        if (
            config != self._expected_compose_config(release)
            or working_dir != str(release / COMPOSE_FILES[0].parent)
        ):
            return None
        return config

    def _app_compose_identity_mismatches(
        self,
        container: RuntimeContainer,
        service: str,
        *,
        repo: Path | None = None,
    ) -> list[str]:
        selected_repo = self.repo if repo is None else repo
        mismatches = compose_identity_mismatches(
            container.compose_labels,
            service,
            expected_compose_dir=str(selected_repo / COMPOSE_FILES[0].parent),
            expected_config_files=self._expected_compose_config(selected_repo),
        )
        if container.source_dirty != "false":
            mismatches.append(f"{service}_container_dirty_label_mismatch")
        return mismatches

    def _current_runtime(self) -> Subject:
        values = {service: self._inspect(service) for service in PROJECT_SERVICES}
        observed_services = self.runner.run(
            [*self.docker, "container", "ls", "-a", "--filter",
             f"label=com.docker.compose.project={PROJECT}", "--format",
             '{{.Label "com.docker.compose.service"}}'],
            output=True, timeout=30, environment=_docker_environment(),
        ).splitlines()
        app_values = {role: values[role] for role in ("api", "worker", "frontend")}
        commits = {item.commit for item in app_values.values()}
        commit = next(iter(commits)) if len(commits) == 1 else ""
        expected_config = ",".join(
            str(self.root / "releases" / commit / path) for path in COMPOSE_FILES
        )
        runtime_repo = self.root / "releases" / commit
        persistent_config = self._managed_persistent_compose_config(values)
        if (
            COMMIT.fullmatch(commit) is None
            or sorted(observed_services) != sorted(PROJECT_SERVICES)
            or persistent_config is None
            or any(
                (
                    item.project != PROJECT
                    or item.service != service
                    or (
                        service not in PERSISTENT_SERVICES
                        and item.config_files != expected_config
                    )
                )
                for service, item in values.items()
            )
            or any(
                self._app_compose_identity_mismatches(
                    app_values[service],
                    service,
                    repo=runtime_repo,
                )
                for service in app_values
            )
        ):
            raise QuickstartError("current runtime subject is invalid")
        backend = app_values["api"].image
        if app_values["worker"].image != backend:
            raise QuickstartError("current runtime subject is invalid")
        for ref, repository in (
            (backend, BACKEND_REPOSITORY),
            (app_values["frontend"].image, FRONTEND_REPOSITORY),
        ):
            match = DIGEST_REF.fullmatch(ref)
            if match is None or match.group("repository") != repository:
                raise QuickstartError("current runtime subject is invalid")
        return Subject(
            commit,
            backend,
            app_values["frontend"].image,
            persistent_compose_config=persistent_config,
        )

    def _validate_env(self, path: Path) -> Path:
        try:
            root_metadata = self.root.stat(follow_symlinks=False)
            metadata = path.stat(follow_symlinks=False)
            valid = (
                path.resolve(strict=True) == path
                and path.is_relative_to(self.root / "config")
                and stat.S_ISREG(metadata.st_mode)
                and (
                    os.name != "posix"
                    or (
                        metadata.st_uid == root_metadata.st_uid
                        and stat.S_IMODE(metadata.st_mode) == 0o600
                    )
                )
            )
        except OSError:
            valid = False
        if not valid:
            raise QuickstartError("managed .env is missing or unsafe")
        return path

    def _verify_checkout(self, repo: Path, commit: str) -> None:
        expected = self.root / "releases" / commit
        if repo.resolve() != expected or any(
            not (repo / path).is_file() or (repo / path).is_symlink()
            for path in COMPOSE_FILES
        ):
            raise QuickstartError("run quickstart from the prepared exact release checkout")
        git_environment = _git_local_environment()
        head = self.runner.run(
            ["git", "rev-parse", "HEAD"], cwd=repo, output=True,
            environment=git_environment,
        )
        dirty = self.runner.run(
            ["git", "status", "--porcelain", "--untracked-files=all"], cwd=repo,
            output=True, environment=git_environment,
        )
        if head != commit or dirty:
            raise QuickstartError("release checkout is not clean at the exact commit")

    def _verify_source(self, subject: Subject) -> None:
        self._verify_checkout(self.repo, subject.commit)
        origin = self.runner.run(
            ["git", "config", "--local", "--get", "remote.origin.url"],
            cwd=self.repo, output=True, environment=_git_local_environment(),
        )
        if origin.rstrip("/") != ORIGIN_URL:
            raise QuickstartError("prepared subject has an invalid origin")

    def _compose(self, env_file: Path, subject: Subject, *arguments: str) -> None:
        self._validate_env(env_file)
        self.runner.run(
            [*_compose_command(self.docker, self.repo, env_file, subject), *arguments],
            cwd=self.repo / COMPOSE_FILES[0].parent,
            timeout=600 if arguments and arguments[0] == "up" else 90,
            environment=_docker_environment(),
        )

    def _http_json(self, path: str) -> dict[str, Any]:
        with urlopen(f"http://127.0.0.1:8020{path}", timeout=10) as response:
            payload = response.read(65537)
        if len(payload) > 65536:
            raise QuickstartError("health response is too large")
        value = json.loads(payload.decode("utf-8"))
        if not isinstance(value, dict):
            raise QuickstartError("health response is invalid")
        return value

    def _probe_opensandbox_lifecycle(self) -> None:
        for service in ("api", "worker"):
            self.runner.run(
                [
                    *self.docker,
                    "exec",
                    f"ai-platform-{service}",
                    "python",
                    "-c",
                    OPENSANDBOX_HEALTH_PROBE,
                ],
                timeout=15,
                environment=_docker_environment(),
            )

    def _health(self, subject: Subject) -> None:
        health = self._http_json("/api/ai/health")
        ready = self._http_json("/api/ai/ready")
        if health.get("status") != "ok" or ready.get("status") != "ready" or ready.get("runtime_commit") != subject.commit:
            raise QuickstartError("API health failed")
        containers = {service: self._inspect(service) for service in SERVICES}
        if self._managed_persistent_compose_config(containers) is None:
            raise QuickstartError("container health failed")
        for service, container in containers.items():
            healthy = service == "worker" or container.health == "healthy"
            if (
                container.project != PROJECT
                or container.service != service
                or (
                    service not in PERSISTENT_SERVICES
                    and container.config_files != self._expected_compose_config()
                )
                or container.status != "running"
                or not healthy
            ):
                raise QuickstartError("container health failed")
            if service in {"api", "worker", "frontend"}:
                if self._app_compose_identity_mismatches(container, service):
                    raise QuickstartError("container identity failed")
                if container.commit != subject.commit:
                    raise QuickstartError("runtime commit mismatch")
                expected_image = (
                    subject.frontend_image
                    if service == "frontend"
                    else subject.backend_image
                )
                if container.image != expected_image:
                    raise QuickstartError("runtime image mismatch")
        if self.runner.run(["systemctl", "is-active", "opensandbox.service"], output=True, timeout=15) != "active":
            raise QuickstartError("OpenSandbox is not active")
        self._probe_opensandbox_lifecycle()

    def _wait_health(self, subject: Subject) -> None:
        deadline = time.monotonic() + self.health_timeout
        while True:
            try:
                self._health(subject)
                return
            except (
                OSError, UnicodeError, ValueError, json.JSONDecodeError,
                HTTPException, QuickstartError,
            ):
                if time.monotonic() >= deadline:
                    raise QuickstartError("runtime health did not converge") from None
                time.sleep(2)

    def _protocol_v2_lease_count(self) -> int:
        result = self.runner.run(
            [
                *self.docker,
                "exec",
                "ai-platform-redis",
                "redis-cli",
                "--raw",
                "EVAL",
                ROLLBACK_PROTOCOL_V2_LEASE_PROBE,
                "2",
                ROLLBACK_PROCESSING_META_KEY,
                ROLLBACK_RETRY_META_KEY,
                str(ROLLBACK_LEASE_SCAN_LIMIT),
            ],
            output=True,
            timeout=30,
            environment=_docker_environment(),
        )
        try:
            value = json.loads(result)
            if not isinstance(value, dict) or value.get("status") != "ok":
                raise ValueError
            if set(value) != {"status", "processing", "retry", "total"}:
                raise ValueError
            processing = value["processing"]
            retry = value["retry"]
            total = value["total"]
            if any(
                isinstance(item, bool) or not isinstance(item, int) or item < 0
                for item in (processing, retry, total)
            ) or total > processing + retry:
                raise ValueError
        except (TypeError, ValueError, json.JSONDecodeError):
            raise QuickstartError("protocol-v2 lease state could not be proven") from None
        return total

    def _worker_runtime_sample(
        self,
        subject: Subject,
        *,
        not_before: float,
    ) -> WorkerRuntimeSample:
        container = self._inspect("worker")
        if (
            container.commit != subject.commit
            or container.image != subject.backend_image
            or container.status != "running"
            or self._app_compose_identity_mismatches(container, "worker")
        ):
            raise QuickstartError("worker runtime metadata is invalid")
        result = self.runner.run(
            [
                *self.docker,
                "exec",
                container.container_id,
                "python",
                "-I",
                "-c",
                WORKER_RUNTIME_HEARTBEAT_PROBE,
                subject.commit,
                repr(not_before),
            ],
            output=True,
            timeout=30,
            environment=_docker_environment(),
        )
        try:
            heartbeat = json.loads(result)
            if not isinstance(heartbeat, dict) or set(heartbeat) != {
                "status", "worker_id", "pid", "observed_at",
            }:
                raise ValueError
            worker_id = heartbeat["worker_id"]
            pid = heartbeat["pid"]
            observed_at = heartbeat["observed_at"]
            if (
                heartbeat["status"] != "ok"
                or not isinstance(worker_id, str)
                or not worker_id.strip()
                or isinstance(pid, bool)
                or not isinstance(pid, int)
                or pid <= 0
                or isinstance(observed_at, bool)
                or not isinstance(observed_at, (int, float))
                or not math.isfinite(observed_at)
                or observed_at < not_before
            ):
                raise ValueError
        except (TypeError, ValueError, json.JSONDecodeError):
            raise QuickstartError("worker runtime heartbeat is invalid") from None
        return WorkerRuntimeSample(
            container_id=container.container_id,
            restart_count=container.restart_count,
            config_hash=container.config_hash,
            worker_id=worker_id,
            pid=pid,
            observed_at=float(observed_at),
        )

    def _wait_worker_runtime(self, subject: Subject, *, not_before: float) -> None:
        deadline = time.monotonic() + self.health_timeout
        previous: WorkerRuntimeSample | None = None
        while True:
            try:
                current = self._worker_runtime_sample(subject, not_before=not_before)
                if (
                    previous is not None
                    and current.container_id == previous.container_id
                    and current.restart_count == previous.restart_count
                    and current.config_hash == previous.config_hash
                    and current.worker_id == previous.worker_id
                    and current.pid == previous.pid
                    and current.observed_at > previous.observed_at
                ):
                    return
                previous = current
            except (
                OSError,
                UnicodeError,
                ValueError,
                json.JSONDecodeError,
                subprocess.SubprocessError,
                QuickstartError,
            ):
                previous = None
            if time.monotonic() >= deadline:
                raise QuickstartError(
                    "worker runtime heartbeat did not converge"
                ) from None
            time.sleep(2)

    def _retain_target_recovery_worker(self, subject: Subject, env_file: Path) -> None:
        started_at = time.time()
        self._compose(
            env_file,
            subject,
            "up",
            "-d",
            "--no-build",
            "--pull",
            "never",
            "worker",
        )
        self._wait_worker_runtime(subject, not_before=started_at)

    def _restore_target_recovery_worker(
        self,
        subject: Subject,
        env_file: Path,
    ) -> None:
        self._compose(env_file, subject, "stop", "api", "worker")
        self._retain_target_recovery_worker(subject, env_file)

    def _rollback(self, subject: Subject, previous: Subject, env_file: Path) -> None:
        try:
            self._compose(env_file, subject, "stop", "api", "worker")
            try:
                protocol_v2_leases = self._protocol_v2_lease_count()
            except (OSError, subprocess.SubprocessError, QuickstartError):
                self._retain_target_recovery_worker(subject, env_file)
                self.termination.mark_safe_runtime()
                raise RollbackBlockedError(
                    "image rollback blocked because protocol-v2 lease state could not be "
                    "proven; the API remains stopped and the target recovery worker is verified"
                ) from None
            if protocol_v2_leases:
                self._retain_target_recovery_worker(subject, env_file)
                self.termination.mark_safe_runtime()
                raise RollbackBlockedError(
                    "image rollback blocked by active protocol-v2 leases; the API remains "
                    "stopped and the target recovery worker is verified"
                )
            previous_repo = self.root / "releases" / previous.commit
            self._verify_checkout(previous_repo, previous.commit)
            current_repo, self.repo = self.repo, previous_repo
            try:
                self._compose(env_file, previous, "config", "--quiet")
                previous_started_at = time.time()
                self._compose(
                    env_file,
                    previous,
                    "up",
                    "-d",
                    "--no-build",
                    "--pull",
                    "never",
                )
                self._wait_health(previous)
                self._wait_worker_runtime(previous, not_before=previous_started_at)
            finally:
                self.repo = current_repo
        except (RollbackBlockedError, RollbackInterruptedError):
            raise
        except KeyboardInterrupt:
            self._restore_target_recovery_worker(subject, env_file)
            self.termination.mark_safe_runtime()
            raise RollbackInterruptedError(
                "rollback interruption was deferred until the API was stopped and the "
                "target recovery worker was verified"
            ) from None
        except (OSError, subprocess.SubprocessError, QuickstartError):
            self._restore_target_recovery_worker(subject, env_file)
            self.termination.mark_safe_runtime()
            raise RollbackBlockedError(
                "image rollback could not complete; the API remains stopped and the target "
                "recovery worker is verified"
            ) from None
        self.termination.mark_safe_runtime()

    def _preflight_rollback(self, previous: Subject, env_file: Path) -> None:
        previous_repo = self.root / "releases" / previous.commit
        self._verify_checkout(previous_repo, previous.commit)
        for image in (previous.backend_image, previous.frontend_image):
            self.runner.run(
                [*self.docker, "image", "inspect", image], timeout=30,
                environment=_docker_environment(),
            )
        current_repo, self.repo = self.repo, previous_repo
        try:
            self._compose(env_file, previous, "config", "--quiet")
        finally:
            self.repo = current_repo

    def run(self) -> Subject:
        with self.termination:
            self._detect_docker()
            subject = _load_subject(self.subject_path, self.root)
            self._verify_source(subject)
            previous = self._current_runtime()
            if subject.env_file is None:
                raise QuickstartError("latest main subject is missing the managed env path")
            env_file = self._validate_env(subject.env_file)
            self._compose(env_file, subject, "config", "--quiet")
            print("preflight: ok")
            # The backend artifact also contains the executor app. Pulling this exact
            # digest on the OpenSandbox Docker host removes first-run registry latency.
            for image in dict.fromkeys((subject.backend_image, subject.frontend_image)):
                self._validate_env(env_file)
                self.runner.run(
                    [*self.docker, "pull", image], timeout=900,
                    environment=_docker_environment(),
                )
            self.runner.run(
                [*self.docker, "image", "inspect", subject.executor_image],
                timeout=30,
                environment=_docker_environment(),
            )
            print("pull: ok (OpenSandbox executor cached)")
            self._verify_source(subject)
            if self._current_runtime() != previous:
                raise QuickstartError("runtime changed while quickstart was preparing images")
            self._preflight_rollback(previous, env_file)
            self.termination.protect_runtime_transition()
            target_started_at = time.time()
            try:
                self._compose(
                    env_file,
                    subject,
                    "up",
                    "-d",
                    "--no-build",
                    "--pull",
                    "never",
                )
                self._wait_health(subject)
                self._wait_worker_runtime(subject, not_before=target_started_at)
            except (OSError, subprocess.SubprocessError, QuickstartError, KeyboardInterrupt):
                try:
                    self._rollback(subject, previous, env_file)
                except RollbackBlockedError as exc:
                    raise QuickstartError(f"startup failed; {exc}") from None
                except RollbackInterruptedError as exc:
                    raise QuickstartError(f"startup failed; {exc}") from None
                except (OSError, subprocess.SubprocessError, QuickstartError, KeyboardInterrupt):
                    raise QuickstartError(
                        "startup and rollback failed; data volumes were preserved"
                    ) from None
                raise QuickstartError(
                    "startup failed; previous images are healthy again "
                    "(database changes were not reversed)"
                ) from None
            else:
                self.termination.mark_safe_runtime()
            print("up: ok")
            print("health: api=ok ready=ok worker=ok containers=ok opensandbox=ok")
            print(f"commit: {subject.commit}")
            print(f"backend: {subject.backend_image}")
            print(f"frontend: {subject.frontend_image}")
            return subject


def main() -> int:
    repo = Path(__file__).resolve().parents[1]
    try:
        Quickstart(repo).run()
    except QuickstartError as exc:
        print(f"sandbox quickstart: failed: {exc} (no data volumes were removed)")
        return 2
    except (OSError, subprocess.SubprocessError, KeyboardInterrupt):
        print("sandbox quickstart: failed: command error (no data volumes were removed)")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
