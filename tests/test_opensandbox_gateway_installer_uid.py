from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "deploy" / "opensandbox" / "install-s72.sh"
ROLLBACK = ROOT / "deploy" / "opensandbox" / "rollback-s72.sh"


def _find_shell() -> str | None:
    shell = shutil.which("sh")
    if shell is not None:
        return shell
    git = shutil.which("git")
    if git is None:
        return None
    bundled_shell = Path(git).resolve().parents[1] / "bin" / "sh.exe"
    return str(bundled_shell) if bundled_shell.is_file() else None


SHELL = _find_shell()

pytestmark = pytest.mark.skipif(SHELL is None, reason="POSIX sh is required")


def _script_prefix(path: Path, entrypoint: str) -> str:
    script = path.read_text(encoding="utf-8")
    marker = f'\n{entrypoint} "$@"'
    assert script.count(marker) == 1
    return script.rsplit(marker, 1)[0]


def _run_function(
    tmp_path: Path,
    source: Path,
    entrypoint: str,
    command: str,
    *,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    harness = tmp_path / f"{source.stem}-harness.sh"
    harness.write_text(
        _script_prefix(source, entrypoint) + "\n" + command + "\n",
        encoding="utf-8",
        newline="\n",
    )
    environment = os.environ.copy()
    if env:
        environment.update(env)
    return subprocess.run(
        [SHELL, str(harness)],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8", newline="\n")
    path.chmod(0o755)


def _fake_accounts(
    tmp_path: Path,
    *,
    passwd: str = "",
    group: str = "",
) -> tuple[dict[str, str], Path, Path]:
    database = tmp_path / "accounts"
    database.mkdir()
    (database / "passwd").write_text(passwd, encoding="utf-8", newline="\n")
    (database / "group").write_text(group, encoding="utf-8", newline="\n")
    command_log = tmp_path / "account-commands.log"
    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()

    _write_executable(
        fake_bin / "getent",
        """#!/bin/sh
database=$1
key=${2-}
file=$FAKE_ACCOUNT_DB/$database
test -f "$file" || exit 3
if test -z "$key"; then
  cat "$file"
  exit 0
fi
awk -F: -v key="$key" '
  $1 == key || $3 == key { print; found = 1 }
  END { if (!found) exit 2 }
' "$file"
""",
    )
    _write_executable(
        fake_bin / "groupadd",
        """#!/bin/sh
printf 'groupadd %s\\n' "$*" >> "$FAKE_ACCOUNT_LOG"
gid=
name=
while test "$#" -gt 0; do
  case "$1" in
    --system) shift ;;
    --gid) gid=$2; shift 2 ;;
    *) name=$1; shift ;;
  esac
done
test -n "$gid" && test -n "$name"
printf '%s:x:%s:\\n' "$name" "$gid" > "$FAKE_ACCOUNT_DB/group"
""",
    )
    _write_executable(
        fake_bin / "useradd",
        """#!/bin/sh
printf 'useradd %s\\n' "$*" >> "$FAKE_ACCOUNT_LOG"
test "${FAKE_USERADD_EXIT:-0}" -eq 0 || exit "$FAKE_USERADD_EXIT"
uid=
group_name=
home=
shell=
name=
while test "$#" -gt 0; do
  case "$1" in
    --system|--no-create-home) shift ;;
    --uid) uid=$2; shift 2 ;;
    --gid) group_name=$2; shift 2 ;;
    --home-dir) home=$2; shift 2 ;;
    --shell) shell=$2; shift 2 ;;
    --comment) shift 2 ;;
    *) name=$1; shift ;;
  esac
done
gid=$(awk -F: -v name="$group_name" '$1 == name { print $3 }' "$FAKE_ACCOUNT_DB/group")
test -n "$uid" && test -n "$gid" && test -n "$home" && test -n "$shell" && test -n "$name"
printf '%s:x:%s:%s::%s:%s\\n' "$name" "$uid" "$gid" "$home" "$shell" > "$FAKE_ACCOUNT_DB/passwd"
""",
    )
    _write_executable(
        fake_bin / "userdel",
        """#!/bin/sh
printf 'userdel %s\\n' "$*" >> "$FAKE_ACCOUNT_LOG"
test "$1" = opensandbox-gateway
: > "$FAKE_ACCOUNT_DB/passwd"
""",
    )
    _write_executable(
        fake_bin / "groupdel",
        """#!/bin/sh
printf 'groupdel %s\\n' "$*" >> "$FAKE_ACCOUNT_LOG"
test "$1" = opensandbox-gateway
: > "$FAKE_ACCOUNT_DB/group"
""",
    )
    environment = {
        "PATH": str(fake_bin) + os.pathsep + os.environ["PATH"],
        "FAKE_ACCOUNT_DB": str(database),
        "FAKE_ACCOUNT_LOG": str(command_log),
    }
    return environment, database, command_log


def _snapshot_path(tmp_path: Path) -> Path:
    snapshot = tmp_path / "snapshot"
    snapshot.mkdir()
    return snapshot


@pytest.mark.parametrize(
    "value",
    ["", "0", "-1", "+42", "01", " 42", "42 ", "4 2", "abc", "4294967295", "99999999999999999999"],
)
def test_service_uid_rejects_missing_root_and_noncanonical_values(tmp_path: Path, value: str) -> None:
    result = _run_function(
        tmp_path,
        INSTALLER,
        "install_main",
        'is_service_uid "$CANDIDATE_UID"',
        env={"CANDIDATE_UID": value},
    )

    assert result.returncode != 0


@pytest.mark.parametrize("value", ["1", "999", "4294967294"])
def test_service_uid_accepts_canonical_non_root_linux_range(tmp_path: Path, value: str) -> None:
    result = _run_function(
        tmp_path,
        INSTALLER,
        "install_main",
        'is_service_uid "$CANDIDATE_UID"',
        env={"CANDIDATE_UID": value},
    )

    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    "contents",
    [
        "OTHER=value\n",
        "OPENSANDBOX_GATEWAY_ALLOWED_UID=1234\nOPENSANDBOX_GATEWAY_ALLOWED_UID=1234\n",
        "OPENSANDBOX_GATEWAY_ALLOWED_UID=1235\n",
        "OPENSANDBOX_GATEWAY_ALLOWED_UID=01234\n",
        "export OPENSANDBOX_GATEWAY_ALLOWED_UID=1234\n",
        " OPENSANDBOX_GATEWAY_ALLOWED_UID=1234\n",
        "OPENSANDBOX_GATEWAY_ALLOWED_UID =1234\n",
    ],
)
def test_gateway_env_rejects_missing_duplicate_mismatch_and_malformed_uid(
    tmp_path: Path,
    contents: str,
) -> None:
    gateway_env = tmp_path / "gateway.env"
    gateway_env.write_text(contents, encoding="utf-8", newline="\n")
    result = _run_function(
        tmp_path,
        INSTALLER,
        "install_main",
        'require_gateway_config_uid "$GATEWAY_ENV" 1234',
        env={"GATEWAY_ENV": str(gateway_env)},
    )

    assert result.returncode != 0


def test_gateway_env_accepts_one_exact_uid_without_evaluating_file(tmp_path: Path) -> None:
    marker = tmp_path / "must-not-exist"
    gateway_env = tmp_path / "gateway.env"
    gateway_env.write_text(
        "OPENSANDBOX_GATEWAY_ALLOWED_UID=1234\n"
        f"UNRELATED=$(touch {marker})\n",
        encoding="utf-8",
        newline="\n",
    )
    result = _run_function(
        tmp_path,
        INSTALLER,
        "install_main",
        'require_gateway_config_uid "$GATEWAY_ENV" 1234',
        env={"GATEWAY_ENV": str(gateway_env)},
    )

    assert result.returncode == 0, result.stderr
    assert not marker.exists()


def test_target_uid_owned_by_another_principal_is_rejected(tmp_path: Path) -> None:
    env, _, _ = _fake_accounts(
        tmp_path,
        passwd="other:x:1234:2000::/home/other:/bin/sh\n",
    )
    result = _run_function(
        tmp_path,
        INSTALLER,
        "install_main",
        "preflight_gateway_account_contract 1234",
        env=env,
    )

    assert result.returncode != 0


@pytest.mark.parametrize(
    ("passwd", "group"),
    [
        ("", "other:x:1234:\n"),
        ("other:x:2000:1234::/home/other:/bin/sh\n", "opensandbox-gateway:x:1234:\n"),
    ],
)
def test_target_gid_owned_or_used_by_another_principal_is_rejected(
    tmp_path: Path,
    passwd: str,
    group: str,
) -> None:
    env, _, _ = _fake_accounts(tmp_path, passwd=passwd, group=group)
    result = _run_function(
        tmp_path,
        INSTALLER,
        "install_main",
        "preflight_gateway_account_contract 1234",
        env=env,
    )

    assert result.returncode != 0


def test_account_lookup_backend_error_fails_closed(tmp_path: Path) -> None:
    env, database, _ = _fake_accounts(tmp_path)
    (database / "group").unlink()
    result = _run_function(
        tmp_path,
        INSTALLER,
        "install_main",
        "preflight_gateway_account_contract 1234",
        env=env,
    )

    assert result.returncode != 0


@pytest.mark.parametrize(
    ("passwd", "group"),
    [
        (
            "opensandbox-gateway:x:1235:1234::/nonexistent:/usr/sbin/nologin\n",
            "opensandbox-gateway:x:1234:\n",
        ),
        (
            "opensandbox-gateway:x:1234:1235::/nonexistent:/usr/sbin/nologin\n",
            "opensandbox-gateway:x:1235:\n",
        ),
        (
            "opensandbox-gateway:x:1234:1234::/srv/gateway:/usr/sbin/nologin\n",
            "opensandbox-gateway:x:1234:\n",
        ),
        (
            "opensandbox-gateway:x:1234:1234::/nonexistent:/bin/sh\n",
            "opensandbox-gateway:x:1234:\n",
        ),
        (
            "opensandbox-gateway:x:1234:1234::/nonexistent:/usr/sbin/nologin\n",
            "opensandbox-gateway:x:1234:other\n",
        ),
    ],
)
def test_existing_gateway_user_or_group_identity_drift_is_rejected(
    tmp_path: Path,
    passwd: str,
    group: str,
) -> None:
    env, _, _ = _fake_accounts(tmp_path, passwd=passwd, group=group)
    result = _run_function(
        tmp_path,
        INSTALLER,
        "install_main",
        "preflight_gateway_account_contract 1234",
        env=env,
    )

    assert result.returncode != 0


def test_account_creation_uses_exact_uid_and_deterministic_gid(tmp_path: Path) -> None:
    env, database, command_log = _fake_accounts(tmp_path)
    snapshot = _snapshot_path(tmp_path)
    result = _run_function(
        tmp_path,
        INSTALLER,
        "install_main",
        'snapshot_gateway_account_state "$SNAPSHOT" 1234\n'
        'ensure_gateway_account "$SNAPSHOT" 1234',
        env={**env, "SNAPSHOT": str(snapshot)},
    )

    assert result.returncode == 0, result.stderr
    assert command_log.read_text(encoding="utf-8").splitlines() == [
        "groupadd --system --gid 1234 opensandbox-gateway",
        "useradd --system --uid 1234 --gid opensandbox-gateway --home-dir /nonexistent --shell /usr/sbin/nologin --no-create-home --comment  opensandbox-gateway",
    ]
    assert (database / "passwd").read_text(encoding="utf-8") == (
        "opensandbox-gateway:x:1234:1234::/nonexistent:/usr/sbin/nologin\n"
    )
    assert (database / "group").read_text(encoding="utf-8") == "opensandbox-gateway:x:1234:\n"
    assert (snapshot / "gateway-user.created").is_file()
    assert (snapshot / "gateway-group.created").is_file()


def test_failed_install_restore_deletes_only_accounts_created_by_that_install(tmp_path: Path) -> None:
    env, database, command_log = _fake_accounts(tmp_path)
    snapshot = _snapshot_path(tmp_path)
    result = _run_function(
        tmp_path,
        INSTALLER,
        "install_main",
        'snapshot_gateway_account_state "$SNAPSHOT" 1234\n'
        'ensure_gateway_account "$SNAPSHOT" 1234\n'
        'preflight_gateway_account_restore "$SNAPSHOT"\n'
        'restore_gateway_account_state "$SNAPSHOT"',
        env={**env, "SNAPSHOT": str(snapshot)},
    )

    assert result.returncode == 0, result.stderr
    assert (database / "passwd").read_text(encoding="utf-8") == ""
    assert (database / "group").read_text(encoding="utf-8") == ""
    assert command_log.read_text(encoding="utf-8").splitlines()[-2:] == [
        "userdel opensandbox-gateway",
        "groupdel opensandbox-gateway",
    ]


def test_user_creation_failure_removes_only_the_group_created_before_it(tmp_path: Path) -> None:
    env, database, command_log = _fake_accounts(tmp_path)
    snapshot = _snapshot_path(tmp_path)
    result = _run_function(
        tmp_path,
        INSTALLER,
        "install_main",
        'snapshot_gateway_account_state "$SNAPSHOT" 1234\n'
        'if ensure_gateway_account "$SNAPSHOT" 1234; then exit 99; fi\n'
        'preflight_gateway_account_restore "$SNAPSHOT"\n'
        'restore_gateway_account_state "$SNAPSHOT"',
        env={**env, "SNAPSHOT": str(snapshot), "FAKE_USERADD_EXIT": "19"},
    )

    assert result.returncode == 0, result.stderr
    assert (database / "passwd").read_text(encoding="utf-8") == ""
    assert (database / "group").read_text(encoding="utf-8") == ""
    assert command_log.read_text(encoding="utf-8").splitlines() == [
        "groupadd --system --gid 1234 opensandbox-gateway",
        "useradd --system --uid 1234 --gid opensandbox-gateway --home-dir /nonexistent --shell /usr/sbin/nologin --no-create-home --comment  opensandbox-gateway",
        "groupdel opensandbox-gateway",
    ]


@pytest.mark.parametrize(("source", "entrypoint"), [(INSTALLER, "install_main"), (ROLLBACK, "rollback_main")])
def test_preexisting_accounts_are_preserved_by_failure_and_standalone_rollback(
    tmp_path: Path,
    source: Path,
    entrypoint: str,
) -> None:
    passwd = "opensandbox-gateway:x:1234:1234::/nonexistent:/usr/sbin/nologin\n"
    group = "opensandbox-gateway:x:1234:\n"
    env, database, command_log = _fake_accounts(tmp_path, passwd=passwd, group=group)
    snapshot = _snapshot_path(tmp_path)
    snapshot_command = (
        'snapshot_gateway_account_state "$SNAPSHOT" 1234\n'
        if source == INSTALLER
        else 'test -f "$SNAPSHOT/gateway-service-uid"\n'
    )
    if source == ROLLBACK:
        (snapshot / "gateway-service-uid").write_text("1234\n", encoding="utf-8")
        (snapshot / "gateway-user.present").touch()
        (snapshot / "gateway-user.entry").write_text(passwd, encoding="utf-8")
        (snapshot / "gateway-group.present").touch()
        (snapshot / "gateway-group.entry").write_text(group, encoding="utf-8")
    result = _run_function(
        tmp_path,
        source,
        entrypoint,
        snapshot_command
        + 'preflight_gateway_account_restore "$SNAPSHOT"\n'
        + 'restore_gateway_account_state "$SNAPSHOT"',
        env={**env, "SNAPSHOT": str(snapshot)},
    )

    assert result.returncode == 0, result.stderr
    assert (database / "passwd").read_text(encoding="utf-8") == passwd
    assert (database / "group").read_text(encoding="utf-8") == group
    assert not command_log.exists() or "del " not in command_log.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "drift",
    ["ambiguous-marker", "missing-created-marker", "uid-reused", "identity-drift"],
)
def test_standalone_rollback_fails_closed_on_snapshot_marker_or_identity_drift(
    tmp_path: Path,
    drift: str,
) -> None:
    snapshot = _snapshot_path(tmp_path)
    (snapshot / "gateway-service-uid").write_text("1234\n", encoding="utf-8")
    (snapshot / "gateway-user.absent").touch()
    (snapshot / "gateway-group.absent").touch()
    passwd = "opensandbox-gateway:x:1234:1234::/nonexistent:/usr/sbin/nologin\n"
    group = "opensandbox-gateway:x:1234:\n"
    if drift == "ambiguous-marker":
        (snapshot / "gateway-user.present").touch()
    elif drift == "missing-created-marker":
        pass
    elif drift == "uid-reused":
        passwd = "other:x:1234:2000::/home/other:/bin/sh\n"
    else:
        (snapshot / "gateway-user.created").write_text(passwd, encoding="utf-8")
        (snapshot / "gateway-group.created").write_text(group, encoding="utf-8")
        passwd = "opensandbox-gateway:x:1234:1234::/srv/drift:/usr/sbin/nologin\n"
    env, _, command_log = _fake_accounts(tmp_path, passwd=passwd, group=group)
    result = _run_function(
        tmp_path,
        ROLLBACK,
        "rollback_main",
        'preflight_gateway_account_restore "$SNAPSHOT"',
        env={**env, "SNAPSHOT": str(snapshot)},
    )

    assert result.returncode != 0
    assert not command_log.exists()


