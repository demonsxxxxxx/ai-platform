#!/usr/bin/env python3
"""Deploy an official Compose package. No Git checkout or GitHub API is required."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import json
import os
from pathlib import Path
import shlex
import signal
import stat
import subprocess
import sys
import time

# Filled by release_compose_package.py, never operator configuration.
COMMIT = "@@SOURCE_COMMIT@@"
BACKEND = "@@BACKEND_IMAGE@@"
FRONTEND = "@@FRONTEND_IMAGE@@"
PROJECT = "ai-platform-internal"
DATA = ("postgres", "redis", "minio")
APPS = ("frontend", "api", "worker")

QUIESCENCE_SQL = """select
(select count(*) from runs where status not in ('succeeded','failed','cancelled')),
(select count(*) from run_attempts where status not in ('succeeded','failed','cancelled')),
(select count(*) from sandbox_leases l where status <> 'released' and not coalesce(
  l.status = 'quarantined' and l.expires_at < CURRENT_TIMESTAMP
  and l.executor_status in ('completed','failed')
  and l.executor_reconciliation_status = 'failed'
  and l.executor_reconciliation_claim_token is null
  and coalesce(nullif(trim(l.runtime_container_id),''), nullif(trim(l.runtime_container_name),'')) is not null
  and exists (select 1 from runs r where r.id=l.run_id and r.tenant_id=l.tenant_id
              and r.status in ('succeeded','failed','cancelled')),
  false));"""
HEALTH_PROBE = """
import json, sys, urllib.request
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
for path, status in (('health', 'ok'), ('ready', 'ready')):
    with opener.open('http://127.0.0.1:8020/api/ai/' + path, timeout=10) as response:
        value = json.loads(response.read(65537))
    assert value['status'] == status
    if path == 'ready':
        assert value['runtime_commit'] == sys.argv[1]
"""
OPENSANDBOX_PROBE = """
import os, urllib.request
base = os.environ.get('OPENSANDBOX_BASE_URL', '').strip() or (
    os.environ['OPENSANDBOX_PROTOCOL'] + '://' + os.environ['OPENSANDBOX_DOMAIN'])
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
with opener.open(base.rstrip('/') + '/health', timeout=10) as response:
    assert response.status == 200
"""
HEARTBEAT_PROBE = """
from datetime import datetime
import json, os, sys, time
from pathlib import Path
p = json.loads((Path(os.environ.get('TMPDIR') or '/tmp') /
    'ai-platform-worker-runtime-heartbeat.json').read_text())
