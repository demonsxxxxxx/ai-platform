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
        timeout=60,
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


def _run_privileged_bash(
    body: str,
    *paths: pathlib.Path,
) -> subprocess.CompletedProcess[str]:
    if os.name != "posix":
        pytest.skip("Linux privileged filesystem contract runs on required Ubuntu CI")
    sudo = shutil.which("sudo")
    if not sudo:
        pytest.fail("required Ubuntu contract needs sudo")
    return subprocess.run(
        [
            sudo,
            "-n",
            _bash(),
            "-c",
            textwrap.dedent(body),
            "s72-atomic-root-contract",
            *(path.as_posix() for path in paths),
            str(os.getuid()),
            str(os.getgid()),
        ],
        text=True,
        capture_output=True,
        timeout=60,
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
        s72_loader_mode=test-source-eval
        chown() { :; }

        mkdir -p "$ROOT/tree" "$ROOT/snapshot"
        printf 'sealed\n' > "$ROOT/tree/payload"
        s72_atomic_write_manifest "$ROOT/tree"
        s72_atomic_verify_manifest "$ROOT/tree"
        printf 'tampered\n' >> "$ROOT/tree/payload"
        ! s72_atomic_verify_manifest "$ROOT/tree"

        require_root_tree() { :; }
        require_root_owned_regular() { test -f "$1" && test ! -L "$1"; }
        s72_atomic_require_root_owned_regular() { test -f "$1" && test ! -L "$1"; }
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
        printf '%s\n' schema=s72-transaction-owner-v1 transaction=11111111111111111111111111111111 \
          "root=$(stat -c %d:%i "$ROOT/snapshot")" > "$ROOT/snapshot/transaction-owner"
        printf '%s\n' 1111111111111111111111111111111111111111 > "$ROOT/snapshot/captured-authority-sha"
        printf '%s\n' sealed-source-evidence > "$ROOT/snapshot/captured-authority-evidence"
        printf '%s\n' \
          schema=s72-lifecycle-authority-v1 \
          service=opensandbox.service \
          active=active \
          fragment=/etc/systemd/system/opensandbox.service \
          listener=127.0.0.1:8080 \
          listener-count=1 > "$ROOT/snapshot/lifecycle.authority"
        : > "$ROOT/snapshot/config.absent"
        printf '%s\n' 62001 > "$ROOT/snapshot/gateway-service-uid"
        : > "$ROOT/snapshot/gateway-group.absent"
        : > "$ROOT/snapshot/gateway-user.absent"
        : > "$ROOT/snapshot/runtime-state.absent"
        : > "$ROOT/snapshot/workspaces.acl"
        : > "$ROOT/snapshot/authority-sha.absent"
        : > "$ROOT/snapshot/authority-evidence.absent"
        : > "$ROOT/snapshot/current.absent"
        : > "$ROOT/snapshot/rollback-pointer.absent"
        chmod 0600 "$ROOT/snapshot"/*.absent "$ROOT/snapshot"/*.inactive \
          "$ROOT/snapshot"/*.disabled "$ROOT/snapshot/workspaces.acl"
        chmod 0400 "$ROOT/snapshot/transaction-owner" "$ROOT/snapshot/captured-authority-sha" \
          "$ROOT/snapshot/captured-authority-evidence" "$ROOT/snapshot/lifecycle.authority" \
          "$ROOT/snapshot/gateway-service-uid"
        s72_atomic_write_manifest "$ROOT/snapshot"
        s72_atomic_preflight_snapshot "$ROOT/snapshot"
        : > "$ROOT/snapshot/config.present"
        ! s72_atomic_preflight_snapshot "$ROOT/snapshot"
        rm "$ROOT/snapshot/config.present"
        : > "$ROOT/snapshot/opensandbox-gateway.service.present"
        rm "$ROOT/snapshot/opensandbox-gateway.service.absent"
        ! s72_atomic_preflight_snapshot "$ROOT/snapshot"
        rm "$ROOT/snapshot/opensandbox-gateway.service.present"
        : > "$ROOT/snapshot/opensandbox-gateway.service.absent"

        mkdir "$ROOT/snapshot/etc-opensandbox-gateway"
        ! s72_atomic_preflight_snapshot "$ROOT/snapshot"
        rmdir "$ROOT/snapshot/etc-opensandbox-gateway"
        printf 'releases/1111111111111111111111111111111111111111\n' > "$ROOT/snapshot/current"
        ! s72_atomic_preflight_snapshot "$ROOT/snapshot"
        rm "$ROOT/snapshot/current"
        printf '.rollback.22222222222222222222222222222222\n' > "$ROOT/snapshot/rollback-pointer"
        ! s72_atomic_preflight_snapshot "$ROOT/snapshot"
        rm "$ROOT/snapshot/rollback-pointer"
        printf '1111111111111111111111111111111111111111\n' > "$ROOT/snapshot/authority-sha"
        ! s72_atomic_preflight_snapshot "$ROOT/snapshot"
        rm "$ROOT/snapshot/authority-sha"
        printf 'unknown\n' > "$ROOT/snapshot/unknown-payload"
        ! s72_atomic_preflight_snapshot "$ROOT/snapshot"
        rm "$ROOT/snapshot/unknown-payload"
        printf 'nonempty\n' > "$ROOT/snapshot/config.absent"
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
        s72_loader_mode=test-source-eval
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


def test_atomic_recovery_entrypoints_expose_one_closed_recovery_engine() -> None:
    helper = HELPER.read_text(encoding="utf-8")
    installer = INSTALLER.read_text(encoding="utf-8")
    rollback = ROLLBACK.read_text(encoding="utf-8")

    for symbol in (
        "s72_atomic_publish_transaction_record",
        "s72_atomic_load_active_transaction",
        "s72_atomic_publish_snapshot",
        "s72_atomic_verify_snapshot_seal",
        "s72_atomic_restore_snapshot",
        "s72_atomic_require_exact_lifecycle",
    ):
        assert f"{symbol}()" in helper
        assert symbol in installer
        assert symbol in rollback

    assert "--recover" in installer
    assert "--recover" in rollback
    assert "JOURNAL.sha256" not in helper
    assert "rm -rf \"$CONFIG_DIR\"" not in installer
    assert "rm -rf \"$CONFIG_DIR\"" not in rollback
    assert "ss -ltn | grep" not in installer
    assert "ss -ltn | grep" not in rollback


def test_transaction_records_are_immutable_self_authenticating_and_chained() -> None:
    helper = HELPER.read_text(encoding="utf-8")

    assert "schema=s72-atomic-transaction-v1" in helper
    assert "previous-seal=" in helper
    assert "record-seal=" in helper
    assert "O_TMPFILE" in helper
    assert "AT_EMPTY_PATH" in helper
    assert "os.fsync" in helper
    assert "transaction-record.*.tmp" not in helper


def test_root_owned_node_checks_do_not_clobber_caller_state(tmp_path: pathlib.Path) -> None:
    result = _run_bash(
        r'''
        set -eu
        HELPER=$1; ROOT=$2
        . "$HELPER"
        mkdir "$ROOT/directory"
        printf '%s\n' payload > "$ROOT/regular"
        stat() {
          case "$1:$2" in
            -c:%u) printf '%s\n' 0 ;;
            -c:%G) printf '%s\n' root ;;
            -c:%a)
              test -d "$3" && printf '%s\n' 700 || printf '%s\n' 600
              ;;
            *) command stat "$@" ;;
          esac
        }
        mode=bootstrap
        s72_atomic_require_root_owned_directory "$ROOT/directory" 700
        test "$mode" = bootstrap
        s72_atomic_require_root_owned_regular "$ROOT/regular" 600
        test "$mode" = bootstrap
        ''',
        HELPER,
        tmp_path,
    )
    assert result.returncode == 0, result.stderr or result.stdout


@pytest.mark.parametrize(
    ("ss_output", "accepted"),
    [
        ("LISTEN 0 4096 127.0.0.1:8080 0.0.0.0:*", True),
        ("LISTEN 0 4096 127.0.0.1:80800 0.0.0.0:*", False),
        ("LISTEN 0 4096 127.0.0.1:18080 0.0.0.0:*", False),
        ("LISTEN 0 4096 0.0.0.0:8080 0.0.0.0:*", False),
        ("LISTEN 0 4096 [::1]:8080 [::]:*", False),
        (
            "LISTEN 0 4096 127.0.0.1:8080 0.0.0.0:*\n"
            "LISTEN 0 4096 127.0.0.1:8080 0.0.0.0:*",
            False,
        ),
        ("", False),
    ],
)
def test_exact_lifecycle_listener_parser_rejects_aliases_and_duplicates(
    tmp_path: pathlib.Path,
    ss_output: str,
    accepted: bool,
) -> None:
    output = tmp_path / "ss-output"
    output.write_text(ss_output + ("\n" if ss_output else ""), encoding="utf-8")
    result = _run_bash(
        r'''
        set -eu
        HELPER=$1
        OUTPUT=$2
        . "$HELPER"
        systemctl() {
          test "$1" = show
          case "$2:$3:$4:$5" in
            opensandbox.service:-p:ActiveState:--value) printf '%s\n' active ;;
            opensandbox.service:-p:FragmentPath:--value) printf '%s\n' /etc/systemd/system/opensandbox.service ;;
            *) return 1 ;;
          esac
        }
        ss() {
          test "$1:$2:$3" = '-H:-ltn:sport = :8080'
          cat "$OUTPUT"
        }
        s72_atomic_require_exact_lifecycle
        ''',
        HELPER,
        output,
    )

    assert (result.returncode == 0) is accepted, result.stderr


def test_snapshot_contract_is_closed_over_markers_payloads_and_node_types() -> None:
    helper = HELPER.read_text(encoding="utf-8")

    assert "s72_atomic_require_snapshot_inventory" in helper
    assert "lifecycle.authority" in helper
    assert "captured-authority-sha" in helper
    assert "captured-authority-evidence" in helper
    assert "MANIFEST.identity" in helper
    assert "SNAPSHOT.seal" in helper
    assert "! -type f ! -type d" in helper
    assert "config.absent" in helper
    assert "test ! -e \"$snapshot/etc-opensandbox-gateway\"" in helper
    assert "test ! -e \"$snapshot/$unit\"" in helper


def test_snapshot_publish_uses_same_parent_and_seals_before_atomic_rename() -> None:
    helper = HELPER.read_text(encoding="utf-8")

    assert "s72_atomic_create_snapshot_stage()" in helper
    publish = helper[helper.index("s72_atomic_publish_snapshot()") :]
    assert 'stage_parent="$snapshots_parent"' in helper
    assert 'test "$stage_device" = "$parent_device"' in helper
    assert "mv -T -n" in helper
    assert "s72_atomic_fsync_path \"$snapshots_parent\"" in helper
    assert publish.index('s72_atomic_write_snapshot_seal "$stage"') < publish.index(
        'mv -T -n "$stage" "$published"'
    )
    assert publish.index('s72_atomic_fsync_tree "$stage"') < publish.index(
        'mv -T -n "$stage" "$published"'
    )
    assert 's72_atomic_write_snapshot_seal "$published"' not in publish


def test_transaction_records_reject_torn_unknown_and_invalid_phase_state(
    tmp_path: pathlib.Path,
) -> None:
    result = _run_bash(
        r'''
        set -eu
        HELPER=$1; ROOT=$2
        . "$HELPER"
        records=$ROOT/records
        mkdir -p "$records"
        chmod 0700 "$records"
        s72_loader_mode=test-source-eval
        chown() { :; }
        s72_atomic_require_root_owned_directory() { test -d "$1" && test ! -L "$1"; }
        stat() {
          if test "$1:$2" = '-c:%u:%g:%a'; then
            printf '%s\n' 0:0:400
          else
            command stat "$@"
          fi
        }
        tx=11111111111111111111111111111111
        recovery=.rollback.$tx
        apply=.rollback.22222222222222222222222222222222
        commit=1111111111111111111111111111111111111111
        s72_atomic_publish_transaction_record "$records" "$tx" 000000 install reserved \
          "$recovery" "$apply" none "$commit" sealed-evidence none none >/dev/null
        s72_atomic_bind_transaction_stage "$records" "$tx" '1:2:directory:0:0:700:0:1:1'
        s72_atomic_advance_transaction "$records" "$tx" snapshot-published
        s72_atomic_load_transaction "$records" "$tx"
        test "$S72_TX_PHASE" = snapshot-published
        test "$S72_TX_PREVIOUS_PHASE" = reserved
        ! s72_atomic_advance_transaction "$records" "$tx" committed

        printf '%s\n' hostile > "$records/unknown"
        ! s72_atomic_require_transaction_inventory "$records"
        rm "$records/unknown"

        other=33333333333333333333333333333333
        printf '%s\n' truncated > "$records/transaction-$other-000000.record"
        chmod 0400 "$records/transaction-$other-000000.record"
        ! s72_atomic_require_transaction_inventory "$records"
        ''',
        HELPER,
        tmp_path,
    )
    assert result.returncode == 0, result.stderr or result.stdout


@pytest.mark.skipif(os.name != "posix", reason="Linux durable publication runs on required Ubuntu CI")
def test_linux_snapshot_publication_is_same_filesystem_presealed_and_foreign_safe(
    tmp_path: pathlib.Path,
) -> None:
    result = _run_privileged_bash(
        r'''
        set -eu
        HELPER=$1; ROOT=$2; caller_uid=$3; caller_gid=$4
        . "$HELPER"
        snapshots=$ROOT/snapshots
        mkdir -p "$snapshots"
        chown root:root "$ROOT" "$snapshots"
        chmod 0700 "$ROOT" "$snapshots"
        require_root_tree() { s72_atomic_require_root_tree "$@"; }
        require_root_owned_regular() { s72_atomic_require_root_owned_regular "$@"; }
        s72_atomic_preflight_snapshot() { s72_atomic_verify_manifest "$1"; }

        tx=11111111111111111111111111111111
        stage=$(s72_atomic_create_snapshot_stage "$snapshots" "$tx")
        printf '%s\n' payload > "$stage/payload"
        chown root:root "$stage/payload"
        chmod 0400 "$stage/payload"
        s72_atomic_write_manifest "$stage"
        stage_device=$(stat -c %d "$stage")
        published=$(s72_atomic_publish_snapshot "$stage" "$snapshots" .rollback.$tx)
        test "$published" = "$snapshots/.rollback.$tx"
        test "$(stat -c %d "$published")" = "$stage_device"
        s72_atomic_verify_snapshot_seal "$published"

        foreign=22222222222222222222222222222222
        mkdir "$snapshots/.snapshot-stage-$foreign"
        printf '%s\n' preserve > "$snapshots/.snapshot-stage-$foreign/foreign"
        ! s72_atomic_create_snapshot_stage "$snapshots" "$foreign"
        grep -qx preserve "$snapshots/.snapshot-stage-$foreign/foreign"

        replaced=33333333333333333333333333333333
        replaced_stage=$(s72_atomic_create_snapshot_stage "$snapshots" "$replaced")
        mv "$replaced_stage" "$snapshots/original-stage"
        mkdir "$replaced_stage"
        cp "$snapshots/original-stage/transaction-owner" "$replaced_stage/transaction-owner"
        printf '%s\n' preserve-replacement > "$replaced_stage/foreign"
        ! s72_atomic_remove_owned_stage "$replaced_stage" "$replaced"
        grep -qx preserve-replacement "$replaced_stage/foreign"
        chown -R "$caller_uid:$caller_gid" "$ROOT"
        ''',
        HELPER,
        tmp_path,
    )
    assert result.returncode == 0, result.stderr or result.stdout


@pytest.mark.skipif(os.name != "posix", reason="Linux production entry runs on required Ubuntu CI")
def test_linux_production_recovery_entry_is_lock_first_and_idempotent_across_mounts(
    tmp_path: pathlib.Path,
) -> None:
    result = _run_privileged_bash(
        r'''
        set -eu
        INSTALLER=$1; ROLLBACK=$2; HELPER=$3; ROOT=$4; caller_uid=$5; caller_gid=$6
        trap 'rc=$?; chown -R "$caller_uid:$caller_gid" "$ROOT" >/dev/null 2>&1 || :; exit "$rc"' EXIT
        unshare --mount --fork --pid --mount-proc --propagation private \
          /bin/sh -eux -c '
            INSTALLER=$1; ROLLBACK=$2; HELPER=$3; ROOT=$4
            mount -t tmpfs -o mode=0755 s72-run-test /run
            mount -t tmpfs -o mode=0755 s72-var-test /var/lib
            mount -t tmpfs -o mode=0755 s72-opt-test /opt
            test "$(stat -c %d /run)" != "$(stat -c %d /var/lib)"
            install -d -o root -g root -m 0755 /run/lock /opt/s72-source/deploy/opensandbox/lib
            install -o root -g root -m 0600 /dev/null \
              /run/lock/opensandbox-gateway-s72-install.lock
            install -o root -g root -m 0755 "$INSTALLER" \
              /opt/s72-source/deploy/opensandbox/install-s72.sh
            install -o root -g root -m 0755 "$ROLLBACK" \
              /opt/s72-source/deploy/opensandbox/rollback-s72.sh
            install -o root -g root -m 0644 "$HELPER" \
              /opt/s72-source/deploy/opensandbox/lib/s72-atomic-recovery-authority.sh
            /opt/s72-source/deploy/opensandbox/install-s72.sh --recover
            /opt/s72-source/deploy/opensandbox/rollback-s72.sh --recover
            test -d /var/lib/opensandbox-gateway-deploy/snapshots
            test -d /var/lib/opensandbox-gateway-deploy/transactions
            test -z "$(find /var/lib/opensandbox-gateway-deploy/transactions -mindepth 1 -print -quit)"

            . /opt/s72-source/deploy/opensandbox/lib/s72-atomic-recovery-authority.sh
            DEPLOY=/var/lib/opensandbox-gateway-deploy
            RECORDS=$DEPLOY/transactions
            SNAPSHOTS=$DEPLOY/snapshots
            tx=11111111111111111111111111111111
            recovery=.rollback.$tx
            apply=.rollback.22222222222222222222222222222222
            commit=1111111111111111111111111111111111111111
            s72_atomic_publish_transaction_record "$RECORDS" "$tx" 000000 install reserved \
              "$recovery" "$apply" none "$commit" sealed-crash-evidence none none >/dev/null
            transaction_workspace=$(s72_atomic_prepare_workspace "$DEPLOY" transaction "$tx")
            s72_atomic_bind_transaction_stage "$RECORDS" "$tx" \
              "$(s72_atomic_node_identity "$transaction_workspace")"
            snapshot_stage=$(s72_atomic_create_snapshot_stage "$SNAPSHOTS" "$tx")

            # This is the durable state left by death after reservation and stage creation.
            /opt/s72-source/deploy/opensandbox/install-s72.sh --recover
            s72_atomic_load_transaction "$RECORDS" "$tx"
            test "$S72_TX_PHASE" = cleaned
            test ! -e "$transaction_workspace" && test ! -e "$snapshot_stage"
            /opt/s72-source/deploy/opensandbox/install-s72.sh --recover

            foreign=33333333333333333333333333333333
            foreign_stage=$SNAPSHOTS/.snapshot-stage-$foreign
            mkdir "$foreign_stage"
            printf '%s\n' preserve-foreign > "$foreign_stage/payload"
            ! /opt/s72-source/deploy/opensandbox/install-s72.sh --recover
            grep -qx preserve-foreign "$foreign_stage/payload"
            rm -rf "$foreign_stage"

            mkfifo "$DEPLOY/foreign-fifo"
            ! /opt/s72-source/deploy/opensandbox/install-s72.sh --recover
            test -p "$DEPLOY/foreign-fifo"
            rm "$DEPLOY/foreign-fifo"

            torn=44444444444444444444444444444444
            torn_record=$RECORDS/transaction-$torn-000000.record
            printf '%s\n' truncated > "$torn_record"
            chown root:root "$torn_record"; chmod 0400 "$torn_record"
            ! /opt/s72-source/deploy/opensandbox/install-s72.sh --recover
            grep -qx truncated "$torn_record"
          ' s72-production-entry "$INSTALLER" "$ROLLBACK" "$HELPER" "$ROOT"
        ''',
        INSTALLER,
        ROLLBACK,
        HELPER,
        tmp_path,
    )
    assert result.returncode == 0, result.stderr or result.stdout


@pytest.mark.parametrize(
    ("active_contour", "expected_success"),
    [
        ("inactive", True),
        ("active", True),
        ("stop-error", False),
        ("show-error", False),
        ("empty", False),
        ("failed", False),
        ("activating", False),
        ("deactivating", False),
        ("reloading", False),
        ("unknown", False),
    ],
)
def test_restore_snapshot_requires_exact_inactive_state_before_stopped_phase(
    tmp_path: pathlib.Path,
    active_contour: str,
    expected_success: bool,
) -> None:
    result = _run_bash(
        rf'''
        set -eu
        SCRIPT=$(cygpath -u "$1" 2>/dev/null || printf '%s\n' "$1")
        ROOT=$(cygpath -u "$2" 2>/dev/null || printf '%s\n' "$2")
        eval "$(sed '/^install_main "\$@"$/d' "$SCRIPT")"
        phases=$ROOT/phases
        state=$ROOT/active-state
        case {shlex.quote(active_contour)} in
          active|stop-error) printf '%s\n' active > "$state" ;;
          show-error) printf '%s\n' inactive > "$state" ;;
          *) printf '%s\n' {shlex.quote(active_contour)} > "$state" ;;
        esac
        s72_atomic_preflight_snapshot() {{ :; }}
        s72_atomic_verify_snapshot_seal() {{ :; }}
        s72_atomic_load_transaction() {{
          S72_TX_PHASE=reserved
          S72_TX_OPERATION=rollback
          S72_TX_PREVIOUS_PHASE=reserved
        }}
        preflight_live_state() {{ :; }}
        s72_atomic_require_exact_lifecycle() {{ :; }}
        s72_atomic_advance_transaction() {{
          printf '%s\n' "$3" >> "$phases"
          S72_TX_PHASE=$3
        }}
        restore_snapshot_payload() {{ :; }}
        restore_snapshot_runtime() {{ :; }}
        systemctl() {{
          case "$1" in
            stop)
              printf '%s\n' inactive > "$state"
              test {shlex.quote(active_contour)} != stop-error
              ;;
            show)
              test {shlex.quote(active_contour)} != show-error || return 73
              cat "$state"
              ;;
            *) return 1 ;;
          esac
        }}
        if test {str(expected_success).lower()} = true; then
          s72_atomic_restore_snapshot "$ROOT/snapshot" \
            11111111111111111111111111111111 "$ROOT/records"
          grep -qx stopped "$phases"
        else
          ! s72_atomic_restore_snapshot "$ROOT/snapshot" \
            11111111111111111111111111111111 "$ROOT/records"
          ! grep -qx stopped "$phases"
        fi
        ''',
        INSTALLER,
        tmp_path,
    )
    assert result.returncode == 0, result.stderr or result.stdout


@pytest.mark.parametrize("failure_mode", ["disable-failure", "enabled-drift"])
def test_restore_runtime_fails_closed_before_state_advance(
    tmp_path: pathlib.Path,
    failure_mode: str,
) -> None:
    result = _run_bash(
        rf'''
        set -eu
        SCRIPT=$(cygpath -u "$1" 2>/dev/null || printf '%s\n' "$1")
        ROOT=$(cygpath -u "$2" 2>/dev/null || printf '%s\n' "$2")
        eval "$(sed '/^install_main "\$@"$/d' "$SCRIPT")"
        mkdir -p "$ROOT/snapshot"
        for unit in opensandbox-gateway.service opensandbox-gateway-helper.service; do
          : > "$ROOT/snapshot/$unit.present"
          : > "$ROOT/snapshot/$unit.inactive"
          : > "$ROOT/snapshot/$unit.disabled"
        done
        systemctl() {{
          case "$1" in
            daemon-reload|stop) return 0 ;;
            disable)
              test {shlex.quote(failure_mode)} != disable-failure
              ;;
            show)
              case "$4" in
                ActiveState) printf '%s\n' inactive ;;
                UnitFileState) printf '%s\n' enabled ;;
                LoadState) printf '%s\n' loaded ;;
                *) return 1 ;;
              esac
              ;;
            *) return 1 ;;
          esac
        }}
        ! restore_snapshot_runtime "$ROOT/snapshot"
        ''',
        INSTALLER,
        tmp_path,
    )
    assert result.returncode == 0, result.stderr or result.stdout


@pytest.mark.parametrize(
    ("post_disable_contour", "expected_success"),
    [
        ("exact-absent", True),
        ("loaded-drift", False),
        ("load-query-error", False),
    ],
)
def test_absent_unit_restore_revalidates_all_systemd_states_after_disable(
    tmp_path: pathlib.Path,
    post_disable_contour: str,
    expected_success: bool,
) -> None:
    result = _run_bash(
        rf'''
        set -eu
        SCRIPT=$(cygpath -u "$1" 2>/dev/null || printf '%s\n' "$1")
        ROOT=$(cygpath -u "$2" 2>/dev/null || printf '%s\n' "$2")
        eval "$(sed '/^install_main "\$@"$/d' "$SCRIPT")"
        snapshot=$ROOT/snapshot
        mkdir -p "$snapshot" "$ROOT/state"
        systemctl() {{
          command_name=$1
          unit=${{2:-}}
          case "$command_name" in
            daemon-reload|stop) return 0 ;;
            disable) : > "$ROOT/state/$unit.disabled" ;;
            show)
              property=$4
              if test -f "$ROOT/state/$unit.disabled"; then
                case "$property" in
                  UnitFileState) printf '%s' '' ;;
                  ActiveState) printf '%s\n' inactive ;;
                  LoadState)
                    case {shlex.quote(post_disable_contour)} in
                      exact-absent) printf '%s\n' not-found ;;
                      loaded-drift) printf '%s\n' loaded ;;
                      load-query-error) return 74 ;;
                    esac
                    ;;
                  *) return 1 ;;
                esac
              else
                case "$property" in
                  UnitFileState) printf '%s\n' enabled ;;
                  ActiveState) printf '%s\n' inactive ;;
                  LoadState) printf '%s\n' not-found ;;
                  *) return 1 ;;
                esac
              fi
              ;;
            *) return 1 ;;
          esac
        }}
        if test {str(expected_success).lower()} = true; then
          restore_snapshot_runtime "$snapshot"
        else
          ! restore_snapshot_runtime "$snapshot"
        fi
        ''',
        INSTALLER,
        tmp_path,
    )
    assert result.returncode == 0, result.stderr or result.stdout


@pytest.mark.parametrize(
    "systemd_contour",
    [
        "query-error",
        "failed",
        "activating",
        "static",
        "masked",
        "linked",
        "enabled-runtime",
        "absent-loaded",
        "present-not-found",
    ],
)
def test_snapshot_state_rejects_unsealed_systemd_baselines_before_mutation(
    tmp_path: pathlib.Path,
    systemd_contour: str,
) -> None:
    result = _run_bash(
        rf'''
        set -eu
        SCRIPT=$(cygpath -u "$1" 2>/dev/null || printf '%s\n' "$1")
        ROOT=$(cygpath -u "$2" 2>/dev/null || printf '%s\n' "$2")
        eval "$(sed '/^install_main "\$@"$/d' "$SCRIPT")"
        SYSTEMD_DIR=$ROOT/systemd
        CONFIG_DIR=$ROOT/config
        WORKSPACE_ROOT=$ROOT/workspaces
        DEPLOY_STATE=$ROOT/deploy
        AUTHORITY_SHA_STATE=$DEPLOY_STATE/current-authority-sha
        AUTHORITY_EVIDENCE_STATE=$DEPLOY_STATE/current-authority-evidence
        ROLLBACK_POINTER=$DEPLOY_STATE/previous-snapshot
        CURRENT_LINK=$ROOT/current
        EXPECTED_AUTHORITY_SHA=1111111111111111111111111111111111111111
        AUTHORITY_EVIDENCE_ID=sealed-systemd-baseline
        snapshot=$ROOT/snapshot
        mkdir -p "$SYSTEMD_DIR" "$WORKSPACE_ROOT" "$DEPLOY_STATE" "$snapshot"
        printf '%s\n' owner > "$snapshot/transaction-owner"
        case {shlex.quote(systemd_contour)} in
          absent-loaded) ;;
          *)
            : > "$SYSTEMD_DIR/opensandbox-gateway.service"
            : > "$SYSTEMD_DIR/opensandbox-gateway-helper.service"
            ;;
        esac
        preflight_live_state() {{ :; }}
        getfacl() {{ printf '%s\n' acl; }}
        chown() {{ :; }}
        stat() {{
          if test "$1:$2" = '-c:%u'; then
            printf '%s\n' 0
          else
            command stat "$@"
          fi
        }}
        write_manifest() {{ :; }}
        require_root_tree() {{ :; }}
        verify_manifest() {{ :; }}
        preflight_snapshot() {{ :; }}
        s72_atomic_write_lifecycle_authority() {{ : > "$1"; }}
        systemctl() {{
          test "$1" = show || return 1
          property=$4
          case {shlex.quote(systemd_contour)}:$property in
            query-error:*) return 1 ;;
            failed:LoadState|activating:LoadState|static:LoadState|masked:LoadState|linked:LoadState|enabled-runtime:LoadState|absent-loaded:LoadState)
              printf '%s\n' loaded
              ;;
            present-not-found:LoadState) printf '%s\n' not-found ;;
            failed:ActiveState) printf '%s\n' failed ;;
            activating:ActiveState) printf '%s\n' activating ;;
            static:ActiveState|masked:ActiveState|linked:ActiveState|enabled-runtime:ActiveState|absent-loaded:ActiveState|present-not-found:ActiveState)
              printf '%s\n' inactive
              ;;
            failed:UnitFileState|activating:UnitFileState|present-not-found:UnitFileState)
              printf '%s\n' disabled
              ;;
            static:UnitFileState) printf '%s\n' static ;;
            masked:UnitFileState) printf '%s\n' masked ;;
            linked:UnitFileState) printf '%s\n' linked ;;
            enabled-runtime:UnitFileState) printf '%s\n' enabled-runtime ;;
            absent-loaded:UnitFileState) printf '%s' '' ;;
            *) return 1 ;;
          esac
        }}
        ! snapshot_state "$snapshot"
        ''',
        INSTALLER,
        tmp_path,
    )
    assert result.returncode == 0, result.stderr or result.stdout


@pytest.mark.parametrize(
    ("presence", "load_state", "active_state", "unit_file_state"),
    [
        ("present", "loaded", "active", "enabled"),
        ("present", "loaded", "inactive", "disabled"),
        ("absent", "not-found", "inactive", ""),
    ],
)
def test_snapshot_state_preserves_each_supported_exact_systemd_baseline(
    tmp_path: pathlib.Path,
    presence: str,
    load_state: str,
    active_state: str,
    unit_file_state: str,
) -> None:
    result = _run_bash(
        rf'''
        set -eu
        SCRIPT=$(cygpath -u "$1" 2>/dev/null || printf '%s\n' "$1")
        ROOT=$(cygpath -u "$2" 2>/dev/null || printf '%s\n' "$2")
        eval "$(sed '/^install_main "\$@"$/d' "$SCRIPT")"
        SYSTEMD_DIR=$ROOT/systemd
        CONFIG_DIR=$ROOT/config
        WORKSPACE_ROOT=$ROOT/workspaces
        DEPLOY_STATE=$ROOT/deploy
        AUTHORITY_SHA_STATE=$DEPLOY_STATE/current-authority-sha
        AUTHORITY_EVIDENCE_STATE=$DEPLOY_STATE/current-authority-evidence
        ROLLBACK_POINTER=$DEPLOY_STATE/previous-snapshot
        CURRENT_LINK=$ROOT/current
        EXPECTED_AUTHORITY_SHA=1111111111111111111111111111111111111111
        AUTHORITY_EVIDENCE_ID=sealed-systemd-baseline
        snapshot=$ROOT/snapshot
        mkdir -p "$SYSTEMD_DIR" "$WORKSPACE_ROOT" "$DEPLOY_STATE" "$snapshot"
        printf '%s\n' owner > "$snapshot/transaction-owner"
        if test {shlex.quote(presence)} = present; then
          : > "$SYSTEMD_DIR/opensandbox-gateway.service"
          : > "$SYSTEMD_DIR/opensandbox-gateway-helper.service"
        fi
        preflight_live_state() {{ :; }}
        getfacl() {{ printf '%s\n' acl; }}
        chown() {{ :; }}
        stat() {{
          if test "$1:$2" = '-c:%u'; then
            printf '%s\n' 0
          else
            command stat "$@"
          fi
        }}
        write_manifest() {{ :; }}
        require_root_tree() {{ :; }}
        verify_manifest() {{ :; }}
        preflight_snapshot() {{ :; }}
        s72_atomic_write_lifecycle_authority() {{ : > "$1"; }}
        systemctl() {{
          case "$1" in
            show)
              case "$4" in
                LoadState) printf '%s\n' {shlex.quote(load_state)} ;;
                ActiveState) printf '%s\n' {shlex.quote(active_state)} ;;
                UnitFileState) printf '%s\n' {shlex.quote(unit_file_state)} ;;
                *) return 1 ;;
              esac
              ;;
            is-active) test {shlex.quote(active_state)} = active ;;
            is-enabled) test {shlex.quote(unit_file_state)} = enabled ;;
            *) return 1 ;;
          esac
        }}
        snapshot_state "$snapshot"
        for unit in opensandbox-gateway.service opensandbox-gateway-helper.service; do
          test -f "$snapshot/$unit.{active_state}"
          if test {shlex.quote(unit_file_state)} = enabled; then
            test -f "$snapshot/$unit.enabled"
          else
            test -f "$snapshot/$unit.disabled"
          fi
        done
        ''',
        INSTALLER,
        tmp_path,
    )
    assert result.returncode == 0, result.stderr or result.stdout


@pytest.mark.parametrize(
    "process_contour",
    ["live-before", "enumeration-error", "live-after", "post-group-live"],
)
def test_gateway_identity_restore_refuses_live_uid_or_enumeration_drift(
    tmp_path: pathlib.Path,
    process_contour: str,
) -> None:
    result = _run_bash(
        rf'''
        set -eu
        SCRIPT=$(cygpath -u "$1" 2>/dev/null || printf '%s\n' "$1")
        ROOT=$(cygpath -u "$2" 2>/dev/null || printf '%s\n' "$2")
        eval "$(sed '/^install_main "\$@"$/d' "$SCRIPT")"
        DEPLOY_STATE=$ROOT/deploy
        RUNTIME_STATE=$ROOT/runtime
        tx=11111111111111111111111111111111
        workspace=$DEPLOY_STATE/.s72-transaction-$tx
        snapshot=$ROOT/snapshot
        mkdir -p "$workspace" "$snapshot"
        printf '%s\n' schema=s72-transaction-owner-v1 "transaction=$tx" \
          "root=$(command stat -c %d:%i "$workspace")" > "$workspace/transaction-owner"
        printf '%s\n' 62001 > "$snapshot/gateway-service-uid"
        : > "$snapshot/gateway-user.absent"
        : > "$snapshot/gateway-group.absent"
        : > "$snapshot/runtime-state.absent"
        printf '%s\n' opensandbox-gateway:x:62001: > "$workspace/gateway-group.intent"
        printf '%s\n' opensandbox-gateway:x:62001:62001::/nonexistent:/usr/sbin/nologin \
          > "$workspace/gateway-user.intent"
        chmod 0400 "$workspace/transaction-owner" "$workspace/gateway-group.intent" \
          "$workspace/gateway-user.intent" "$snapshot/gateway-service-uid"
        chmod 0600 "$snapshot"/*.absent
        s72_atomic_require_root_owned_regular() {{ test -f "$1" && test ! -L "$1"; }}
        require_root_owned_regular() {{ test -f "$1" && test ! -L "$1"; }}
        USER_ENTRY=opensandbox-gateway:x:62001:62001::/nonexistent:/usr/sbin/nologin
        GROUP_ENTRY=opensandbox-gateway:x:62001:
        USERDEL_COUNT=0
        GROUPDEL_COUNT=0
        PROCESS_SCAN_COUNT=$ROOT/process-scan-count
        printf '%s\n' 0 > "$PROCESS_SCAN_COUNT"
        getent() {{
          database=$1; key=$2
          case "$database:$key" in
            passwd:opensandbox-gateway|passwd:62001)
              test -n "$USER_ENTRY" || return 2
              printf '%s\n' "$USER_ENTRY"
              ;;
            group:opensandbox-gateway|group:62001)
              test -n "$GROUP_ENTRY" || return 2
              printf '%s\n' "$GROUP_ENTRY"
              ;;
            *) return 2 ;;
          esac
        }}
        userdel() {{ USERDEL_COUNT=$((USERDEL_COUNT + 1)); USER_ENTRY=; }}
        groupdel() {{ GROUPDEL_COUNT=$((GROUPDEL_COUNT + 1)); GROUP_ENTRY=; }}
        s72_list_process_uids() {{
          count=$(cat "$PROCESS_SCAN_COUNT")
          count=$((count + 1))
          printf '%s\n' "$count" > "$PROCESS_SCAN_COUNT"
          case {shlex.quote(process_contour)}:$count in
            enumeration-error:1) return 1 ;;
            live-before:1|live-after:2|post-group-live:4)
              printf '%s\n' '62001 62001 62001 62001 4242'
              ;;
            *) printf '%s\n' '0 0 0 0 1' ;;
          esac
        }}
        if restore_gateway_identity "$snapshot" "$tx"; then
          restore_status=0
        else
          restore_status=$?
        fi
        test "$restore_status" -ne 0
        case {shlex.quote(process_contour)} in
          live-after)
            test "$USERDEL_COUNT" -eq 1
            test "$GROUPDEL_COUNT" -eq 0
            test -z "$USER_ENTRY"
            test -n "$GROUP_ENTRY"
            ;;
          post-group-live)
            test "$USERDEL_COUNT" -eq 1
            test "$GROUPDEL_COUNT" -eq 1
            test -z "$USER_ENTRY"
            test -z "$GROUP_ENTRY"
            ;;
          *)
            test "$USERDEL_COUNT" -eq 0
            test "$GROUPDEL_COUNT" -eq 0
            test -n "$USER_ENTRY"
            test -n "$GROUP_ENTRY"
            ;;
        esac
        ''',
        INSTALLER,
        tmp_path,
    )
    assert result.returncode == 0, result.stderr or result.stdout


def test_uid_process_enumerator_is_streamed_and_read_bounded() -> None:
    source = INSTALLER.read_text(encoding="utf-8")
    body = source.split("require_no_live_uid_processes() {", 1)[1].split(
        "\ngateway_runtime_identity() {", 1
    )[0]

    assert "process_rows=$(s72_list_process_uids)" not in body
    assert "mkfifo" in body
    assert "wait \"$process_uid_producer_pid\"" in body
    assert "1048576" in body
    assert "131072" in body

    assert "process_uid_raw_identity=" in body
    assert "process_uid_bounded_identity=" in body
    assert "process_uid_raw_descriptor_identity=" in body
    assert "process_uid_bounded_descriptor_identity=" in body
    assert 's72_list_process_uids >&4' in body
    assert '<&3 >&6' in body
    assert "<&5" in body
    assert 'exec 3<"$process_uid_raw"' in body
    assert 'exec 4>"$process_uid_raw"' in body
    assert 'exec 5<"$process_uid_bounded"' in body
    assert 'exec 6>"$process_uid_bounded"' in body


@pytest.mark.parametrize(
    ("process_contour", "expected_success"),
    [
        ("valid", True),
        ("live", False),
        ("producer-error", False),
        ("malformed", False),
        ("over-bytes", False),
        ("over-rows", False),
    ],
)
def test_uid_process_enumerator_rejects_bounded_and_producer_failures(
    tmp_path: pathlib.Path,
    process_contour: str,
    expected_success: bool,
) -> None:
    result = _run_bash(
        rf'''
        set -eu
        SCRIPT=$(cygpath -u "$1" 2>/dev/null || printf '%s\n' "$1")
        ROOT=$(cygpath -u "$2" 2>/dev/null || printf '%s\n' "$2")
        eval "$(sed '/^install_main "\$@"$/d' "$SCRIPT")"
        s72_list_process_uids() {{
          case {shlex.quote(process_contour)} in
            valid) printf '%s\n' '0 0 0 0 1' ;;
            live) printf '%s\n' '62001 62001 62001 62001 4242' ;;
            producer-error) printf '%s\n' '0 0 0 0 1'; return 73 ;;
            malformed) printf '%s\n' '0 0 0 1' ;;
            over-bytes) awk 'BEGIN {{ for (i = 0; i < 7; i++) print "0 0 0 0 1" }}' ;;
            over-rows)
              awk 'BEGIN {{ for (i = 0; i < 17; i++) print "0 0 0 0 1" }}'
              ;;
          esac
        }}
        case {shlex.quote(process_contour)} in
          over-bytes) process_max_bytes=64; process_max_rows=16 ;;
          *) process_max_bytes=1024; process_max_rows=16 ;;
        esac
        if require_no_live_uid_processes 62001 "$process_max_bytes" "$process_max_rows" \
            >"$ROOT/stdout" 2>"$ROOT/stderr"; then
          result_status=0
        else
          result_status=$?
        fi
        test ! -s "$ROOT/stdout"
        test ! -s "$ROOT/stderr"
        if test {str(expected_success).lower()} = true; then
          test "$result_status" -eq 0
        else
          test "$result_status" -ne 0
        fi
        ''',
        INSTALLER,
        tmp_path,
    )
    assert result.returncode == 0, result.stderr or result.stdout


@pytest.mark.skipif(
    os.name != "posix",
    reason="FIFO replacement identity hostile runs on required Ubuntu CI",
)
def test_uid_process_enumerator_preserves_replaced_foreign_fifo(
    tmp_path: pathlib.Path,
) -> None:
    result = _run_bash(
        r'''
        set -eu
        SCRIPT=$1
        ROOT=$2
        export TMPDIR=$ROOT
        eval "$(sed '/^install_main "\$@"$/d' "$SCRIPT")"
        ready=$ROOT/producer-ready
        proceed=$ROOT/producer-proceed
        foreign_identity_file=$ROOT/foreign-identity
        s72_list_process_uids() {
          : > "$ready"
          while test ! -e "$proceed"; do sleep 0.01; done
          printf '%s\n' '0 0 0 0 1'
        }
        (
          attempt=0
          while test ! -e "$ready"; do
            attempt=$((attempt + 1))
            test "$attempt" -lt 1000 || exit 91
            sleep 0.01
          done
          raw=$(find "$ROOT" -type p -name process-table.raw -print -quit)
          test -n "$raw"
          foreign=${raw%/*}/foreign.raw
          /usr/bin/mkfifo -m 0600 "$foreign"
          foreign_identity=$(stat -c '%d:%i:%F:%u:%g:%a' "$foreign")
          printf '%s\n' "$foreign_identity" > "$foreign_identity_file"
          mv -f -- "$foreign" "$raw"
          : > "$proceed"
        ) &
        replacer_pid=$!
        if require_no_live_uid_processes 62001 >"$ROOT/stdout" 2>"$ROOT/stderr"; then
          result_status=0
        else
          result_status=$?
        fi
        wait "$replacer_pid"
        test "$result_status" -ne 0
        test ! -s "$ROOT/stdout"
        test ! -s "$ROOT/stderr"
        raw=$(find "$ROOT" -type p -name process-table.raw -print -quit)
        test -n "$raw"
        test "$(stat -c '%d:%i:%F:%u:%g:%a' "$raw")" = "$(cat "$foreign_identity_file")"
        ''',
        INSTALLER,
        tmp_path,
    )
    assert result.returncode == 0, result.stderr or result.stdout


@pytest.mark.parametrize(
    ("user_entry", "group_entry", "runtime_identity"),
    [
        (
            "opensandbox-gateway:x:62002:62001::/nonexistent:/usr/sbin/nologin",
            "opensandbox-gateway:x:62001:",
            "1:2:62001:62001:700",
        ),
        (
            "opensandbox-gateway:x:62001:62002::/nonexistent:/usr/sbin/nologin",
            "opensandbox-gateway:x:62001:",
            "1:2:62001:62001:700",
        ),
        (
            "opensandbox-gateway:x:62001:62001::/var/lib/opensandbox-gateway:/usr/sbin/nologin",
            "opensandbox-gateway:x:62001:",
            "1:2:62001:62001:700",
        ),
        (
            "opensandbox-gateway:x:62001:62001::/nonexistent:/bin/sh",
            "opensandbox-gateway:x:62001:",
            "1:2:62001:62001:700",
        ),
        (
            "opensandbox-gateway:x:62001:62001::/nonexistent:/usr/sbin/nologin",
            "opensandbox-gateway:x:62001:foreign-member",
            "1:2:62001:62001:700",
        ),
        (
            "opensandbox-gateway:x:62001:62001::/nonexistent:/usr/sbin/nologin",
            "opensandbox-gateway:x:62001:",
            "1:2:62002:62001:700",
        ),
    ],
)
def test_gateway_identity_preflight_rejects_drifted_existing_subjects(
    tmp_path: pathlib.Path,
    user_entry: str,
    group_entry: str,
    runtime_identity: str,
) -> None:
    config = tmp_path / "config"
    runtime = tmp_path / "runtime"
    config.mkdir()
    runtime.mkdir()
    (config / "gateway.env").write_text(
        "OPENSANDBOX_GATEWAY_ALLOWED_UID=62001\n",
        encoding="utf-8",
    )
    result = _run_bash(
        rf'''
        set -eu
        SCRIPT=$(cygpath -u "$1" 2>/dev/null || printf '%s\n' "$1")
        CONFIG=$(cygpath -u "$2" 2>/dev/null || printf '%s\n' "$2")
        RUNTIME=$(cygpath -u "$3" 2>/dev/null || printf '%s\n' "$3")
        eval "$(sed '/^install_main "\$@"$/d' "$SCRIPT")"
        RUNTIME_STATE=$RUNTIME
        USER_ENTRY={shlex.quote(user_entry)}
        GROUP_ENTRY={shlex.quote(group_entry)}
        RUNTIME_IDENTITY={shlex.quote(runtime_identity)}
        getent() {{
          database=$1; key=$2
          case "$database:$key" in
            passwd:opensandbox-gateway|passwd:62001|passwd:62002)
              printf '%s\n' "$USER_ENTRY"
              ;;
            group:opensandbox-gateway|group:62001|group:62002)
              printf '%s\n' "$GROUP_ENTRY"
              ;;
            *) return 2 ;;
          esac
        }}
        gateway_runtime_identity() {{ printf '%s\n' "$RUNTIME_IDENTITY"; }}
        uid=$(gateway_service_uid_from_config_at "$CONFIG")
        test "$uid" = 62001
        ! require_gateway_identity_contract "$uid"
        ''',
        INSTALLER,
        config,
        runtime,
    )
    assert result.returncode == 0, result.stderr or result.stdout


@pytest.mark.parametrize("boundary", ["group", "user", "runtime"])
def test_gateway_identity_recovery_is_idempotent_after_each_create_boundary(
    tmp_path: pathlib.Path,
    boundary: str,
) -> None:
    result = _run_bash(
        rf'''
        set -eu
        SCRIPT=$(cygpath -u "$1" 2>/dev/null || printf '%s\n' "$1")
        ROOT=$(cygpath -u "$2" 2>/dev/null || printf '%s\n' "$2")
        eval "$(sed '/^install_main "\$@"$/d' "$SCRIPT")"
        DEPLOY_STATE=$ROOT/deploy
        RUNTIME_STATE=$ROOT/runtime
        tx=11111111111111111111111111111111
        workspace=$DEPLOY_STATE/.s72-transaction-$tx
        snapshot=$ROOT/snapshot
        mkdir -p "$workspace" "$snapshot"
        printf '%s\n' schema=s72-transaction-owner-v1 "transaction=$tx" \
          "root=$(command stat -c %d:%i "$workspace")" > "$workspace/transaction-owner"
        printf '%s\n' 62001 > "$snapshot/gateway-service-uid"
        : > "$snapshot/gateway-user.absent"
        : > "$snapshot/gateway-group.absent"
        : > "$snapshot/runtime-state.absent"
        chmod 0400 "$snapshot/gateway-service-uid"
        chmod 0600 "$snapshot"/*.absent
        printf '%s\n' 'opensandbox-gateway:x:62001:' > "$workspace/gateway-group.intent"
        printf '%s\n' 'opensandbox-gateway:x:62001:62001::/nonexistent:/usr/sbin/nologin' \
          > "$workspace/gateway-user.intent"
        chmod 0400 "$workspace/transaction-owner" "$workspace/gateway-group.intent" \
          "$workspace/gateway-user.intent"
        s72_atomic_require_root_owned_regular() {{ test -f "$1" && test ! -L "$1"; }}
        s72_atomic_fsync_path() {{ :; }}
        USER_ENTRY=; GROUP_ENTRY=
        case {shlex.quote(boundary)} in
          group) GROUP_ENTRY=$(cat "$workspace/gateway-group.intent") ;;
          user)
            GROUP_ENTRY=$(cat "$workspace/gateway-group.intent")
            USER_ENTRY=$(cat "$workspace/gateway-user.intent")
            ;;
          runtime)
            GROUP_ENTRY=$(cat "$workspace/gateway-group.intent")
            USER_ENTRY=$(cat "$workspace/gateway-user.intent")
            mkdir "$RUNTIME_STATE"
            runtime_identity=$(s72_atomic_directory_identity "$RUNTIME_STATE")
            printf '%s\n' "$runtime_identity" > "$workspace/runtime-state.created-identity"
            chmod 0400 "$workspace/runtime-state.created-identity"
            ;;
        esac
        getent() {{
          database=$1; key=$2
          case "$database:$key" in
            passwd:opensandbox-gateway|passwd:62001)
              test -n "$USER_ENTRY" || return 2
              printf '%s\n' "$USER_ENTRY"
              ;;
            group:opensandbox-gateway|group:62001)
              test -n "$GROUP_ENTRY" || return 2
              printf '%s\n' "$GROUP_ENTRY"
              ;;
            *) return 2 ;;
          esac
        }}
        userdel() {{ USER_ENTRY=; }}
        groupdel() {{ GROUP_ENTRY=; }}
        s72_list_process_uids() {{ printf '%s\n' '0 0 0 0 1'; }}
        require_gateway_identity_matches_transaction "$snapshot" "$tx"
        restore_gateway_identity "$snapshot" "$tx"
        test -z "$USER_ENTRY" && test -z "$GROUP_ENTRY"
        test ! -e "$RUNTIME_STATE" && test ! -L "$RUNTIME_STATE"
        restore_gateway_identity "$snapshot" "$tx"
        ''',
        INSTALLER,
        tmp_path,
    )
    assert result.returncode == 0, result.stderr or result.stdout


def test_gateway_identity_creation_is_journaled_and_uses_a_private_runtime_stage(
    tmp_path: pathlib.Path,
) -> None:
    result = _run_bash(
        r'''
        set -eu
        SCRIPT=$(cygpath -u "$1" 2>/dev/null || printf '%s\n' "$1")
        ROOT=$(cygpath -u "$2" 2>/dev/null || printf '%s\n' "$2")
        eval "$(sed '/^install_main "\$@"$/d' "$SCRIPT")"
        DEPLOY_STATE=$ROOT/deploy
        TRANSACTION_RECORDS=$DEPLOY_STATE/transactions
        RUNTIME_STATE=$ROOT/var/lib/opensandbox-gateway
        tx=11111111111111111111111111111111
        workspace=$DEPLOY_STATE/.s72-transaction-$tx
        snapshot=$ROOT/snapshot
        mkdir -p "$workspace" "$snapshot" "$TRANSACTION_RECORDS" "${RUNTIME_STATE%/*}"
        printf '%s\n' schema=s72-transaction-owner-v1 "transaction=$tx" \
          "root=$(command stat -c %d:%i "$workspace")" > "$workspace/transaction-owner"
        printf '%s\n' 62001 > "$snapshot/gateway-service-uid"
        : > "$snapshot/gateway-user.absent"
        : > "$snapshot/gateway-group.absent"
        : > "$snapshot/runtime-state.absent"
        chmod 0400 "$workspace/transaction-owner" "$snapshot/gateway-service-uid"
        chmod 0600 "$snapshot"/*.absent
        s72_atomic_require_root_owned_regular() { test -f "$1" && test ! -L "$1"; }
        s72_atomic_require_root_tree() { test -d "$1" && test ! -L "$1"; }
        s72_atomic_require_root_owned_directory() { test -d "$1" && test ! -L "$1"; }
        s72_atomic_fsync_path() { :; }
        s72_atomic_directory_identity() {
          value=$(command stat -c %d:%i -- "$1")
          printf '%s:%s\n' "$value" 62001:62001:700
        }
        s72_atomic_prepare_workspace() {
          parent=$1; label=$2; transaction=$3
          prepared=$parent/.s72-$label-$transaction
          if test ! -d "$prepared"; then
            mkdir "$prepared"
            printf '%s\n' schema=s72-transaction-owner-v1 "transaction=$transaction" \
              "root=$(command stat -c %d:%i "$prepared")" > "$prepared/transaction-owner"
            chmod 0400 "$prepared/transaction-owner"
          fi
          printf '%s\n' "$prepared"
        }
        install() {
          test "$1" = -d || return 1
          mkdir "${@: -1}"
        }
        chown() { :; }
        USER_ENTRY=; GROUP_ENTRY=
        getent() {
          database=$1; key=$2
          case "$database:$key" in
            passwd:opensandbox-gateway|passwd:62001)
              test -n "$USER_ENTRY" || return 2
              printf '%s\n' "$USER_ENTRY"
              ;;
            group:opensandbox-gateway|group:62001)
              test -n "$GROUP_ENTRY" || return 2
              printf '%s\n' "$GROUP_ENTRY"
              ;;
            *) return 2 ;;
          esac
        }
        groupadd() { GROUP_ENTRY=opensandbox-gateway:x:62001:; }
        useradd() { USER_ENTRY=opensandbox-gateway:x:62001:62001::/nonexistent:/usr/sbin/nologin; }
        userdel() { USER_ENTRY=; }
        groupdel() { GROUP_ENTRY=; }
        s72_list_process_uids() { printf '%s\n' '0 0 0 0 1'; }
        : > "$ROOT/phases"
        s72_atomic_advance_transaction() { printf '%s\n' "$3" >> "$ROOT/phases"; }

        ensure_gateway_identity "$snapshot" "$tx"
        test "$GROUP_ENTRY" = opensandbox-gateway:x:62001:
        test "$USER_ENTRY" = opensandbox-gateway:x:62001:62001::/nonexistent:/usr/sbin/nologin
        test -d "$RUNTIME_STATE"
        test ! -e "${RUNTIME_STATE%/*}/.s72-runtime-$tx/runtime.new"
        test "$(cat "$ROOT/phases")" = "$(printf '%s\n' \
          identity-group-intent identity-group-ready identity-user-intent \
          identity-user-ready identity-runtime-intent identity-ready)"

        restore_gateway_identity "$snapshot" "$tx"
        test -z "$USER_ENTRY" && test -z "$GROUP_ENTRY"
        test ! -e "$RUNTIME_STATE" && test ! -L "$RUNTIME_STATE"
        ''',
        INSTALLER,
        tmp_path,
    )
    assert result.returncode == 0, result.stderr or result.stdout
