from __future__ import annotations

import os
from pathlib import Path
import re
import stat
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]
ENTRY = ROOT / "scripts/deploy-latest.sh"


def _fake_python(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    python = fake_bin / "python3"
    python.write_text(
        "#!/bin/sh\n"
        "printf 'sentinel=%s\\n' \"${DEPLOY_SENTINEL:-}\"\n"
        "for argument do printf 'arg=%s\\n' \"$argument\"; done\n",
        encoding="utf-8",
    )
    python.chmod(0o755)
    environment = os.environ.copy()
    environment["PATH"] = f"{fake_bin}{os.pathsep}{environment['PATH']}"
    environment["DEPLOY_SENTINEL"] = "preserved"
    return python, environment


def _run(
    tmp_path: Path,
    *arguments: str,
) -> subprocess.CompletedProcess[str]:
    _python, environment = _fake_python(tmp_path)
    return subprocess.run(
        [str(ENTRY), *arguments],
        cwd=ROOT,
        env=environment,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=10,
        check=False,
    )


@pytest.mark.parametrize(
    ("profile", "controller"),
    [
        ("internal-test", "tools/latest_main_quickstart.py"),
        ("production", "tools/production_bootstrap.py"),
    ],
)
def test_profile_selects_controller_and_preserves_arguments_and_environment(
    tmp_path: Path,
    profile: str,
    controller: str,
) -> None:
    env_path = "/managed path/production.env"
    result = _run(
        tmp_path,
        "--profile",
        profile,
        "--latest",
        "--env-file",
        env_path,
    )

    assert result.returncode == 0
    assert result.stderr == ""
    assert result.stdout.splitlines() == [
        "sentinel=preserved",
        "arg=-I",
        f"arg={ROOT / controller}",
        "arg=--latest",
        "arg=--env-file",
        f"arg={env_path}",
    ]


@pytest.mark.parametrize(
    "arguments",
    [(), ("--profile",), ("--profile", "unknown")],
)
def test_missing_or_unknown_profile_fails_before_controller_execution(
    tmp_path: Path,
    arguments: tuple[str, ...],
) -> None:
    result = _run(tmp_path, *arguments)

    assert result.returncode == 2
    assert result.stdout == ""
    assert "usage:" in result.stderr


def test_help_and_entry_permissions_are_operator_ready(tmp_path: Path) -> None:
    result = _run(tmp_path, "--help")

    assert result.returncode == 0
    assert "--profile <internal-test|production>" in result.stderr
    assert stat.S_IMODE(ENTRY.stat().st_mode) == 0o755


def test_existing_internal_test_alias_routes_through_the_canonical_entry(
    tmp_path: Path,
) -> None:
    _python, environment = _fake_python(tmp_path)
    alias = ROOT / "scripts/quickstart-s72.sh"
    result = subprocess.run(
        [str(alias), "--latest"],
        cwd=ROOT,
        env=environment,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=10,
        check=False,
    )

    assert result.returncode == 0
    assert f"arg={ROOT / 'tools/latest_main_quickstart.py'}" in result.stdout
    assert "arg=--latest" in result.stdout


def test_primary_quickstart_surfaces_are_role_based() -> None:
    surfaces = (
        ROOT / "README.md",
        ROOT / "scripts/deploy-latest.sh",
        ROOT / "docs/operations/production-bootstrap.md",
    )

    for surface in surfaces:
        assert re.search(r"\bs(?:72|75)\b", surface.read_text(encoding="utf-8")) is None

    assert (ROOT / "deploy/opensandbox/opensandbox-production.service").is_file()
    assert (ROOT / "deploy/opensandbox/server-production.env.example").is_file()
    assert (ROOT / "deploy/opensandbox/server-production.toml.example").is_file()