assert p['schema_version'] == 'ai-platform.worker-runtime-heartbeat.v1'
assert p['runtime_commit'] == sys.argv[1]
assert type(p['pid']) is int and p['pid'] > 0
assert isinstance(p['worker_id'], str) and p['worker_id']
observed = datetime.fromisoformat(p['observed_at'])
assert observed.tzinfo is not None
assert -5 <= time.time() - observed.timestamp() <= 30
os.kill(p['pid'], 0)
print(json.dumps([p['worker_id'], p['pid'], observed.timestamp()]))
"""


class DeploymentError(Exception):
    pass


def run(command: list[str], stage: str, timeout: int = 90) -> str:
    # Docker/Compose output may contain expanded secrets. Never echo it or argv.
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        raise DeploymentError(f"{stage}: command unavailable or timed out") from None
    if result.returncode:
        raise DeploymentError(f"{stage}: command failed (exit {result.returncode})")
    return result.stdout.strip()


def inspect(docker: list[str], name: str) -> dict:
    return json.loads(run([*docker, "inspect", name], "container inspection"))[0]


def quiescent(docker: list[str]) -> None:
    counts = run([
        *docker, "exec", "ai-platform-postgres", "sh", "-ceu",
        'psql -v ON_ERROR_STOP=1 -At -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "$1"',
        "sh", QUIESCENCE_SQL,
    ], "activity check", 30)
    if counts != "0|0|0":
        raise DeploymentError("active Run, Attempt or unreleased lease blocks deployment")
    # Quarantine explicitly means unverifiable, not released. Even an expired
    # terminal record blocks if its recorded container still exists (orphan labels
    # alone cannot establish absence). Keep the DB record unchanged.
    handles = run([
        *docker, "exec", "ai-platform-postgres", "sh", "-ceu",
        'psql -v ON_ERROR_STOP=1 -At -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "$1"',
        "sh", "select coalesce(runtime_container_id,'') || '|' || coalesce(runtime_container_name,'') from sandbox_leases where status='quarantined';",
    ], "quarantined runtime check", 30)
    inventory = run([*docker, "ps", "-a", "--no-trunc", "--format", "{{.ID}}|{{.Names}}"], "sandbox inventory")
    existing = {value for line in inventory.splitlines() for value in line.split("|") if value}
    if any(value in existing for line in handles.splitlines() for value in line.split("|") if value):
        raise DeploymentError("quarantined sandbox still exists")
    for owner in ("sandbox-runtime", "sandbox-native-tool"):
        if run([*docker, "ps", "-aq", "--filter", f"label=ai-platform.owner={owner}"], "sandbox check"):
            raise DeploymentError("sandbox containers block deployment")


def snapshot(docker: list[str]) -> dict:
    records = {}
    names = run([*docker, "ps", "-a", "--format", "{{.Names}}"], "container inventory").splitlines()
    for service in (*DATA, *APPS):
        name = f"ai-platform-{service}"
        if name not in names:
            continue
        record = inspect(docker, name)
        labels = record["Config"].get("Labels") or {}
        if labels.get("com.docker.compose.project") != PROJECT or labels.get("com.docker.compose.service") != service:
            raise DeploymentError("existing container belongs to another deployment")
        records[service] = record
    if records and set(records) != set((*DATA, *APPS)):
        raise DeploymentError("partial existing stack requires recovery, not a normal upgrade")
    return records


def verify_runtime(docker: list[str], image_ids: dict[str, str], before: dict) -> None:
    for service in (*DATA, *APPS):
        record = inspect(docker, f"ai-platform-{service}")
        state = record["State"]
        if not state["Running"] or (service != "worker" and state.get("Health", {}).get("Status") != "healthy"):
            raise DeploymentError("service health did not converge")
        if service in DATA:
            if before and any(record[key] != before[service][key] for key in ("Id", "Mounts", "RestartCount")):
                raise DeploymentError("persistent service identity changed")
        else:
            target = FRONTEND if service == "frontend" else BACKEND
            if record["Image"] != image_ids[target] or record["Config"]["Labels"].get("ai-platform.source-commit") != COMMIT:
                raise DeploymentError("application image or commit mismatch")
    for service in ("migrate", "workspace-init"):
        state = inspect(docker, f"ai-platform-{service}")["State"]
        if state["Status"] != "exited" or state["ExitCode"] != 0:
            raise DeploymentError("migration or workspace initialization failed")
    run([*docker, "exec", "ai-platform-api", "python", "-B", "-c", HEALTH_PROBE, COMMIT], "API readiness", 30)
    for service in ("api", "worker"):
        run([*docker, "exec", f"ai-platform-{service}", "python", "-B", "-c", OPENSANDBOX_PROBE], "OpenSandbox reachability", 30)
    command = [*docker, "exec", "ai-platform-worker", "python", "-B", "-c", HEARTBEAT_PROBE, COMMIT]
    first = json.loads(run(command, "Worker heartbeat"))
    time.sleep(15)
    second = json.loads(run(command, "Worker heartbeat"))
    if first[:2] != second[:2] or second[2] <= first[2]:
        raise DeploymentError("Worker heartbeat did not advance with stable identity")


def deploy(package: Path, env: Path, docker: list[str], offline: bool, check_only: bool = False) -> None:
    if "@@" in COMMIT + BACKEND + FRONTEND:
        raise DeploymentError("use the published deployment package, not the source template")
    compose = [*docker, "compose", "--project-name", PROJECT, "--env-file", str(env),
               "-f", str(package / "compose.yaml"), "-f", str(package / "compose.override.yaml")]
    run([*compose, "config", "--quiet"], "configuration")
    config = json.loads(run([*compose, "config", "--format", "json"], "configuration identity"))
    for service in ("api", "worker", "migrate", "workspace-init", "frontend"):
        expected = FRONTEND if service == "frontend" else BACKEND
        if config["services"][service]["image"] != expected:
            raise DeploymentError("Compose image does not match this release")
    before = snapshot(docker)
    if before:
        quiescent(docker)
    run(["systemctl", "is-active", "--quiet", "opensandbox.service"], "OpenSandbox host prerequisite", 15)
    references = {entry["image"] for entry in config["services"].values()}
    if any("@sha256:" not in reference for reference in references):
        raise DeploymentError("all packaged images must be digest-qualified")
    if not offline and not check_only:
        run([*compose, "pull"], "image download", 1800)
    image_ids = {}
    for reference in references:
        image = json.loads(run([*docker, "image", "inspect", reference], "local image verification"))[0]
        if reference not in (image.get("RepoDigests") or []):
            raise DeploymentError("local image lacks the expected repository digest")
        image_ids[reference] = image["Id"]
    print("preflight: ok", flush=True)
    if check_only:
        return
    stopped = []
    schema_started = False
    try:
        if before:
            for service in APPS:
                name = f"ai-platform-{service}"
                run([*docker, "stop", "--time", "30", name], "admission stop")
                stopped.append(name)
            quiescent(docker)
        run([*compose, "up", "-d", "--no-recreate", "--pull", "never", "--wait", *DATA], "persistent services", 180)
        schema_started = True
        run([*compose, "up", "--no-deps", "--force-recreate", "--pull", "never", "--exit-code-from", "migrate", "migrate"], "schema migration", 600)
        run([*compose, "up", "--no-deps", "--force-recreate", "--pull", "never", "--exit-code-from", "workspace-init", "workspace-init"], "workspace initialization", 180)
        services = [name for name in config["services"] if name not in (*DATA, "migrate", "workspace-init")]
        run([*compose, "up", "-d", "--no-deps", "--pull", "never", "--wait", "--wait-timeout", "180", *services], "application startup", 240)
        verify_runtime(docker, image_ids, before)
    except BaseException:
        if not schema_started:
            for name in reversed(stopped):
                run([*docker, "start", name], "restore pre-migration admission")
        else:
            # No speculative binary rollback against a potentially changed schema.
            for service in APPS:
                try:
                    run([*docker, "stop", "--time", "30", f"ai-platform-{service}"], "failed-deployment admission stop")
                except DeploymentError:
                    print("warning: admission stop needs operator verification", file=sys.stderr)
            print("Deployment stopped after migration began; data retained, no automatic database or image rollback.", file=sys.stderr)
        raise
    print(f"deployment: healthy ({COMMIT})", flush=True)


@contextmanager
def protected_environment(path: Path):
    # Pin the opened inode through Linux procfs for every Compose operation.
    # No on-disk copy of a real environment file is created.
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(descriptor, "rb") as source:
        metadata = os.fstat(source.fileno())
        if not stat.S_ISREG(metadata.st_mode) or stat.S_IMODE(metadata.st_mode) != 0o600 or metadata.st_uid != os.geteuid():
            raise DeploymentError("configuration must be owner-held with mode 0600")
        yield Path(f"/proc/{os.getpid()}/fd/{source.fileno()}")


@contextmanager
def deployment_lock(path: Path):
    import fcntl

    descriptor = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        os.close(descriptor)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument("--docker-cmd", default="docker")
    parser.add_argument("--offline", action="store_true", help="use already loaded, verified images without pulling")
    parser.add_argument("--check", action="store_true", help="verify config, activity and cached images only; no pulls or service changes")
    args = parser.parse_args()
    env = args.env_file.absolute()
    def interrupt(_signum, _frame):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, interrupt)
    try:
        # One project-wide lock even when packages/configs reside in different directories.
        with deployment_lock(Path("/tmp/ai-platform-internal-deploy.lock")), protected_environment(env) as snapshot_env:
            deploy(Path(__file__).resolve().parent, snapshot_env, shlex.split(args.docker_cmd), args.offline, args.check)
    except (DeploymentError, OSError, ValueError, KeyError, KeyboardInterrupt) as exc:
        print(str(exc) if isinstance(exc, DeploymentError) else "deployment failed: invalid input, lock unavailable or interrupted", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
