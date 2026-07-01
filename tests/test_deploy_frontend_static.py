import json
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

from tools.deploy_frontend_static import (
    DeploymentError,
    deploy_static_frontend_release,
    verify_dist_provenance,
)


COMMIT = "5938c04ff11771c3ddfce2e07a798f4370134465"


def write_dist(root: Path, *, commit: str = COMMIT, dirty: bool = False) -> Path:
    dist = root / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<html></html>\n", encoding="utf-8")
    (dist / "assets" / "index.js").write_text("console.log('ok')\n", encoding="utf-8")
    (dist / "ai-platform-build-provenance.json").write_text(
        json.dumps(
            {
                "schema_version": "ai-platform.frontend-build-provenance.v1",
                "frontend_path": "frontend/web",
                "git": {"commit": commit, "dirty": dirty},
                "source_hashes": {
                    "package_json_sha256": "package",
                    "pnpm_lock_sha256": "lock",
                },
            }
        ),
        encoding="utf-8",
    )
    return dist


def pack_dist(package_path: Path, dist: Path) -> Path:
    with tarfile.open(package_path, "w:gz") as archive:
        archive.add(dist, arcname="dist")
    return package_path


def test_verify_dist_provenance_rejects_dirty_build(tmp_path):
    dist = write_dist(tmp_path, dirty=True)

    with pytest.raises(DeploymentError, match="dist_built_from_dirty_worktree"):
        verify_dist_provenance(dist, expected_commit=COMMIT)


def test_deploy_static_frontend_release_activates_commit_with_symlink_and_compat_dist(tmp_path):
    package = pack_dist(tmp_path / "release.tar.gz", write_dist(tmp_path / "package"))
    frontend_root = tmp_path / "frontend-runtime"
    old_dist = write_dist(frontend_root / "legacy", commit="06098e88649d6bee6a736aa1138e722b797429e1")
    old_active = frontend_root / "dist"
    old_active.parent.mkdir(parents=True, exist_ok=True)
    old_dist.replace(old_active)

    result = deploy_static_frontend_release(
        package_path=package,
        frontend_root=frontend_root,
        expected_commit=COMMIT,
        api_base="http://127.0.0.1:8020",
        restart=False,
    )

    release_dist = frontend_root / "releases" / COMMIT / "dist"
    assert result["status"] == "deployed"
    assert result["active_release"] == str(release_dist)
    assert (frontend_root / "current").resolve() == release_dist.resolve()
    assert (frontend_root / "dist").resolve() == release_dist.resolve()
    assert json.loads((frontend_root / "dist" / "ai-platform-build-provenance.json").read_text())[
        "git"
    ]["commit"] == COMMIT
    backups = list((frontend_root / "backups").glob("dist-backup-before-5938c04ff117-*"))
    assert len(backups) == 1
    assert (backups[0] / "ai-platform-build-provenance.json").exists()


def test_deploy_static_frontend_release_does_not_touch_active_dist_on_commit_mismatch(tmp_path):
    package = pack_dist(
        tmp_path / "release.tar.gz",
        write_dist(tmp_path / "package", commit="06098e88649d6bee6a736aa1138e722b797429e1"),
    )
    frontend_root = tmp_path / "frontend-runtime"
    active = write_dist(frontend_root, commit=COMMIT)
    before = (active / "ai-platform-build-provenance.json").read_text(encoding="utf-8")

    with pytest.raises(DeploymentError, match="dist_build_commit_mismatch"):
        deploy_static_frontend_release(
            package_path=package,
            frontend_root=frontend_root,
            expected_commit=COMMIT,
            api_base="http://127.0.0.1:8020",
            restart=False,
        )

    assert (frontend_root / "dist" / "ai-platform-build-provenance.json").read_text(
        encoding="utf-8"
    ) == before
    assert not list((frontend_root / "backups").glob("*"))


def test_deploy_static_frontend_release_rejects_unsafe_package_path(tmp_path):
    package = tmp_path / "unsafe.tar.gz"
    with tarfile.open(package, "w:gz") as archive:
        payload = tmp_path / "payload.txt"
        payload.write_text("bad\n", encoding="utf-8")
        archive.add(payload, arcname="../outside.txt")

    with pytest.raises(DeploymentError, match="release_package_unsafe_path"):
        deploy_static_frontend_release(
            package_path=package,
            frontend_root=tmp_path / "frontend-runtime",
            expected_commit=COMMIT,
            api_base="http://127.0.0.1:8020",
            restart=False,
        )


def test_deploy_static_frontend_release_rejects_incomplete_existing_release(tmp_path):
    package = pack_dist(tmp_path / "release.tar.gz", write_dist(tmp_path / "package"))
    frontend_root = tmp_path / "frontend-runtime"
    incomplete_release = frontend_root / "releases" / COMMIT
    incomplete_release.mkdir(parents=True)
    (incomplete_release / "README.txt").write_text("interrupted deploy\n", encoding="utf-8")

    with pytest.raises(DeploymentError, match="release_directory_incomplete"):
        deploy_static_frontend_release(
            package_path=package,
            frontend_root=frontend_root,
            expected_commit=COMMIT,
            api_base="http://127.0.0.1:8020",
            restart=False,
        )

    assert not (frontend_root / "dist").exists()
    assert (incomplete_release / "README.txt").exists()


def test_deploy_static_frontend_release_cli_outputs_json(tmp_path):
    package = pack_dist(tmp_path / "release.tar.gz", write_dist(tmp_path / "package"))
    frontend_root = tmp_path / "frontend-runtime"

    result = subprocess.run(
        [
            sys.executable,
            "tools/deploy_frontend_static.py",
            "--package-path",
            str(package),
            "--frontend-root",
            str(frontend_root),
            "--expected-commit",
            COMMIT,
            "--format",
            "json",
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    payload = json.loads(result.stdout)
    assert payload["status"] == "deployed"
    assert payload["expected_commit"] == COMMIT
    assert payload["build_provenance"]["git"]["commit"] == COMMIT
    assert (frontend_root / "dist").resolve() == (frontend_root / "releases" / COMMIT / "dist").resolve()
