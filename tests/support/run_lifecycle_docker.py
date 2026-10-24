"""Isolated Docker infrastructure for full Run lifecycle integration tests."""

from __future__ import annotations

import base64
import json
from pathlib import Path
import re
import secrets
import subprocess
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import ProxyHandler, build_opener
import uuid

from tests.support.run_lifecycle_faults import TERMINAL_ACK_FAULT_MARKER_PATH


ROOT = Path(__file__).resolve().parents[2]
_SOURCE_COMMIT_RE = re.compile(r"[0-9a-f]{40}\Z")
_RELEASE_ROLE_LABEL = "ai-platform.release-role"
_RELEASE_OWNER_LABEL = "ai-platform.release-owner"
_SOURCE_COMMIT_LABEL = "ai-platform.source-commit"
_LIFECYCLE_LABEL = "ai-platform.lifecycle-test"
_LIFECYCLE_SCOPE_LABEL = "ai-platform.lifecycle-test.scope"
_WORKSPACE_LABEL = "ai-platform.workspace_id"
_RUN_LABEL = "ai-platform.run_id"
_DIRECT_HTTP = build_opener(ProxyHandler({}))


def _direct_urlopen(url: str, *, timeout: float):
    return _DIRECT_HTTP.open(url, timeout=timeout)


def _new_secret() -> str:
    return base64.b64encode(secrets.token_bytes(32)).decode("ascii")


def _docker_client():
    try:
        import docker
    except ImportError as exc:  # pragma: no cover - dependency is locked in pyproject.toml
        raise RuntimeError("Docker SDK is required for the lifecycle fixture") from exc
    client = docker.from_env()
    try:
        client.ping()
    except Exception:
        client.close()
        raise RuntimeError("Docker daemon is unavailable for lifecycle integration") from None
    return client


def _safe_name(value: str) -> str:
    return re.sub(r"[^a-z0-9_.-]+", "-", value.lower()).strip("-.")[:63]


def build_control_image(evidence_dir: Path) -> str:
    """Build production sources plus the test-only executor/proxy wrapper.

    The production Dockerfile supplies the runtime and Python dependencies. The
    fixture Dockerfile only adds the two test-owned wrappers. Build output stays
    in the supplied evidence directory so image-build diagnostics never become
    noisy test output.
    """

    evidence_dir = Path(evidence_dir)
    evidence_dir.mkdir(parents=True, exist_ok=True)
    if not (ROOT / "tests/support/run_lifecycle_executor.py").is_file():
        raise FileNotFoundError(
            "tests/support/run_lifecycle_executor.py must exist before building the lifecycle image"
        )
    if not (ROOT / "tests/support/run_lifecycle_docker_proxy.py").is_file():
        raise FileNotFoundError("lifecycle Docker socket proxy source is missing")

    source_commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    ).stdout.strip()
    if _SOURCE_COMMIT_RE.fullmatch(source_commit) is None:
        raise RuntimeError("lifecycle image source commit is invalid")
    dirty = bool(subprocess.run(
        ["git", "status", "--porcelain"], cwd=ROOT, check=True,
        capture_output=True, text=True, timeout=10,
    ).stdout.strip())

    build_id = uuid.uuid4().hex
    base_tag = f"ai-platform-lifecycle-base:{build_id}"
    control_tag = f"ai-platform-lifecycle-control:{build_id}"
    base_log = evidence_dir / f"docker-build-base-{build_id}.log"
    control_log = evidence_dir / f"docker-build-control-{build_id}.log"
    base_command = [
        "docker", "build", "--pull=false", "--progress=plain",
        "--build-arg", f"AI_PLATFORM_BUILD_COMMIT={source_commit}",
        "--build-arg", f"AI_PLATFORM_BUILD_DIRTY={str(dirty).lower()}",
        "--file", "Dockerfile", "--tag", base_tag, ".",
    ]
    _run_build(base_command, base_log)

    control_command = [
        "docker", "build", "--pull=false", "--progress=plain",
        "--build-arg", f"BASE_IMAGE={base_tag}",
        "--file", "tests/fixtures/run-lifecycle/Dockerfile",
        "--tag", control_tag, ".",
    ]
    _run_build(control_command, control_log)
    inspected = subprocess.run(
        ["docker", "image", "inspect", "--format", "{{.Id}}", control_tag],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    ).stdout.strip()
    if re.fullmatch(r"sha256:[0-9a-f]{64}", inspected) is None:
        raise RuntimeError("lifecycle control image digest is invalid")
    return inspected


def _run_build(command: list[str], log_path: Path) -> None:
    with log_path.open("wb") as output:
        try:
            result = subprocess.run(
                command,
                cwd=ROOT,
                stdout=output,
                stderr=subprocess.STDOUT,
                check=False,
                timeout=600,
            )
        except subprocess.TimeoutExpired as exc:
            raise TimeoutError(f"Docker build timed out; see {log_path}") from exc
    if result.returncode != 0:
        raise RuntimeError(
            f"Docker build failed with exit code {result.returncode}; see {log_path}"
        )


