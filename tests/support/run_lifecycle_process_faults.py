"""Process-level faults for the real-dependency Run lifecycle fixture."""

from __future__ import annotations

from typing import Any


SHORT_EXECUTION_MAX_SECONDS = 30.0


def release_executor_runner(container: Any) -> None:
    result = container.exec_run(
        [
            "python",
            "-c",
            "from pathlib import Path; "
            "p=next(Path('/workspace').rglob('.n01-entered')); "
            "p.with_name('.n01-release').touch()",
        ]
    )
    if result.exit_code != 0:
        raise AssertionError(f"executor release barrier failed: {result.output}")


def kill_executor_process(container: Any) -> dict[str, Any]:
    container_id = str(container.id)
    container.kill(signal="SIGKILL")
    wait_result = container.wait(timeout=30)
    container.reload()
    if container.status == "running":
        raise AssertionError("executor container remained running after SIGKILL")
    return {
        "container_id": container_id,
        "exit_code": int(wait_result.get("StatusCode", -1)),
        "container_status": str(container.status),
        "signal": "SIGKILL",
    }


def pause_executor_process(container: Any) -> dict[str, Any]:
    container_id = str(container.id)
    container.reload()
    if container.status != "running":
        raise AssertionError("executor container was not running before process pause")
    container.pause()
    container.reload()
    state = container.attrs.get("State", {})
    if state.get("Paused") is not True:
        raise AssertionError("executor container process was not paused")
    return {
        "container_id": container_id,
        "method": "docker_pause",
        "container_status": str(container.status),
        "container_paused": True,
    }


def resume_executor_process(container: Any) -> dict[str, Any]:
    container_id = str(container.id)
    container.reload()
    if container.attrs.get("State", {}).get("Paused") is not True:
        raise AssertionError("executor container was not paused before process resume")
    container.unpause()
    container.reload()
    if (
        container.status != "running"
        or container.attrs.get("State", {}).get("Paused") is not False
    ):
        raise AssertionError("executor container stopped during process resume")
    return {
        "container_id": container_id,
        "method": "docker_unpause",
        "container_status": str(container.status),
        "container_paused": False,
    }

