import os
import pathlib
import shutil
import subprocess
import textwrap

import pytest


ROOT = pathlib.Path(__file__).resolve().parents[1]
HELPER = ROOT / "deploy" / "opensandbox" / "lib" / "s72-atomic-recovery-authority.sh"
INSTALLER = ROOT / "deploy" / "opensandbox" / "install-s72.sh"
ROLLBACK = ROOT / "deploy" / "opensandbox" / "rollback-s72.sh"


def _bash() -> str:
    git_bash = pathlib.Path("C:/Program Files/Git/bin/bash.exe")
    executable = str(git_bash) if git_bash.exists() else shutil.which("bash")
    if not executable:
        pytest.skip("Bash is required for the s72 recovery authority contract")
    return executable


def _run_bash(body: str, *paths: pathlib.Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [_bash(), "-c", textwrap.dedent(body), "s72-atomic-contract", *(path.as_posix() for path in paths)],
        text=True,
        capture_output=True,
        timeout=20,
        check=False,
    )


def test_shared_helper_is_the_single_active_primitive_authority() -> None:
    helper = HELPER.read_text(encoding="utf-8")

    for symbol in (
        "s72_atomic_is_commit",
        "s72_atomic_is_authority_evidence_id",
        "s72_atomic_require_root_tree",
        "s72_atomic_require_root_owned_regular",
        "s72_atomic_verify_manifest",
        "s72_atomic_require_marker_pair",
        "s72_atomic_preflight_snapshot",
        "s72_atomic_record_authority_state",
    ):
        assert f"{symbol}()" in helper

    for script_path in (INSTALLER, ROLLBACK):
        script = script_path.read_text(encoding="utf-8")
        assert '. "$S72_LIB_DIR/s72-atomic-recovery-authority.sh"' in script
        assert 'is_commit() {\n  s72_atomic_is_commit "$@"\n}' in script
        assert 'is_authority_evidence_id() {\n  s72_atomic_is_authority_evidence_id "$@"\n}' in script
        assert 'require_root_tree() {\n  s72_atomic_require_root_tree "$@"\n}' in script
        assert 'verify_manifest() {\n  s72_atomic_verify_manifest "$@"\n}' in script
        assert 'require_marker_pair() {\n  s72_atomic_require_marker_pair "$@"\n}' in script
        assert 'preflight_snapshot() {\n  s72_atomic_preflight_snapshot "$@"\n}' in script
        assert 'record_authority_state() {\n  s72_atomic_record_authority_state "$@"\n}' in script


@pytest.mark.parametrize(
    ("script", "entrypoint"),
    [(INSTALLER, "install_main"), (ROLLBACK, "rollback_main")],
)
def test_existing_source_harness_loads_the_deterministic_sibling_helper(
    script: pathlib.Path,
    entrypoint: str,
) -> None:
    result = _run_bash(
        rf'''
        set -eu
        SCRIPT=$1
        eval "$(sed '/^{entrypoint} "\$@"$/d' "$SCRIPT")"
        test "$S72_LIB_DIR" = "$(dirname "$SCRIPT")/lib"
        type s72_atomic_is_commit >/dev/null 2>&1
        is_commit 0123456789012345678901234567890123456789
        ! is_commit 012345678901234567890123456789012345678x
        ''',
        script,
    )
    assert result.returncode == 0, result.stderr or result.stdout


def test_helper_rejects_manifest_mismatch_and_closed_marker_state(tmp_path: pathlib.Path) -> None:
    result = _run_bash(
        r'''
        set -eu
        HELPER=$1; ROOT=$2
        . "$HELPER"

        mkdir -p "$ROOT/tree" "$ROOT/snapshot"
        printf 'sealed\n' > "$ROOT/tree/payload"
        (cd "$ROOT/tree" && sha256sum payload > MANIFEST.sha256)
        s72_atomic_verify_manifest "$ROOT/tree"
        printf 'tampered\n' >> "$ROOT/tree/payload"
        ! s72_atomic_verify_manifest "$ROOT/tree"

        require_root_tree() { :; }
        verify_manifest() { :; }
        require_marker_pair() { s72_atomic_require_marker_pair "$@"; }
        is_commit() { s72_atomic_is_commit "$@"; }
        is_authority_evidence_id() { s72_atomic_is_authority_evidence_id "$@"; }
        validate_release() { :; }
        for unit in opensandbox-gateway.service opensandbox-gateway-helper.service; do
          : > "$ROOT/snapshot/$unit.absent"
          : > "$ROOT/snapshot/$unit.inactive"
          : > "$ROOT/snapshot/$unit.disabled"
        done
        : > "$ROOT/snapshot/config.absent"
        : > "$ROOT/snapshot/workspaces.acl"
        : > "$ROOT/snapshot/authority-sha.absent"
        : > "$ROOT/snapshot/authority-evidence.absent"
        : > "$ROOT/snapshot/current.absent"
        s72_atomic_preflight_snapshot "$ROOT/snapshot"
        : > "$ROOT/snapshot/config.present"
        ! s72_atomic_preflight_snapshot "$ROOT/snapshot"
        rm "$ROOT/snapshot/config.present"
        : > "$ROOT/snapshot/opensandbox-gateway.service.present"
        rm "$ROOT/snapshot/opensandbox-gateway.service.absent"
        ! s72_atomic_preflight_snapshot "$ROOT/snapshot"
        ''',
        HELPER,
        tmp_path,
    )
    assert result.returncode == 0, result.stderr or result.stdout


def test_helper_rejects_non_root_identity_and_records_authority_atomically(
    tmp_path: pathlib.Path,
) -> None:
    commit = "1" * 40
    result = _run_bash(
        rf'''
        set -eu
        HELPER=$1; ROOT=$2
        . "$HELPER"
        mkdir -p "$ROOT/tree" "$ROOT/state"
        stat() {{ printf '1000\n'; }}
        ! s72_atomic_require_root_tree "$ROOT/tree"
        unset -f stat

        is_commit() {{ s72_atomic_is_commit "$@"; }}
        is_authority_evidence_id() {{ s72_atomic_is_authority_evidence_id "$@"; }}
        DEPLOY_STATE=$ROOT/state
        AUTHORITY_SHA_STATE=$DEPLOY_STATE/current-authority-sha
        AUTHORITY_EVIDENCE_STATE=$DEPLOY_STATE/current-authority-evidence
        chown() {{ :; }}
        chmod() {{ :; }}
        s72_atomic_record_authority_state {commit} ls-remote-sealed
        grep -qx {commit} "$AUTHORITY_SHA_STATE"
        grep -qx ls-remote-sealed "$AUTHORITY_EVIDENCE_STATE"
        ! s72_atomic_record_authority_state {commit} 'invalid evidence'
        ''',
        HELPER,
        tmp_path,
    )
    assert result.returncode == 0, result.stderr or result.stdout


@pytest.mark.skipif(os.name != "posix", reason="POSIX special-node contract runs on required Ubuntu CI")
def test_helper_rejects_symlink_and_fifo_nodes(tmp_path: pathlib.Path) -> None:
    result = _run_bash(
        r'''
        set -eu
        HELPER=$1; ROOT=$2
        . "$HELPER"
        mkdir -p "$ROOT/tree"
        printf 'target\n' > "$ROOT/target"
        ln -s "$ROOT/target" "$ROOT/tree/link"
        ! s72_atomic_require_root_tree "$ROOT/tree"
        mkfifo "$ROOT/fifo"
        ! s72_atomic_require_root_owned_regular "$ROOT/fifo" 600
        ''',
        HELPER,
        tmp_path,
    )
    assert result.returncode == 0, result.stderr or result.stdout