class LifecycleDockerStack:
    """One test-owned PostgreSQL, Redis, MinIO, API, Worker and Docker network."""

    def __init__(self, image_id: str, source_commit: str, evidence_dir: Path) -> None:
        if re.fullmatch(r"sha256:[0-9a-f]{64}", image_id) is None:
            raise ValueError("image_id must be a sha256 Docker image ID")
        if _SOURCE_COMMIT_RE.fullmatch(source_commit) is None:
            raise ValueError("source_commit must be a full lowercase Git SHA")
        self.image_id = image_id
        self.source_commit = source_commit
        self.evidence_dir = Path(evidence_dir)
        self.evidence_dir.mkdir(parents=True, exist_ok=True)

        self.scope_id = uuid.uuid4().hex
        self.workspace_id = f"lifecycle-ws-{self.scope_id}"
        self.user_id = "88000001"
        self.tenant_id = "default"
        self.bucket = f"ai-platform-lifecycle-{self.scope_id}"
        self.gateway_secret = _new_secret()
        self.session_secret = _new_secret()
        self.encryption_key = _new_secret()
        self.egress_proof_signing_key = _new_secret()
        self.s3_access_key = "synthetic-artifact-tests"
        self.s3_secret_key = "synthetic-artifact-tests-only"
        self._postgres_user = f"lifecycle_{self.scope_id[:12]}"
        self._postgres_password = _new_secret()
        self._postgres_database = f"lifecycle_{self.scope_id[:12]}"
        self._minio_root_user = self.s3_access_key
        self._minio_root_password = self.s3_secret_key

        self.name_prefix = f"ai-platform-lifecycle-{self.scope_id}"
        self.network_name = f"{self.name_prefix}-bridge"
        # The production UDS owner checks the daemon-side path against 107 bytes.
        self.workspace_volume_name = f"n01-{self.scope_id[:16]}"
        self.socket_volume_name = f"{self.name_prefix}-socket"
        self._client: Any | None = None
        self._network: Any | None = None
        self._workspace_volume: Any | None = None
        self._socket_volume: Any | None = None
        self._containers: dict[str, Any] = {}
        self._port_map: dict[str, str] = {}
        self._docker_socket_source = ""
        self._closed = False
        self.api_base = ""
        self.postgres_dsn = ""
        self.redis_url = ""
        self.s3_endpoint = ""
        self.workspace_mountpoint = ""
        self.network_gateway = ""

        self.environment = self._container_environment()

    def __enter__(self) -> LifecycleDockerStack:
        try:
            self._client = _docker_client()
            self._docker_socket_source = self._resolve_docker_socket_source()
            self._create_network()
            self._create_data_services()
            self._create_workspace_volume()
            self._create_docker_socket_volume()
            self._initialize_workspace_owner()
            self._start_socket_proxy()
            self._create_artifact_bucket()
            return self
        except BaseException:
            try:
                self._cleanup()
            except BaseException as cleanup_error:
                self._write_cleanup_failure(cleanup_error)
            raise

    def __exit__(self, exc_type, exc, traceback) -> bool:
        try:
            self._cleanup()
        except BaseException as cleanup_error:
            self._write_cleanup_failure(cleanup_error)
            if exc is None:
                raise
            if hasattr(exc, "add_note"):
                exc.add_note(
                    f"Lifecycle Docker cleanup also failed; see {self.evidence_dir / 'docker-cleanup.json'}"
                )
        return False

    @property
    def api_url(self) -> str:
        """Alias for the loopback API base URL after start_api()."""

        if not self.api_base:
            raise RuntimeError("lifecycle API has not been started")
        return self.api_base

    def start_api(
        self,
        *,
        timeout_seconds: float = 120.0,
        drop_terminal_ack_once: bool = False,
    ) -> str:
        if self._client is None or self._network is None:
            raise RuntimeError("LifecycleDockerStack must be entered before starting services")
        if "api" in self._containers:
            container = self._containers["api"]
            container.reload()
            if container.status != "running":
                raise RuntimeError("lifecycle API container is not running")
            if not self.api_base:
                self.api_base = self._published_url(container, "8020/tcp")
        else:
            env = dict(self.environment)
            env.update(
                {
                    "APP_MODULE": "app.main:create_app",
                    "APP_PORT": "8020",
                    "WORKER_CONCURRENCY": "1",
                }
            )
            if drop_terminal_ack_once:
                env.update(
                    {
                        "N01_DROP_TERMINAL_ACK_ONCE": "true",
                        "N01_TERMINAL_ACK_MARKER_PATH": TERMINAL_ACK_FAULT_MARKER_PATH,
                    }
                )
            container = self._client.containers.create(
                self.image_id,
                name=f"{self.name_prefix}-api",
                command=["uvicorn"],
                environment=env,
                labels=self._labels(
                    **{
                        _RELEASE_ROLE_LABEL: "api",
                        _RELEASE_OWNER_LABEL: "repo-local-compose",
                        _SOURCE_COMMIT_LABEL: self.source_commit,
                    }
                ),
                network=self.network_name,
                ports={"8020/tcp": ("127.0.0.1", None)},
                healthcheck={
                    "test": [
                        "CMD",
                        "python",
                        "-c",
                        "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8020/api/ai/ready', timeout=2).read()",
                    ],
                    "interval": 2_000_000_000,
                    "timeout": 3_000_000_000,
                    "retries": 60,
                    "start_period": 10_000_000_000,
                },
                user="10001:10001",
                detach=True,
            )
            self._containers["api"] = container
            container.start()
            self.api_base = self._published_url(container, "8020/tcp")

        self._wait_healthy(container, timeout_seconds=timeout_seconds, role="api")
        self._wait_http_ready(self.api_base, timeout_seconds=timeout_seconds)
        return container.id

    def start_worker(self) -> str:
        if self._client is None or self._network is None:
            raise RuntimeError("LifecycleDockerStack must be entered before starting services")
        if not self.api_base:
            raise RuntimeError("start_api() must complete before start_worker()")
        container = self._containers.get("worker")
        if container is None:
            env = dict(self.environment)
            # Trusted control processes share the API network namespace. This
            # gives the Worker a route to the owned per-Attempt internal bridge
            # while retaining its two Docker members: API witness and executor.
            env.update(
                {
                    "APP_MODULE": "app.main:create_app",
                    "APP_PORT": "8020",
                    "WORKER_ROLE": "integrated",
                    "WORKER_CONCURRENCY": "1",
                    "DOCKER_HOST": "unix:///socket/docker.sock",
                    "SANDBOX_EXECUTOR_PUBLISHED_HOST": "127.0.0.1",
                    "SANDBOX_WORKSPACE_ROOT": f"{self.workspace_mountpoint}/s",
                    "CLAUDE_AGENT_WORKSPACE_ROOT": f"{self.workspace_mountpoint}/agent",
                }
            )
            volumes = {
                self.workspace_volume_name: {
                    "bind": self.workspace_mountpoint,
                    "mode": "rw",
                },
                self.socket_volume_name: {"bind": "/socket", "mode": "rw"},
            }
            container = self._client.containers.create(
                self.image_id,
                name=f"{self.name_prefix}-worker",
                command=["python", "-m", "app.worker_main", "--timeout", "1"],
                environment=env,
                labels=self._labels(
                    **{"ai-platform.lifecycle-test.resource": "worker"}
                ),
                network_mode=f"container:{self._containers['api'].id}",
                volumes=volumes,
                user="10001:10001",
                detach=True,
            )
            self._containers["worker"] = container
            container.start()
        else:
            container.reload()
            if container.status != "running":
                container.start()
        return container.id

    def stop_worker(self, *, timeout_seconds: int = 20) -> str:
        container = self._containers.get("worker")
        if container is None:
            return ""
        container.reload()
        if container.status == "running":
            container.stop(timeout=timeout_seconds)
        container.reload()
        return container.id

    def kill_worker(self) -> str:
        container = self._containers.get("worker")
        if container is None:
            raise RuntimeError("lifecycle Worker has not been started")
        container.reload()
        if container.status == "running":
            container.kill(signal="SIGKILL")
            container.wait(timeout=30)
        container.reload()
        return container.id

    def capture_worker_logs(self, checkpoint: str) -> str:
        container = self._containers.get("worker")
        if container is None:
            raise RuntimeError("lifecycle Worker has not been started")
        safe_checkpoint = _safe_name(checkpoint)
        if not safe_checkpoint:
            raise ValueError("worker log checkpoint is required")
        container.reload()
        log_name = f"docker-worker-{safe_checkpoint}.log"
        self._capture_container_log(f"worker-{safe_checkpoint}", container)
        log_path = self.evidence_dir / log_name
        if not log_path.is_file():
            raise RuntimeError(f"lifecycle Worker logs were not captured: {log_name}")
        return log_name

    def assert_no_run_resources(self, run_id: str) -> None:
        if self._client is None:
            raise RuntimeError("LifecycleDockerStack is not active")
        run_id = str(run_id).strip()
        if not run_id:
            raise ValueError("run_id is required")
        filters = {"label": f"{_RUN_LABEL}={run_id}"}
        containers = self._client.containers.list(all=True, filters=filters)
        networks = self._client.networks.list(filters=filters)
        if containers or networks:
            names = [
                str(getattr(item, "name", None) or getattr(item, "id", ""))
                for item in [*containers, *networks]
            ]
            raise AssertionError(
                f"Docker resources remain for run {run_id}: {', '.join(names)}"
            )

    def capture_run_logs(self, run_id: str) -> None:
        """Retain diagnostics before production cleanup removes a failed runtime."""
        assert self._client is not None
        for container in self._client.containers.list(
            all=True, sparse=True, filters={"label": [f"{_RUN_LABEL}={run_id}", f"{_WORKSPACE_LABEL}={self.workspace_id}"]},
        ):
            self._capture_container_log(f"run-{container.id[:12]}", container)

    def _container_environment(self) -> dict[str, str]:
        suffix = self.scope_id[:12]
        internal_postgres = (
            f"postgresql://{self._postgres_user}:{quote(self._postgres_password, safe='')}"
            f"@{self.name_prefix}-postgres:5432/{self._postgres_database}"
        )
        return {
            "DATABASE_URL": internal_postgres,
            "REDIS_URL": f"redis://{self.name_prefix}-redis:6379/0",
            "S3_ENDPOINT_URL": f"http://{self.name_prefix}-minio:9000",
            "S3_ACCESS_KEY_ID": self.s3_access_key,
            "S3_SECRET_ACCESS_KEY": self.s3_secret_key,
            "S3_BUCKET": self.bucket,
            "S3_REGION": "us-east-1",
            "TRUSTED_PRINCIPAL_SECRET": self.gateway_secret,
            "AI_SESSION_SECRET": self.session_secret,
            "AUTH_CONTEXT_SECRET": self.gateway_secret,
            "MODEL_PROXY_INTERNAL_TOKEN": self.gateway_secret,
            "MODEL_CONNECTION_ENCRYPTION_KEY": self.encryption_key,
            "EXISTING_USER_INFO_BASE_URL": f"http://{self.name_prefix}-identity:8080",
            "SANDBOX_CONTAINER_PROVIDER": "docker",
            "SANDBOX_SECURITY_PROFILE": "governed",
            "SANDBOX_EGRESS_POLICY_ENABLED": "true",
            "SANDBOX_EGRESS_PROOF_SIGNING_KEY": self.egress_proof_signing_key,
            "AI_PLATFORM_RUNTIME_COMMIT": self.source_commit,
            "SANDBOX_EXECUTOR_IMAGE": self.image_id,
            "SANDBOX_EXECUTOR_PUBLISHED_HOST": "",
            "SANDBOX_CALLBACK_BASE_URL": "http://api.sandbox.internal:8020",
            "SANDBOX_CALLBACK_TOKEN": self.gateway_secret,
            "CLAUDE_AGENT_SDK_ENABLED": "true",
            "CLAUDE_AGENT_SDK_TIMEOUT_SECONDS": "120",
            "CLAUDE_AGENT_SDK_MAX_TURNS": "8",
            "CLAUDE_AGENT_WORKSPACE_ROOT": "/tmp/ai-platform-agent-workspaces",
            "SANDBOX_WORKSPACE_ROOT": "/tmp/ai-platform-sandbox-workspaces",
            "QUEUE_KEY_PREFIX": f"ai-platform:lifecycle:{suffix}",
            "DEFAULT_TENANT_ID": self.tenant_id,
            "APP_PORT": "8020",
        }

    def _labels(self, **extra: str) -> dict[str, str]:
        labels = {
            _LIFECYCLE_LABEL: self.scope_id,
            _LIFECYCLE_SCOPE_LABEL: self.workspace_id,
            _WORKSPACE_LABEL: self.workspace_id,
            "ai-platform.lifecycle-test.source-commit": self.source_commit,
        }
        labels.update(extra)
        return labels

    def _create_network(self) -> None:
        assert self._client is not None
        self._network = self._client.networks.create(
            self.network_name,
            driver="bridge",
            labels=self._labels(**{"ai-platform.lifecycle-test.resource": "network"}),
            check_duplicate=True,
        )
        self._network.reload()
        config = self._network.attrs.get("IPAM", {}).get("Config") or []
        self.network_gateway = next(
            (
                str(item.get("Gateway") or "")
                for item in config
                if isinstance(item, dict) and item.get("Gateway")
            ),
            "",
        )
        if not self.network_gateway:
            raise RuntimeError("lifecycle Docker bridge has no gateway")
        self.environment["SANDBOX_EXECUTOR_PUBLISHED_HOST"] = self.network_gateway

    def _create_data_services(self) -> None:
        from docker.errors import ImageNotFound

        assert self._client is not None
        services = (
            (
                "postgres",
                "postgres:16-alpine",
                {
                    "POSTGRES_DB": self._postgres_database,
                    "POSTGRES_USER": self._postgres_user,
                    "POSTGRES_PASSWORD": self._postgres_password,
                },
                "5432/tcp",
                ["postgres", "-c", "listen_addresses=*"],
            ),
            (
                "redis",
                "redis:7.4-alpine",
                {},
                "6379/tcp",
                ["redis-server", "--save", "", "--appendonly", "no"],
            ),
            (
                "minio",
                "bitnamilegacy/minio:2025.4.22-debian-12-r1",
                {
                    "MINIO_ROOT_USER": self._minio_root_user,
                    "MINIO_ROOT_PASSWORD": self._minio_root_password,
                },
                "9000/tcp",
                None,
            ),
        )
        for role, image, environment, port, command in services:
            try:
                self._client.images.get(image)
            except ImageNotFound:
                _run_build(
                    ["docker", "pull", image],
                    self.evidence_dir / f"docker-pull-{role}.log",
                )
            ports = {port: ("127.0.0.1", None)}
            container = self._client.containers.create(
                image,
                name=f"{self.name_prefix}-{role}",
                command=command,
                environment=environment,
                labels=self._labels(
                    **{"ai-platform.lifecycle-test.resource": role}
                ),
                network=self.network_name,
                ports=ports,
                detach=True,
            )
            self._containers[role] = container
            container.start()
            self._port_map[role] = self._published_port(container, port)

        self.postgres_dsn = (
            f"postgresql://{self._postgres_user}:{quote(self._postgres_password, safe='')}"
            f"@127.0.0.1:{self._port_map['postgres']}/{self._postgres_database}"
        )
        self.redis_url = f"redis://127.0.0.1:{self._port_map['redis']}/0"
        self.s3_endpoint = f"http://127.0.0.1:{self._port_map['minio']}"
        self._wait_postgres()
        self._wait_redis()
        self._wait_minio()
        identity = self._client.containers.create(
            self.image_id, name=f"{self.name_prefix}-identity",
            entrypoint=["python", "/app/run_lifecycle_identity.py"], command=[],
            environment={"LIFECYCLE_USER_ID": self.user_id},
            labels=self._labels(**{"ai-platform.lifecycle-test.resource": "identity"}),
            network=self.network_name, user="10001:10001", detach=True,
        )
        self._containers["identity"] = identity
        identity.start()

    def _create_workspace_volume(self) -> None:
        assert self._client is not None
        self._workspace_volume = self._client.volumes.create(
            name=self.workspace_volume_name,
            labels=self._labels(
                **{"ai-platform.lifecycle-test.resource": "workspace-volume"}
            ),
        )
        self._workspace_volume.reload()
        if not self._owns(self._workspace_volume.attrs.get("Labels") or {}):
            raise RuntimeError("lifecycle workspace volume ownership mismatch")
        self.workspace_mountpoint = str(
            self._workspace_volume.attrs.get("Mountpoint") or ""
        )
        if not self.workspace_mountpoint.startswith("/"):
            raise RuntimeError("lifecycle workspace Docker volume has no absolute mountpoint")

    def _create_docker_socket_volume(self) -> None:
        assert self._client is not None
        self._socket_volume = self._client.volumes.create(
            name=self.socket_volume_name,
            labels=self._labels(
                **{"ai-platform.lifecycle-test.resource": "docker-socket-volume"}
            ),
        )

    def _initialize_workspace_owner(self) -> None:
        assert self._client is not None
        name = f"{self.name_prefix}-workspace-init"
        container = self._client.containers.create(
            self.image_id,
            name=name,
            command=["-R", "10001:10001", "/lifecycle-workspace"],
            entrypoint=["/usr/bin/chown"],
            labels=self._labels(
                **{"ai-platform.lifecycle-test.resource": "workspace-init"}
            ),
            network_mode="none",
            volumes={
                self.workspace_volume_name: {
                    "bind": "/lifecycle-workspace",
                    "mode": "rw",
                }
            },
            user="0:0",
            detach=True,
        )
        self._containers["workspace-init"] = container
        container.start()
        result = container.wait(timeout=30)
        if int(result.get("StatusCode", 1)) != 0:
            self._capture_container_log("workspace-init", container)
            raise RuntimeError("lifecycle workspace ownership initialization failed")

    def _start_socket_proxy(self) -> None:
        assert self._client is not None
        container = self._client.containers.create(
            self.image_id,
            name=f"{self.name_prefix}-docker-proxy",
            entrypoint=["python", "/app/run_lifecycle_docker_proxy.py"],
            command=["--socket-path", "/socket/docker.sock", "--uid", "10001", "--gid", "10001"],
            labels=self._labels(
                **{"ai-platform.lifecycle-test.resource": "docker-socket-proxy"}
            ),
            network_mode="none",
            volumes={
                self._docker_socket_source: {
                    "bind": "/var/run/docker.sock",
                    "mode": "rw",
                },
                self.socket_volume_name: {"bind": "/socket", "mode": "rw"},
            },
            user="0:0",
            detach=True,
        )
        self._containers["docker-proxy"] = container
        container.start()
        self._wait_log_marker(container, "lifecycle-docker-proxy-ready", timeout_seconds=30)

    def _resolve_docker_socket_source(self) -> str:
        assert self._client is not None
        adapter = getattr(self._client.api, "_custom_adapter", None)
        socket_path = str(getattr(adapter, "socket_path", "") or "").strip()
        if not socket_path.startswith("/"):
            raise RuntimeError("lifecycle Docker proxy requires a local Unix Docker socket")
        source = Path(socket_path).expanduser().resolve()
        if not source.exists():
            raise RuntimeError("lifecycle Docker Unix socket is unavailable")
        # The mount is resolved by the daemon, not by this macOS/Linux client.
        # Docker Desktop exposes its VM's Engine socket at this standard path.
        return "/var/run/docker.sock"

    def _create_artifact_bucket(self) -> None:
        import boto3
        from botocore.config import Config

        client = boto3.client(
            "s3",
            endpoint_url=self.s3_endpoint,
            aws_access_key_id=self.s3_access_key,
            aws_secret_access_key=self.s3_secret_key,
            region_name="us-east-1",
            config=Config(proxies={}),
        )
        try:
            client.create_bucket(Bucket=self.bucket)
        except Exception:
            self._capture_text_log(
                "startup-bucket-error.txt",
                "MinIO bucket initialization failed; synthetic credentials are omitted.\n",
            )
            raise RuntimeError("lifecycle MinIO bucket initialization failed") from None

    def _wait_postgres(self, *, timeout_seconds: float = 60.0) -> None:
        container = self._containers["postgres"]
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            container.reload()
            if container.status != "running":
                self._capture_container_log("postgres", container)
                raise RuntimeError("lifecycle PostgreSQL stopped during startup")
            result = container.exec_run(
                [
                    "pg_isready",
                    "-U",
                    self._postgres_user,
                    "-d",
                    self._postgres_database,
                ],
                demux=True,
            )
            if result.exit_code == 0:
                return
            time.sleep(0.25)
        raise TimeoutError("lifecycle PostgreSQL did not become ready")

    def _wait_redis(self, *, timeout_seconds: float = 45.0) -> None:
        container = self._containers["redis"]
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            container.reload()
            if container.status != "running":
                self._capture_container_log("redis", container)
                raise RuntimeError("lifecycle Redis stopped during startup")
            result = container.exec_run(["redis-cli", "ping"], demux=True)
            if result.exit_code == 0 and b"PONG" in (result.output[0] or b""):
                return
            time.sleep(0.25)
        raise TimeoutError("lifecycle Redis did not become ready")

    def _wait_minio(self, *, timeout_seconds: float = 60.0) -> None:
        self._wait_http_url(f"{self.s3_endpoint}/minio/health/ready", timeout_seconds)

    def _wait_healthy(
        self, container: Any, *, timeout_seconds: float, role: str
    ) -> None:
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            container.reload()
            if container.status != "running":
                self._capture_container_log(role, container)
                raise RuntimeError(f"lifecycle {role} container exited before healthcheck passed")
            health = (
                container.attrs.get("State", {}).get("Health", {}).get("Status")
            )
            if health == "healthy":
                return
            if health == "unhealthy":
                self._capture_container_log(role, container)
                raise RuntimeError(f"lifecycle {role} Docker healthcheck failed")
            time.sleep(0.25)
        self._capture_container_log(role, container)
        raise TimeoutError(f"lifecycle {role} Docker healthcheck timed out")

    def _wait_http_ready(self, base_url: str, *, timeout_seconds: float) -> None:
        deadline = time.monotonic() + timeout_seconds
        url = f"{base_url}/api/ai/ready"
        last_status = 0
        while time.monotonic() < deadline:
            try:
                with _direct_urlopen(url, timeout=2) as response:
                    last_status = response.status
                    payload = json.loads(response.read())
                    if (
                        last_status == 200
                        and payload.get("status") == "ready"
                        and payload.get("dependencies", {}).get("postgresql") == "ok"
                        and payload.get("dependencies", {}).get("schema") == "ok"
                        and payload.get("dependencies", {}).get("redis") == "ok"
                    ):
                        return
            except (HTTPError, URLError, TimeoutError, json.JSONDecodeError):
                pass
            time.sleep(0.25)
        raise TimeoutError(
            f"lifecycle API readiness did not converge (last HTTP status {last_status})"
        )

    def _wait_http_url(self, url: str, timeout_seconds: float) -> None:
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            try:
                with _direct_urlopen(url, timeout=2) as response:
                    if response.status == 200:
                        return
            except (HTTPError, URLError, TimeoutError):
                pass
            time.sleep(0.25)
        raise TimeoutError("lifecycle object storage did not become ready")

    def _wait_log_marker(self, container: Any, marker: str, *, timeout_seconds: float) -> None:
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            container.reload()
            if container.status != "running":
                self._capture_container_log("docker-proxy", container)
                raise RuntimeError("lifecycle Docker socket proxy exited during startup")
            output = container.logs(stdout=True, stderr=True, tail=100)
            if marker.encode("ascii") in output:
                return
            time.sleep(0.1)
        self._capture_container_log("docker-proxy", container)
        raise TimeoutError("lifecycle Docker socket proxy did not become ready")

    def _published_port(self, container: Any, port: str) -> str:
        container.reload()
        bindings = container.attrs.get("NetworkSettings", {}).get("Ports", {}).get(port)
        if not isinstance(bindings, list) or len(bindings) != 1:
            raise RuntimeError(f"lifecycle {port} has no unique published port")
        binding = bindings[0]
        host_ip = str(binding.get("HostIp") or "")
        host_port = str(binding.get("HostPort") or "")
        if host_ip not in {"127.0.0.1", "::1"} or not host_port.isdecimal():
            raise RuntimeError(f"lifecycle {port} is not published on a loopback port")
        return host_port

    def _published_url(self, container: Any, port: str) -> str:
        return f"http://127.0.0.1:{self._published_port(container, port)}"

    def _capture_container_log(self, name: str, container: Any) -> None:
        try:
            raw = container.logs(stdout=True, stderr=True, timestamps=True)
        except Exception:
            return
        self._capture_text_log(f"docker-{_safe_name(name)}.log", self._redact(raw.decode("utf-8", errors="replace")))

    def _capture_text_log(self, name: str, content: str) -> None:
        path = self.evidence_dir / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def _redact(self, text: str) -> str:
        for secret in (
            self.gateway_secret,
            self.session_secret,
            self.encryption_key,
            self.egress_proof_signing_key,
            self.s3_access_key,
            self.s3_secret_key,
            self._postgres_password,
            self._minio_root_password,
        ):
            if secret:
                text = text.replace(secret, "[REDACTED]")
        return text

    def _labels_for_workspace(self) -> dict[str, str]:
        return {"label": f"{_WORKSPACE_LABEL}={self.workspace_id}"}

    def _cleanup(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._client is None:
            return

        failures: list[str] = []
        for role in ("worker", "api"):
            container = self._containers.get(role)
            if container is not None:
                self._stop_and_capture(role, container, failures)
                self._remove_container(role, container, failures)

        self._remove_residual_run_resources(failures)

        for role in ("postgres", "redis", "minio", "identity"):
            container = self._containers.get(role)
            if container is not None:
                self._remove_container(role, container, failures)

        for role in ("workspace-init", "docker-proxy"):
            container = self._containers.get(role)
            if container is not None:
                self._remove_container(role, container, failures)

        self._remove_untracked_scope_resources(failures)

        report = self._cleanup_inventory(failures)
        if failures or report["remaining"]:
            self._capture_text_log(
                "docker-cleanup.json",
                json.dumps(report, indent=2, sort_keys=True) + "\n",
            )
            self._client.close()
            self._client = None
            raise RuntimeError(
                f"Lifecycle Docker cleanup incomplete; see {self.evidence_dir / 'docker-cleanup.json'}"
            )
        self._capture_text_log(
            "docker-cleanup.json",
            json.dumps(report, indent=2, sort_keys=True) + "\n",
        )
        self._client.close()
        self._client = None

    def _remove_residual_run_resources(self, failures: list[str]) -> None:
        assert self._client is not None
        filters = self._labels_for_workspace()
        try:
            containers = self._client.containers.list(all=True, filters=filters)
            for container in containers:
                labels = container.labels or {}
                if (
                    str(labels.get(_WORKSPACE_LABEL) or "") != self.workspace_id
                    or labels.get("ai-platform.owner")
                    not in {"sandbox-runtime", "sandbox-native-tool"}
                ):
                    continue
                name = str(container.name or container.id)
                self._remove_residual_container(f"residual-{name}", container, failures)
        except Exception as exc:
            failures.append(f"residual workspace containers: {type(exc).__name__}")
        try:
            networks = self._client.networks.list(filters=filters)
            for network in networks:
                labels = network.attrs.get("Labels") or {}
                if (
                    str(labels.get(_WORKSPACE_LABEL) or "") != self.workspace_id
                    or labels.get("ai-platform.owner")
                    != "sandbox-runtime-governed-egress-v2"
                ):
                    continue
                name = str(network.name or network.id)
                try:
                    network.remove()
                except Exception as exc:
                    failures.append(f"residual network {name}: {type(exc).__name__}")
        except Exception as exc:
            failures.append(f"residual workspace networks: {type(exc).__name__}")

    def _remove_residual_container(
        self, role: str, container: Any, failures: list[str]
    ) -> None:
        try:
            container.reload()
            labels = container.labels or {}
            if (
                str(labels.get(_WORKSPACE_LABEL) or "") != self.workspace_id
                or labels.get("ai-platform.owner")
                not in {"sandbox-runtime", "sandbox-native-tool"}
            ):
                failures.append(f"ownership changed for {role}")
                return
            self._capture_container_log(role, container)
            container.remove(force=True, v=True)
        except Exception as exc:
            failures.append(f"remove {role}: {type(exc).__name__}")

    def _remove_untracked_scope_resources(self, failures: list[str]) -> None:
        """Recover resources created by this fixture even after a lost API reply."""

        assert self._client is not None
        filters = {"label": f"{_LIFECYCLE_SCOPE_LABEL}={self.workspace_id}"}
        try:
            for container in self._client.containers.list(all=True, filters=filters):
                if self._owns(container.labels or {}):
                    self._remove_container(
                        f"untracked-{container.name or container.id}",
                        container,
                        failures,
                    )
        except Exception as exc:
            failures.append(f"untracked scope containers: {type(exc).__name__}")
        try:
            for network in self._client.networks.list(filters=filters):
                if self._owns(network.attrs.get("Labels") or {}):
                    try:
                        network.remove()
                    except Exception as exc:
                        failures.append(
                            f"untracked scope network {network.name}: {type(exc).__name__}"
                        )
        except Exception as exc:
            failures.append(f"untracked scope networks: {type(exc).__name__}")
        try:
            for volume in self._client.volumes.list(filters=filters):
                if self._owns(volume.attrs.get("Labels") or {}):
                    try:
                        volume.remove(force=True)
                    except Exception as exc:
                        failures.append(
                            f"untracked scope volume {volume.name}: {type(exc).__name__}"
                        )
        except Exception as exc:
            failures.append(f"untracked scope volumes: {type(exc).__name__}")

    def _stop_and_capture(self, role: str, container: Any, failures: list[str]) -> None:
        try:
            container.reload()
            if container.status == "running":
                container.stop(timeout=20)
            container.reload()
            self._capture_container_log(role, container)
        except Exception as exc:
            failures.append(f"stop {role}: {type(exc).__name__}")
            try:
                container.kill(signal="SIGKILL")
                container.wait(timeout=20)
                self._capture_container_log(role, container)
            except Exception as kill_error:
                failures.append(f"force stop {role}: {type(kill_error).__name__}")

    def _remove_container(self, role: str, container: Any, failures: list[str]) -> None:
        try:
            container.reload()
            if not self._owns(container.labels or {}):
                failures.append(f"ownership changed for {role}")
                return
            self._capture_container_log(role, container)
            container.remove(force=True, v=True)
        except Exception as exc:
            failures.append(f"remove {role}: {type(exc).__name__}")

    def _owns(self, labels: dict[str, Any]) -> bool:
        return (
            str(labels.get(_LIFECYCLE_LABEL) or "") == self.scope_id
            and str(labels.get(_LIFECYCLE_SCOPE_LABEL) or "") == self.workspace_id
        )

    def _cleanup_inventory(self, failures: list[str]) -> dict[str, Any]:
        assert self._client is not None
        remaining: list[dict[str, str]] = []
        filters = self._labels_for_workspace()
        try:
            for item in self._client.containers.list(all=True, filters=filters):
                labels = item.labels or {}
                if self._owns(labels) or labels.get("ai-platform.owner") in {
                    "sandbox-runtime",
                    "sandbox-native-tool",
                }:
                    remaining.append(
                        {"kind": "container", "id": item.id, "name": item.name}
                    )
            for item in self._client.networks.list(filters=filters):
                labels = item.attrs.get("Labels") or {}
                if self._owns(labels) or labels.get("ai-platform.owner") == (
                    "sandbox-runtime-governed-egress-v2"
                ):
                    remaining.append(
                        {"kind": "network", "id": item.id, "name": item.name}
                    )
            for item in self._client.volumes.list(filters=filters):
                if self._owns(item.attrs.get("Labels") or {}):
                    remaining.append(
                        {"kind": "volume", "name": item.name}
                    )
        except Exception as exc:
            failures.append(f"final inventory: {type(exc).__name__}")
        return {
            "schema_version": "ai-platform.lifecycle-docker-cleanup.v1",
            "workspace_id": self.workspace_id,
            "failures": list(failures),
            "remaining": remaining,
            "production_release_cleanup_claimed": False,
        }

    def _write_cleanup_failure(self, error: BaseException) -> None:
        self._capture_text_log(
            "docker-cleanup-error.txt",
            self._redact(f"{type(error).__name__}: {error}\n"),
        )