def test_installer_validates_uid_and_config_before_first_mutation() -> None:
    script = INSTALLER.read_text(encoding="utf-8")

    assert "OPENSANDBOX_GATEWAY_SERVICE_UID" in script
    assert script.index('is_service_uid "$SERVICE_UID"') < script.index(
        'install -d -o root -g root -m 0755 /opt/opensandbox-gateway'
    )
    assert 'require_gateway_config_uid "$CONFIG_DIR/gateway.env" "$gateway_uid"' in script
    assert script.index('require_gateway_config_contract "$SERVICE_UID"') < script.index(
        'install -d -o root -g root -m 0755 /opt/opensandbox-gateway'
    )
    assert ". \"$CONFIG_DIR/gateway.env\"" not in script
    assert "source \"$CONFIG_DIR/gateway.env\"" not in script
    assert "eval" not in script
    cleanup = script[script.index("cleanup_install()") : script.index("install_main()")]
    assert cleanup.index('write_manifest "$RESTORE_FROM"') < cleanup.index('restore_snapshot "$RESTORE_FROM"')


def test_legacy_unit_config_acl_authority_and_pointer_rollback_contracts_remain() -> None:
    installer = INSTALLER.read_text(encoding="utf-8")
    rollback = ROLLBACK.read_text(encoding="utf-8")

    assert 'install -o root -g root -m 0644 "$snapshot/$unit" "$SYSTEMD_DIR/$unit"' in installer
    assert 'setfacl --restore="$snapshot/workspaces.acl"' in installer
    assert 'install -o root -g root -m 0600 "$snapshot/authority-sha" "$AUTHORITY_SHA_STATE"' in installer
    assert 'install -o root -g root -m 0644 "$SNAPSHOT/$unit" "$SYSTEMD_DIR/$unit"' in rollback
    assert 'setfacl --restore="$SNAPSHOT/workspaces.acl"' in rollback
    assert 'install -o root -g root -m 0600 "$SNAPSHOT/authority-sha" "$AUTHORITY_SHA_STATE"' in rollback
    for text in (installer, rollback):
        assert 'rm -f "$AUTHORITY_SHA_STATE" "$AUTHORITY_EVIDENCE_STATE"' in text
    assert 'mv -Tf "$CURRENT_LINK.restore" "$CURRENT_LINK"' in installer
    assert 'mv -Tf "$CURRENT_LINK.next" "$CURRENT_LINK"' in rollback
    restore_start = rollback.index("PREVIOUS=")
    assert rollback.index('preflight_gateway_account_restore "$SNAPSHOT"') < rollback.index(
        'for unit in opensandbox-gateway.service opensandbox-gateway-helper.service; do', restore_start
    )
    assert rollback.index('restore_gateway_account_state "$SNAPSHOT"') > rollback.index(
        'mv -Tf "$CURRENT_LINK.next" "$CURRENT_LINK"'
    )
