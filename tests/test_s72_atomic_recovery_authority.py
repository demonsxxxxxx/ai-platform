import hashlib
import os
import pathlib
import shlex
import shutil
import subprocess
import textwrap
import threading
import time

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


def _run_bash_with_argv0(
    body: str,
    argv0: str,
    *arguments: str | pathlib.Path,
    cwd: pathlib.Path | None = None,
    environment: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [_bash(), "-c", textwrap.dedent(body), argv0, *(str(value) for value in arguments)],
        cwd=cwd,
        env=environment,
        text=True,
        capture_output=True,
        timeout=20,
        check=False,
    )


def _write_hostile_helper(
    root: pathlib.Path,
    marker: pathlib.Path,
    *,
    exit_code: int,
    signal: str,
) -> None:
    helper = root / "lib" / HELPER.name
    helper.parent.mkdir(parents=True)
    helper.write_text(
        "#!/bin/sh\n"
        f"printf '%s\\n' {shlex.quote(signal)}\n"
        f"printf '%s\\n' {shlex.quote(signal)} > {shlex.quote(marker.as_posix())}\n"
        f"exit {exit_code}\n",
        encoding="utf-8",
    )


def _track_staged_entrypoint(repo: pathlib.Path, entrypoint: pathlib.Path) -> None:
    git = shutil.which("git")
    if not git:
        pytest.skip("Git is required for the explicit source-eval checkout contract")
    subprocess.run(
        [git, "init", "--quiet", str(repo)],
        text=True,
        capture_output=True,
        timeout=20,
        check=True,
    )
    subprocess.run(
        [git, "-C", str(repo), "add", entrypoint.relative_to(repo).as_posix()],
        text=True,
        capture_output=True,
        timeout=20,
        check=True,
    )


@pytest.mark.parametrize(
    ("script", "entrypoint", "script_name"),
    [
        (INSTALLER, "install_main", "install-s72.sh"),
        (ROLLBACK, "rollback_main", "rollback-s72.sh"),
    ],
)
def test_loader_rejects_spoofed_entrypoint_name_and_cwd_helper_before_source(
    tmp_path: pathlib.Path,
    script: pathlib.Path,
    entrypoint: str,
    script_name: str,
) -> None:
    marker = tmp_path / "cwd-helper-executed"
    signal = "HOSTILE_CWD_HELPER_EXECUTED"
    _write_hostile_helper(tmp_path, marker, exit_code=73, signal=signal)

    result = _run_bash_with_argv0(
        rf'''
        set -eu
        REAL_SCRIPT=$(cygpath -u "$1" 2>/dev/null || printf '%s\n' "$1")
        eval "$(sed '/^{entrypoint} "\$@"$/d' "$REAL_SCRIPT")"
        ''',
        script_name,
        script,
        cwd=tmp_path,
    )

    assert result.returncode != 73
    assert not marker.exists()
    assert signal not in result.stdout
    assert signal not in result.stderr


@pytest.mark.parametrize(
    ("script", "entrypoint", "script_name"),
    [
        (INSTALLER, "install_main", "install-s72.sh"),
        (ROLLBACK, "rollback_main", "rollback-s72.sh"),
    ],
)
def test_loader_rejects_relative_script_and_attacker_sibling_before_source(
    tmp_path: pathlib.Path,
    script: pathlib.Path,
    entrypoint: str,
    script_name: str,
) -> None:
    attacker = tmp_path / "attacker"
    foreign_script = attacker / script_name
    foreign_script.parent.mkdir()
    foreign_script.write_text("foreign entrypoint\n", encoding="utf-8")
    marker = tmp_path / "script-helper-executed"
    signal = "HOSTILE_SCRIPT_HELPER_EXECUTED"
    _write_hostile_helper(attacker, marker, exit_code=74, signal=signal)

    result = _run_bash_with_argv0(
        rf'''
        set -eu
        REAL_SCRIPT=$(cygpath -u "$1" 2>/dev/null || printf '%s\n' "$1")
        SCRIPT=attacker/{script_name}
        eval "$(sed '/^{entrypoint} "\$@"$/d' "$REAL_SCRIPT")"
        ''',
        "s72-atomic-contract",
        script,
        cwd=tmp_path,
    )

    assert result.returncode != 74
    assert not marker.exists()
    assert signal not in result.stdout
    assert signal not in result.stderr


