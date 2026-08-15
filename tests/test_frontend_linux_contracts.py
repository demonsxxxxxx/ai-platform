import os
from pathlib import Path
import subprocess
import sys

import pytest


pytestmark = pytest.mark.skipif(
    not sys.platform.startswith("linux"),
    reason="the required frontend-image contract lane runs on Linux",
)


FRONTEND_HEALTHCHECK_FILE_PATHS = (
    "/usr/share/nginx/html/index.html",
    "/usr/share/nginx/html/manifest.json",
    "/usr/share/nginx/html/icons/icon.svg",
    "/usr/share/nginx/html/icons/icon-192.png",
    "/usr/share/nginx/html/icons/icon-512.png",
)
FRONTEND_NGINX_BASE = (
    "FROM nginx:1.30.4-alpine@"
    "sha256:97d490c12ba55b4946b01546d1c3ed324e8d41ab1c9fcb2a616aa470620e5b46 AS runtime"
)


def _frontend_healthcheck_command() -> str:
    dockerfile = Path("frontend/web/Dockerfile").read_text(encoding="utf-8")
    runtime_dockerfile = dockerfile.split(FRONTEND_NGINX_BASE, 1)[1]
    healthcheck = next(
        line for line in runtime_dockerfile.splitlines() if line.startswith("HEALTHCHECK ")
    )
    return healthcheck.split(" CMD ", 1)[1]


def _write_frontend_healthcheck_files(root: Path) -> None:
    for path in FRONTEND_HEALTHCHECK_FILE_PATHS:
        asset_path = root / Path(path).relative_to("/usr/share/nginx/html")
        asset_path.parent.mkdir(parents=True, exist_ok=True)
        asset_path.write_bytes(b"asset")
        asset_path.chmod(0o644)


def _run_frontend_healthcheck_command(
    healthcheck_command: str, root: Path
) -> tuple[subprocess.CompletedProcess[str], Path]:
    test_root = root / ".healthcheck-test"
    http_probe_log = test_root / "http-probes.log"
    bin_root = test_root / "bin"
    bin_root.mkdir(parents=True)
    wget = bin_root / "wget"
    wget.write_text('#!/bin/sh\nprintf "wget\\n" >> "$HTTP_PROBE_LOG"\n', encoding="utf-8")
    wget.chmod(0o755)
    environment = os.environ | {
        "HTTP_PROBE_LOG": str(http_probe_log),
        "PATH": f"{bin_root}:{os.environ['PATH']}",
    }
    result = subprocess.run(
        [
            "/bin/sh",
            "-c",
            healthcheck_command.replace("/usr/share/nginx/html", str(root)),
        ],
        check=False,
        capture_output=True,
        env=environment,
        text=True,
    )
    return result, http_probe_log


def test_frontend_healthcheck_file_predicate_fails_closed_before_http_probes(
    tmp_path: Path,
) -> None:
    healthcheck_command = _frontend_healthcheck_command()
    file_predicate, http_probes = healthcheck_command.split(" && wget ", 1)
    assert "wget " not in file_predicate
    assert http_probes.startswith("-q -O /dev/null http://127.0.0.1:8080/healthz")

    healthy_root = tmp_path / "healthy"
    _write_frontend_healthcheck_files(healthy_root)
    healthy_result, healthy_http_probe_log = _run_frontend_healthcheck_command(
        healthcheck_command, healthy_root
    )
    assert healthy_result.returncode == 0
    assert healthy_http_probe_log.read_text(encoding="utf-8").splitlines() == ["wget"] * 5

    for path in FRONTEND_HEALTHCHECK_FILE_PATHS:
        relative_path = Path(path).relative_to("/usr/share/nginx/html")
        for failure_case in ("missing", "empty", "unreadable", "directory"):
            failure_root = tmp_path / f"{relative_path.stem}-{failure_case}"
            _write_frontend_healthcheck_files(failure_root)
            failure_path = failure_root / relative_path
            if failure_case == "missing":
                failure_path.unlink()
            elif failure_case == "empty":
                failure_path.write_bytes(b"")
            elif failure_case == "unreadable":
                failure_path.chmod(0o600)
            else:
                failure_path.unlink()
                failure_path.mkdir()

            failure_result, failure_http_probe_log = _run_frontend_healthcheck_command(
                healthcheck_command, failure_root
            )
            assert failure_result.returncode != 0
            assert not failure_http_probe_log.exists()
