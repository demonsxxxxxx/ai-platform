"""Remove only the repository-managed production OpenSandbox server container."""

from __future__ import annotations

import json
import re
import subprocess
import sys
from typing import Any, Callable, Sequence


if __name__ == "__main__" and not sys.flags.isolated:
    raise SystemExit("run the OpenSandbox unit guard with Python isolated mode")

DOCKER = "/usr/bin/docker"
SERVER_CONTAINER = "ai-platform-opensandbox-server"
COMMIT_RE = re.compile(r"[0-9a-f]{40}\Z")
IMAGE_RE = re.compile(r"[^\s@]+@sha256:[0-9a-f]{64}\Z")
IDENTITY_LABELS = {
    "ai-platform.release-owner": "production-bootstrap",
    "ai-platform.release-role": "opensandbox-server",
    "ai-platform.security-domain": "execution-controller",
}
Run = Callable[..., subprocess.CompletedProcess[str]]


class GuardError(RuntimeError):
    """A fixed-name container could not be safely classified or removed."""


def _run(
    command: Sequence[str],
    *,
    run: Run = subprocess.run,
) -> subprocess.CompletedProcess[str]:
    try:
        result = run(
            list(command),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise GuardError("Docker command unavailable") from exc
    if result.returncode != 0:
        raise GuardError("Docker command failed")
    return result


def _named_container_id(*, run: Run = subprocess.run) -> str | None:
    result = _run(
        [DOCKER, "container", "ls", "-a", "--format", "{{.ID}}|{{.Names}}"],
        run=run,
    )
    matches: list[str] = []
    for raw_line in result.stdout.splitlines():
        container_id, separator, name = raw_line.partition("|")
        if not separator or not container_id or not name:
            raise GuardError("Docker container inventory is invalid")
        if name == SERVER_CONTAINER:
            matches.append(container_id)
    if len(matches) > 1:
        raise GuardError("OpenSandbox server name is ambiguous")
    return matches[0] if matches else None


def _require_managed_identity(container_id: str, *, run: Run = subprocess.run) -> None:
    result = _run([DOCKER, "container", "inspect", container_id], run=run)
    try:
        payload: Any = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise GuardError("OpenSandbox server metadata is invalid") from exc
    container = (
        payload[0]
        if isinstance(payload, list)
        and len(payload) == 1
        and isinstance(payload[0], dict)
        else None
    )
    config = container.get("Config") if isinstance(container, dict) else None
    labels = config.get("Labels") if isinstance(config, dict) else None
    source_commit = (
        labels.get("ai-platform.source-commit") if isinstance(labels, dict) else None
    )
    host_config_sha256 = (
        labels.get("ai-platform.host-config-sha256")
        if isinstance(labels, dict)
        else None
    )
    if (
        not isinstance(container, dict)
        or container.get("Name") != f"/{SERVER_CONTAINER}"
        or not isinstance(config, dict)
        or IMAGE_RE.fullmatch(config.get("Image", "")) is None
        or not isinstance(labels, dict)
        or any(labels.get(key) != value for key, value in IDENTITY_LABELS.items())
        or not isinstance(source_commit, str)
        or COMMIT_RE.fullmatch(source_commit) is None
        or not isinstance(host_config_sha256, str)
        or re.fullmatch(r"[0-9a-f]{64}", host_config_sha256) is None
    ):
        raise GuardError("fixed-name OpenSandbox container is not repository-managed")


def remove_managed_container(*, run: Run = subprocess.run) -> None:
    container_id = _named_container_id(run=run)
    if container_id is None:
        return
    _require_managed_identity(container_id, run=run)
    _run([DOCKER, "container", "rm", "-f", container_id], run=run)
    if _named_container_id(run=run) is not None:
        raise GuardError("managed OpenSandbox container remains present")


def main() -> int:
    try:
        remove_managed_container()
    except GuardError:
        print(
            "OpenSandbox unit guard: managed-container removal failed", file=sys.stderr
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