@pytest.mark.parametrize(
    ("script", "entrypoint"),
    [(INSTALLER, "install_main"), (ROLLBACK, "rollback_main")],
)
def test_loader_rejects_exported_script_authority(
    script: pathlib.Path,
    entrypoint: str,
) -> None:
    environment = os.environ.copy()
    environment["SCRIPT"] = str(script)

    result = _run_bash_with_argv0(
        rf'''
        set -eu
        REAL_SCRIPT=$(cygpath -u "$1" 2>/dev/null || printf '%s\n' "$1")
        eval "$(sed '/^{entrypoint} "\$@"$/d' "$REAL_SCRIPT")"
        ''',
        "s72-atomic-contract",
        script,
        environment=environment,
    )

    assert result.returncode == 126
    assert "loader authority rejected" in result.stderr


@pytest.mark.parametrize(
    ("script", "entrypoint", "script_name"),
    [
        (INSTALLER, "install_main", "install-s72.sh"),
        (ROLLBACK, "rollback_main", "rollback-s72.sh"),
    ],
)
def test_loader_rejects_foreign_absolute_script_even_with_exact_sibling_helper(
    tmp_path: pathlib.Path,
    script: pathlib.Path,
    entrypoint: str,
    script_name: str,
) -> None:
    foreign = tmp_path / "foreign" / "deploy" / "opensandbox"
    foreign_script = foreign / script_name
    helper_dir = foreign / "lib"
    helper_dir.mkdir(parents=True)
    shutil.copy2(script, foreign_script)
    shutil.copy2(HELPER, helper_dir / HELPER.name)
    signal = "FOREIGN_ABSOLUTE_SCRIPT_ACCEPTED"

    result = _run_bash_with_argv0(
        rf'''
        set -eu
        REAL_SCRIPT=$(cygpath -u "$1" 2>/dev/null || printf '%s\n' "$1")
        SCRIPT=$(cygpath -u "$2" 2>/dev/null || printf '%s\n' "$2")
        eval "$(sed '/^{entrypoint} "\$@"$/d' "$REAL_SCRIPT")"
        printf '%s\n' {signal}
        ''',
        "s72-atomic-contract",
        script,
        foreign_script,
    )

    assert result.returncode == 126
    assert signal not in result.stdout
    assert signal not in result.stderr


@pytest.mark.parametrize(
    ("script", "entrypoint", "script_name"),
    [
        (INSTALLER, "install_main", "install-s72.sh"),
        (ROLLBACK, "rollback_main", "rollback-s72.sh"),
    ],
)
def test_loader_rejects_wrong_helper_digest_before_source(
    tmp_path: pathlib.Path,
    script: pathlib.Path,
    entrypoint: str,
    script_name: str,
) -> None:
    repo = tmp_path / "repo"
    staged = repo / "deploy" / "opensandbox"
    staged.mkdir(parents=True)
    staged_script = staged / script_name
    shutil.copy2(script, staged_script)
    _track_staged_entrypoint(repo, staged_script)
    marker = tmp_path / "wrong-helper-executed"
    signal = "HOSTILE_WRONG_HELPER_EXECUTED"
    _write_hostile_helper(staged, marker, exit_code=76, signal=signal)

    result = _run_bash_with_argv0(
        rf'''
        set -eu
        SCRIPT=$(cygpath -u "$1" 2>/dev/null || printf '%s\n' "$1")
        eval "$(sed '/^{entrypoint} "\$@"$/d' "$SCRIPT")"
        ''',
        "s72-atomic-contract",
        staged_script,
    )

    assert result.returncode == 126
    assert not marker.exists()
    assert signal not in result.stdout
    assert signal not in result.stderr


