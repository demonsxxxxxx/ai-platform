"""Deploy the controller-approved CI-success main subject to s72 internal-test."""

from __future__ import annotations

import sys

if __name__ == "__main__" and not sys.flags.isolated:
    raise SystemExit("run s72 quickstart through scripts/quickstart-s72.sh")

from dataclasses import dataclass
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import time
from typing import Any, Sequence
from urllib.request import urlopen


MANAGED_ROOT = Path("/data/ai-platform-internal-test")
PROJECT = "ai-platform-internal"
COMPOSE_FILES = (
    Path("deploy/ai-platform/docker-compose.yml"),
    Path("deploy/ai-platform/docker-compose.opensandbox-internal-test.yml"),
)
BACKEND_REPOSITORY = "ghcr.io/demonsxxxxxx/ai-platform-backend"
FRONTEND_REPOSITORY = "ghcr.io/demonsxxxxxx/ai-platform-frontend"
ORIGIN_URLS = {
    "https://github.com/demonsxxxxxx/ai-platform.git", "git@github.com:demonsxxxxxx/ai-platform.git",
    "ssh://git@github.com/demonsxxxxxx/ai-platform.git",
}
COMMIT = re.compile(r"[0-9a-f]{40}\Z")
DIGEST_REF = re.compile(r"(?P<repository>[^@]+)@sha256:[0-9a-f]{64}\Z")
SERVICES = ("api", "worker", "frontend", "postgres", "redis", "minio")


class QuickstartError(RuntimeError): ...


@dataclass(frozen=True)
class Subject:
    commit: str
    backend_image: str
    frontend_image: str
    env_file: Path | None = None


