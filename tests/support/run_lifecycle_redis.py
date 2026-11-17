"""Own a Redis instance, including the production reconciliation signal key."""

from __future__ import annotations

import subprocess
import uuid


class IsolatedRedis:
    """The CI service has already pulled this image; never pull during tests."""

    def __init__(self) -> None:
        self.name = f"ai-platform-lifecycle-redis-{uuid.uuid4().hex}"
        self.container_id: str | None = None

    def _docker(self, *arguments: str) -> str:
        return subprocess.check_output(
            ["docker", *arguments], text=True, stderr=subprocess.PIPE, timeout=30,
        ).strip()

    def start(self) -> str:
        # Name and label are selected before create, so cleanup can recover a
        # created container even when the CLI acknowledgement is interrupted.
        self.container_id = self._docker(
            "create", "--pull=never", "--name", self.name,
            "--label", f"ai-platform.lifecycle-test={self.name}",
            "--memory=128m", "--cpus=0.5", "--publish", "127.0.0.1::6379",
            "redis:7.4-alpine",
        )
        self._docker("start", self.container_id)
        address = self._docker("port", self.container_id, "6379/tcp")
        host, port = address.rsplit(":", 1)
        if host != "127.0.0.1" or not port.isdecimal():
            raise RuntimeError("isolated Redis loopback binding invalid")
        return f"redis://127.0.0.1:{port}/0"

    def close(self) -> None:
        # Select by our exact random label, never by a shared prefix or DB index.
        ids = self._docker(
            "ps", "--all", "--quiet", "--no-trunc", "--filter",
            f"label=ai-platform.lifecycle-test={self.name}",
        ).splitlines()
        if self.container_id is not None and self.container_id not in ids:
            raise RuntimeError("isolated Redis resource ownership changed")
        for container_id in ids:
            self._docker("rm", "--force", "--volumes", container_id)
        remaining = self._docker(
            "ps", "--all", "--quiet", "--filter",
            f"label=ai-platform.lifecycle-test={self.name}",
        )
        if remaining:
            raise RuntimeError("isolated Redis cleanup incomplete")
        self.container_id = None