@pytest.mark.parametrize(
    ("script", "entrypoint", "script_name"),
    [
        (INSTALLER, "install_main", "install-s72.sh"),
        (ROLLBACK, "rollback_main", "rollback-s72.sh"),
    ],
)
@pytest.mark.parametrize("linked_node", ["entrypoint", "helper"])
def test_loader_rejects_symlinked_entrypoint_or_helper_before_source(
    tmp_path: pathlib.Path,
    script: pathlib.Path,
    entrypoint: str,
    script_name: str,
    linked_node: str,
) -> None:
    repo = tmp_path / "repo"
    staged = repo / "deploy" / "opensandbox"
    staged.mkdir(parents=True)
    staged_script = staged / script_name
    helper_dir = staged / "lib"
    helper_dir.mkdir()
    try:
        if linked_node == "entrypoint":
            staged_script.symlink_to(script)
            shutil.copy2(HELPER, helper_dir / HELPER.name)
        else:
            shutil.copy2(script, staged_script)
            (helper_dir / HELPER.name).symlink_to(HELPER)
            _track_staged_entrypoint(repo, staged_script)
    except OSError:
        pytest.skip("native symlink creation is required for the loader authority contract")

    result = _run_bash_with_argv0(
        rf'''
        set -eu
        SCRIPT=$(cygpath -u "$1" 2>/dev/null || printf '%s\n' "$1")
        eval "$(sed '/^{entrypoint} "\$@"$/d' "$SCRIPT")"
        ''',
        "s72-atomic-contract",
        staged_script,
    )

    assert result.returncode == 126
    assert "loader authority rejected" in result.stderr


@pytest.mark.parametrize(
    ("script", "entrypoint", "script_name"),
    [
        (INSTALLER, "install_main", "install-s72.sh"),
        (ROLLBACK, "rollback_main", "rollback-s72.sh"),
    ],
)
def test_loader_rejects_missing_fixed_sibling_helper(
    tmp_path: pathlib.Path,
    script: pathlib.Path,
    entrypoint: str,
    script_name: str,
) -> None:
    repo = tmp_path / "repo"
    staged_script = repo / "deploy" / "opensandbox" / script_name
    staged_script.parent.mkdir(parents=True)
    shutil.copy2(script, staged_script)
    _track_staged_entrypoint(repo, staged_script)

    result = _run_bash_with_argv0(
        rf'''
        set -eu
        SCRIPT=$(cygpath -u "$1" 2>/dev/null || printf '%s\n' "$1")
        eval "$(sed '/^{entrypoint} "\$@"$/d' "$SCRIPT")"
        ''',
        "s72-atomic-contract",
        staged_script,
    )

    assert result.returncode == 126
    assert "loader authority rejected" in result.stderr


@pytest.mark.skipif(
    os.name != "posix" or not hasattr(os, "geteuid") or os.geteuid() == 0,
    reason="non-root writable-ancestor hostile runs on required Ubuntu CI",
)
@pytest.mark.parametrize(
    ("script", "entrypoint", "script_name"),
    [
        (INSTALLER, "install_main", "install-s72.sh"),
        (ROLLBACK, "rollback_main", "rollback-s72.sh"),
    ],
)
def test_production_loader_rejects_foreign_writable_ancestor_before_source(
    tmp_path: pathlib.Path,
    script: pathlib.Path,
    entrypoint: str,
    script_name: str,
) -> None:
    staged = tmp_path / "writable" / "deploy" / "opensandbox"
    helper_dir = staged / "lib"
    helper_dir.mkdir(parents=True)
    staged_script = staged / script_name
    script_text = script.read_text(encoding="utf-8")
    expected_call = f'{entrypoint} "$@"'
    assert script_text.count(expected_call) == 1
    staged_script.write_text(
        script_text.replace(expected_call, "printf '%s\\n' FOREIGN_ANCESTOR_ACCEPTED"),
        encoding="utf-8",
    )
    shutil.copy2(HELPER, helper_dir / HELPER.name)
    staged_script.chmod(0o755)

    result = _run_bash_with_argv0(
        'exec "$1"',
        "loader-launcher",
        staged_script,
    )

    assert result.returncode == 126
    assert "FOREIGN_ANCESTOR_ACCEPTED" not in result.stdout
    assert "loader authority rejected" in result.stderr