class Runner:
    def run(self, command: Sequence[str], *, cwd: Path | None = None,
            output: bool = False, timeout: int = 300) -> str:
        result = subprocess.run(
            list(command), cwd=cwd, env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
            stdin=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            stdout=subprocess.PIPE if output else subprocess.DEVNULL,
            text=True, timeout=timeout,
        )
        if result.returncode:
            raise QuickstartError("command failed")
        return result.stdout.strip() if output else ""


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
    ]
    prefix = [*docker[:-1], "env", *overrides, docker[-1]] if docker[:2] == ["sudo", "-n"] else ["env", *overrides, *docker]
    files = [item for path in COMPOSE_FILES for item in ("-f", str(repo / path))]
    return [*prefix, "compose", "-p", PROJECT, "--env-file", str(env_file), *files]


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

    def _detect_docker(self) -> None:
        for candidate in (["docker"], ["sudo", "-n", "docker"]):
            try:
                self.runner.run([*candidate, "version"], timeout=30)
                self.runner.run([*candidate, "compose", "version"], timeout=30)
            except (OSError, subprocess.SubprocessError, QuickstartError):
                continue
            self.docker = list(candidate)
            return
        raise QuickstartError("Docker with Compose is unavailable")

    def _inspect(self, service: str) -> list[str]:
        fmt = "\t".join((
            '{{index .Config.Labels "ai-platform.source-commit"}}', "{{.Config.Image}}",
            "{{.RestartCount}}", "{{.State.Status}}",
            "{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}",
            '{{index .Config.Labels "com.docker.compose.project"}}',
            '{{index .Config.Labels "com.docker.compose.service"}}',
        ))
        fields = self.runner.run(
            [*self.docker, "container", "inspect", f"ai-platform-{service}", "--format", fmt],
            output=True, timeout=30,
        ).split("\t")
        if len(fields) != 7:
            raise QuickstartError("runtime metadata is invalid")
        return fields

    def _current_runtime(self) -> Subject:
        values = {role: self._inspect(role) for role in ("api", "worker", "frontend")}
        commits = {item[0] for item in values.values()}
        if (
            len(commits) != 1 or COMMIT.fullmatch(next(iter(commits))) is None
            or any(item[5:] != [PROJECT, role] for role, item in values.items())
        ):
            raise QuickstartError("current runtime subject is invalid")
        backend = values["api"][1]
        if values["worker"][1] != backend:
            raise QuickstartError("current runtime subject is invalid")
        for ref, repository in ((backend, BACKEND_REPOSITORY), (values["frontend"][1], FRONTEND_REPOSITORY)):
            match = DIGEST_REF.fullmatch(ref)
            if match is None or match.group("repository") != repository:
                raise QuickstartError("current runtime subject is invalid")
        return Subject(commits.pop(), backend, values["frontend"][1])

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
            raise QuickstartError("run quickstart from the prepared exact-main release checkout")
        head = self.runner.run(["git", "rev-parse", "HEAD"], cwd=repo, output=True)
        dirty = self.runner.run(
            ["git", "status", "--porcelain", "--untracked-files=all"], cwd=repo, output=True
        )
        if head != commit or dirty:
            raise QuickstartError("release checkout is not clean at the exact commit")

    def _verify_source(self, subject: Subject) -> None:
        self._verify_checkout(self.repo, subject.commit)
        origin = self.runner.run(["git", "config", "--get", "remote.origin.url"], cwd=self.repo, output=True)
        remote = self.runner.run(
            ["git", "ls-remote", "--exit-code", "origin", "refs/heads/main"],
            cwd=self.repo, output=True, timeout=60,
        ).split()
        if origin.rstrip("/") not in ORIGIN_URLS or remote != [subject.commit, "refs/heads/main"]:
            raise QuickstartError("prepared subject is not fresh origin/main")

    def _compose(self, env_file: Path, subject: Subject, *arguments: str) -> None:
        self._validate_env(env_file)
        self.runner.run(
            [*_compose_command(self.docker, self.repo, env_file, subject), *arguments],
            cwd=self.repo / COMPOSE_FILES[0].parent,
            timeout=600 if arguments and arguments[0] == "up" else 90,
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

    def _health(self, subject: Subject) -> None:
        health = self._http_json("/api/ai/health")
        ready = self._http_json("/api/ai/ready")
        if health.get("status") != "ok" or ready.get("status") != "ready" or ready.get("runtime_commit") != subject.commit:
            raise QuickstartError("API health failed")
        for service in SERVICES:
            commit, image, _restarts, status, container_health, project, compose_service = self._inspect(service)
            healthy = service == "worker" or container_health == "healthy"
            if project != PROJECT or compose_service != service or status != "running" or not healthy:
                raise QuickstartError("container health failed")
            if service in {"api", "worker", "frontend"} and commit != subject.commit:
                raise QuickstartError("runtime commit mismatch")
            expected_image = subject.frontend_image if service == "frontend" else subject.backend_image
            if service in {"api", "worker", "frontend"} and image != expected_image:
                raise QuickstartError("runtime image mismatch")
        if self.runner.run(["systemctl", "is-active", "opensandbox.service"], output=True, timeout=15) != "active":
            raise QuickstartError("OpenSandbox is not active")
        with urlopen("http://127.0.0.1:8080/health", timeout=10) as response:
            if response.status != 200 or len(response.read(65537)) > 65536:
                raise QuickstartError("OpenSandbox health failed")

    def _wait_health(self, subject: Subject) -> None:
        deadline = time.monotonic() + self.health_timeout
        while True:
            try:
                self._health(subject)
                return
            except (OSError, UnicodeError, ValueError, json.JSONDecodeError, QuickstartError):
                if time.monotonic() >= deadline:
                    raise QuickstartError("runtime health did not converge") from None
                time.sleep(2)

    def _rollback(self, previous: Subject, env_file: Path) -> None:
        previous_repo = self.root / "releases" / previous.commit
        self._verify_checkout(previous_repo, previous.commit)
        current_repo, self.repo = self.repo, previous_repo
        try:
            self._compose(env_file, previous, "config", "--quiet")
            self._compose(env_file, previous, "up", "-d", "--no-build", "--pull", "never")
            self._wait_health(previous)
        finally:
            self.repo = current_repo

    def _preflight_rollback(self, previous: Subject, env_file: Path) -> None:
        previous_repo = self.root / "releases" / previous.commit
        self._verify_checkout(previous_repo, previous.commit)
        for image in (previous.backend_image, previous.frontend_image):
            self.runner.run([*self.docker, "image", "inspect", image], timeout=30)
        current_repo, self.repo = self.repo, previous_repo
        try:
            self._compose(env_file, previous, "config", "--quiet")
        finally:
            self.repo = current_repo

    def run(self) -> Subject:
        self._detect_docker()
        subject = _load_subject(self.subject_path, self.root)
        self._verify_source(subject)
        previous = self._current_runtime()
        if subject.env_file is None:
            raise QuickstartError("latest main subject is missing the managed env path")
        env_file = self._validate_env(subject.env_file)
        self._compose(env_file, subject, "config", "--quiet")
        print("preflight: ok")
        for image in (subject.backend_image, subject.frontend_image):
            self._validate_env(env_file)
            self.runner.run([*self.docker, "pull", image], timeout=900)
        print("pull: ok")
        self._verify_source(subject)
        if self._current_runtime() != previous:
            raise QuickstartError("runtime changed while quickstart was preparing images")
        self._preflight_rollback(previous, env_file)
        try:
            self._compose(env_file, subject, "up", "-d", "--no-build", "--pull", "never")
            self._wait_health(subject)
        except (OSError, subprocess.SubprocessError, QuickstartError):
            try:
                self._rollback(previous, env_file)
            except (OSError, subprocess.SubprocessError, QuickstartError):
                raise QuickstartError("startup and rollback failed; data volumes were preserved") from None
            raise QuickstartError(
                "startup failed; previous images are healthy again (database changes were not reversed)"
            ) from None
        print("up: ok")
        print("health: api=ok ready=ok containers=ok opensandbox=ok")
        print(f"commit: {subject.commit}")
        print(f"backend: {subject.backend_image}")
        print(f"frontend: {subject.frontend_image}")
        return subject


def main() -> int:
    repo = Path(__file__).resolve().parents[1]
    try:
        Quickstart(repo).run()
    except QuickstartError as exc:
        print(f"s72 quickstart: failed: {exc} (no data volumes were removed)")
        return 2
    except (OSError, subprocess.SubprocessError):
        print("s72 quickstart: failed: command error (no data volumes were removed)")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
