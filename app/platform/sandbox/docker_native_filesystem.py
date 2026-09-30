"""Docker filesystem layout for native commands and their private IPC listener."""

from __future__ import annotations

import os
from pathlib import Path
import stat
from typing import Any
from collections.abc import Iterable, Mapping


NATIVE_SOCKET_DIRECTORY = "/run/ai-platform-native"
NATIVE_SOCKET_PATH = NATIVE_SOCKET_DIRECTORY + "/native-tool.sock"
NATIVE_FILESYSTEM_LABEL = "ai-platform.native_tool.filesystem"
NATIVE_FILESYSTEM_VERSION = "2"


def native_container_filesystem(
    workspace_host: str,
    workspace_container: str,
    socket_parent: Path,
    *,
    read_only_paths: Mapping[str, str],
    private_roots: Iterable[str],
) -> dict[str, Any]:
    root = Path(workspace_host)
    target = workspace_container.rstrip("/")
    volumes = {
        str(root): {"bind": target, "mode": "rw"},
        str(socket_parent): {"bind": NATIVE_SOCKET_DIRECTORY, "mode": "rw"},
    }
    masks = set(private_roots)
    volumes.update(
        {
            source: {"bind": destination, "mode": "ro"}
            for source, destination in read_only_paths.items()
        }
    )
    return {
        "volumes": volumes,
        "user": "0:0",
        "privileged": False,
        "security_opt": ["no-new-privileges:true"],
        "cap_drop": ["ALL"],
        "cap_add": ["CHOWN", "SETUID", "SETGID"],
        "read_only": True,
        "tmpfs": {
            "/tmp": "rw,noexec,nosuid,nodev,uid=10001,gid=10001,mode=0700,size=64m",
            "/home/ai-platform": "rw,noexec,nosuid,nodev,uid=10001,gid=10001,mode=0700,size=32m",
            **{
                target
                + "/"
                + name: "ro,noexec,nosuid,nodev,uid=0,gid=0,mode=000,size=64k"
                for name in sorted(masks)
            },
        },
    }


def remove_root_owned_socket(client: Any, *, image: str, socket_parent: Path) -> None:
    """After the sidecar has stopped, unlink only its socket in the scoped bind."""
    client.containers.run(
        image=image,
        entrypoint=["python", "-m", __name__],
        command=[],
        volumes={str(socket_parent): {"bind": NATIVE_SOCKET_DIRECTORY, "mode": "rw"}},
        user="0:0",
        network_mode="none",
        read_only=True,
        cap_drop=["ALL"],
        security_opt=["no-new-privileges:true"],
        environment={"PYTHONDONTWRITEBYTECODE": "1"},
        remove=True,
    )


def main() -> int:
    directory = Path(NATIVE_SOCKET_DIRECTORY)
    descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        node = os.fstat(descriptor)
        if node.st_uid != 0 or node.st_gid != 10001:
            raise RuntimeError("native_tool_socket_parent_owner_invalid")
        for name in os.listdir(descriptor):
            node = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
            if name != "native-tool.sock" or not stat.S_ISSOCK(node.st_mode):
                raise RuntimeError("native_tool_socket_directory_occupied")
            os.unlink(name, dir_fd=descriptor)
    finally:
        os.close(descriptor)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