@pytest.mark.parametrize(
    ("script", "entrypoint", "script_name"),
    [
        (INSTALLER, "install_main", "install-s72.sh"),
        (ROLLBACK, "rollback_main", "rollback-s72.sh"),
    ],
)
def test_loader_never_evaluates_a_helper_replaced_after_validation(
    tmp_path: pathlib.Path,
    script: pathlib.Path,
    entrypoint: str,
    script_name: str,
) -> None:
    repo = tmp_path / "repo"
    staged = repo / "deploy" / "opensandbox"
    helper_dir = staged / "lib"
    helper_dir.mkdir(parents=True)
    staged_script = staged / script_name
    staged_helper = helper_dir / HELPER.name
    replacement = helper_dir / "replacement"
    ready = tmp_path / "loader-ready"
    proceed = tmp_path / "loader-proceed"
    signal = "BENIGN_REPLACEMENT_HELPER_EVALUATED"
    function_names = (
        "s72_atomic_is_commit",
        "s72_atomic_is_authority_evidence_id",
        "s72_atomic_require_root_tree",
        "s72_atomic_require_root_owned_regular",
        "s72_atomic_require_root_owned_directory",
        "s72_atomic_verify_manifest",
        "s72_atomic_require_marker_pair",
        "s72_atomic_preflight_snapshot",
        "s72_atomic_record_authority_state",
    )
    replacement.write_text(
        f"printf '%s\\n' {signal}\n"
        "S72_ATOMIC_RECOVERY_AUTHORITY_SCHEMA=s72-atomic-recovery-authority-v1\n"
        + "\n".join(f"{name}() {{ :; }}" for name in function_names)
        + "\n",
        encoding="utf-8",
    )
    shutil.copy2(HELPER, staged_helper)
    script_text = script.read_text(encoding="utf-8")
    source_anchor = '. "$S72_LIB_DIR/s72-atomic-recovery-authority.sh"'
    bound_anchor = 'eval "$s72_loader_helper_content"'
    anchors = [anchor for anchor in (source_anchor, bound_anchor) if script_text.count(anchor) == 1]
    assert len(anchors) == 1
    hook = (
        f"printf '%s\\n' ready > {shlex.quote(ready.as_posix())}\n"
        f"while test ! -e {shlex.quote(proceed.as_posix())}; do /usr/bin/sleep 0.01; done\n"
    )
    staged_script.write_text(script_text.replace(anchors[0], hook + anchors[0]), encoding="utf-8")
    _track_staged_entrypoint(repo, staged_script)

    replacement_errors: list[BaseException] = []

    def replace_after_validation() -> None:
        try:
            for _ in range(500):
                if ready.exists():
                    break
                time.sleep(0.01)
            else:
                raise AssertionError("loader did not reach the pre-evaluation synchronization point")
            os.replace(replacement, staged_helper)
            proceed.write_text("continue\n", encoding="utf-8")
        except BaseException as error:
            replacement_errors.append(error)

    replacement_thread = threading.Thread(target=replace_after_validation)
    replacement_thread.start()
    result = _run_bash_with_argv0(
        rf'''
        set -eu
        SCRIPT=$(cygpath -u "$1" 2>/dev/null || printf '%s\n' "$1")
        eval "$(sed '/^{entrypoint} "\$@"$/d' "$SCRIPT")"
        ''',
        "s72-atomic-contract",
        staged_script,
    )
    replacement_thread.join(timeout=5)

    assert not replacement_thread.is_alive()
    assert not replacement_errors
    assert result.returncode == 126
    assert "loader authority rejected" in result.stderr
    assert signal not in result.stdout
    assert signal not in result.stderr


def test_loader_seals_the_exact_shared_helper_digest_and_schema() -> None:
    helper_digest = hashlib.sha256(HELPER.read_bytes()).hexdigest()
    helper = HELPER.read_text(encoding="utf-8")
    assert "S72_ATOMIC_RECOVERY_AUTHORITY_SCHEMA=s72-atomic-recovery-authority-v1" in helper
    for script_path in (INSTALLER, ROLLBACK):
        script = script_path.read_text(encoding="utf-8")
        assert f"S72_ATOMIC_RECOVERY_HELPER_SHA256={helper_digest}" in script
        assert script.count("s72_loader_entry_identity") >= 2
        assert script.count("s72_loader_helper_identity") >= 2


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
        assert '. "$S72_LIB_DIR/s72-atomic-recovery-authority.sh"' not in script
        assert 'exec 8<"$s72_loader_capture_path"' in script
        assert "/usr/bin/stat -Lc '%d:%i:%f:%u:%g:%a:%s:%Y:%Z' -- /dev/fd/8" in script
        assert "/usr/bin/cat <&8" in script
        assert 'eval "$s72_loader_helper_content"' in script
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
        SCRIPT=$(cygpath -u "$1" 2>/dev/null || printf '%s\n' "$1")
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
