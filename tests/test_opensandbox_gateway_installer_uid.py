from __future__ import annotations

import hashlib
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
    if source == ROLLBACK:
        environment["SCRIPT"] = source.as_posix()
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
    systemd_log = tmp_path / "systemd-commands.log"
    systemd_state = tmp_path / "systemd-state"
    systemd_state.mkdir()
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
    _write_executable(
        fake_bin / "systemctl",
        """#!/bin/sh
action=$1
shift
unit=${1-}
printf '%s %s\\n' "$action" "$*" >> "$FAKE_SYSTEMD_LOG"
case "$action" in
  stop)
    if test "${FAKE_SYSTEMCTL_STOP_LEAVES_ACTIVE:-0}" -ne 1; then
      printf 'inactive\\n' > "$FAKE_SYSTEMD_STATE/$unit.active"
    fi
    exit "${FAKE_SYSTEMCTL_STOP_EXIT:-0}"
    ;;
  show)
    test "${3-}" = ActiveState || exit 2
    test -f "$FAKE_SYSTEMD_STATE/$unit.active" && cat "$FAKE_SYSTEMD_STATE/$unit.active" || printf 'inactive\\n'
    ;;
  restart)
    printf 'active\\n' > "$FAKE_SYSTEMD_STATE/$unit.active"
    ;;
  enable)
    printf 'enabled\\n' > "$FAKE_SYSTEMD_STATE/$unit.enabled"
    ;;
  disable)
    printf 'disabled\\n' > "$FAKE_SYSTEMD_STATE/$unit.enabled"
    ;;
  is-enabled)
    state=not-found
    test -f "$FAKE_SYSTEMD_STATE/$unit.enabled" && state=$(cat "$FAKE_SYSTEMD_STATE/$unit.enabled")
    printf '%s\\n' "$state"
    test "$state" = enabled
    ;;
  daemon-reload) : ;;
  *) exit 2 ;;
esac
""",
    )
    _write_executable(
        fake_bin / "ps",
        """#!/bin/sh
test "$*" = '-eo uid=,pid=' || exit 2
test -z "${FAKE_PS_OUTPUT:-}" || printf '%s\\n' "$FAKE_PS_OUTPUT"
""",
    )
    environment = {
        "PATH": str(fake_bin) + os.pathsep + os.environ["PATH"],
        "FAKE_ACCOUNT_DB": str(database),
        "FAKE_ACCOUNT_LOG": str(command_log),
        "FAKE_SYSTEMD_LOG": str(systemd_log),
        "FAKE_SYSTEMD_STATE": str(systemd_state),
    }
    return environment, database, command_log


def _snapshot_path(tmp_path: Path) -> Path:
    snapshot = tmp_path / "snapshot"
    snapshot.mkdir()
    return snapshot


def _seal_manifest(root: Path) -> None:
    lines: list[str] = []
    for path in sorted(
        item
        for item in root.rglob("*")
        if item.is_file() and item != root / "MANIFEST.sha256"
    ):
        relative = path.relative_to(root).as_posix()
        lines.append(f"{hashlib.sha256(path.read_bytes()).hexdigest()}  ./{relative}\n")
    (root / "MANIFEST.sha256").write_text(
        "".join(lines), encoding="ascii", newline="\n"
    )


def _create_runtime_config(root: Path, uid: str = "1234") -> None:
    (root / "secrets").mkdir(parents=True)
    (root / "tls").mkdir()
    (root / "gateway.env").write_text(
        f"OPENSANDBOX_GATEWAY_ALLOWED_UID={uid}\n"
        "OPENSANDBOX_GATEWAY_UPSTREAM_CA_FILE=/etc/opensandbox-gateway/tls/upstream-ca.pem\n",
        encoding="utf-8",
        newline="\n",
    )
    (root / "egress-policy.v1.json").write_text("{}\n", encoding="utf-8", newline="\n")
    for name in ("fullchain.pem", "upstream-ca.pem", "privkey.pem"):
        (root / "tls" / name).write_text(f"{name}\n", encoding="utf-8", newline="\n")
    for name in ("lifecycle-api-key", "capability-token", "record-signing-key"):
        (root / "secrets" / name).write_text(
            f"{name}\n", encoding="utf-8", newline="\n"
        )


@pytest.mark.parametrize(
    "value",
    [
        "",
        "0",
        "-1",
        "+42",
        "01",
        " 42",
        "42 ",
        "4 2",
        "abc",
        "4294967295",
        "99999999999999999999",
    ],
)
def test_service_uid_rejects_missing_root_and_noncanonical_values(
    tmp_path: Path, value: str
) -> None:
    result = _run_function(
        tmp_path,
        INSTALLER,
        "install_main",
        'is_service_uid "$CANDIDATE_UID"',
        env={"CANDIDATE_UID": value},
    )

    assert result.returncode != 0


@pytest.mark.parametrize("value", ["1", "999", "4294967294"])
def test_service_uid_accepts_canonical_non_root_linux_range(
    tmp_path: Path, value: str
) -> None:
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


def test_gateway_env_accepts_one_exact_uid_without_evaluating_file(
    tmp_path: Path,
) -> None:
    marker = tmp_path / "must-not-exist"
    gateway_env = tmp_path / "gateway.env"
    gateway_env.write_text(
        f"OPENSANDBOX_GATEWAY_ALLOWED_UID=1234\nUNRELATED=$(touch {marker})\n",
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
        'ps() { test "$*" = "-eo uid=,pid="; test -z "${FAKE_PS_OUTPUT:-}" || printf "%s\\n" "$FAKE_PS_OUTPUT"; }\n'
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
    assert (database / "group").read_text(
        encoding="utf-8"
    ) == "opensandbox-gateway:x:1234:\n"
    assert (snapshot / "gateway-user.created").is_file()
    assert (snapshot / "gateway-group.created").is_file()


def test_failed_install_restore_deletes_only_accounts_created_by_that_install(
    tmp_path: Path,
) -> None:
    env, database, command_log = _fake_accounts(tmp_path)
    snapshot = _snapshot_path(tmp_path)
    result = _run_function(
        tmp_path,
        INSTALLER,
        "install_main",
        'ps() { test "$*" = "-eo uid=,pid="; test -z "${FAKE_PS_OUTPUT:-}" || printf "%s\\n" "$FAKE_PS_OUTPUT"; }\n'
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


def test_user_creation_failure_removes_only_the_group_created_before_it(
    tmp_path: Path,
) -> None:
    env, database, command_log = _fake_accounts(tmp_path)
    snapshot = _snapshot_path(tmp_path)
    result = _run_function(
        tmp_path,
        INSTALLER,
        "install_main",
        'ps() { test "$*" = "-eo uid=,pid="; test -z "${FAKE_PS_OUTPUT:-}" || printf "%s\\n" "$FAKE_PS_OUTPUT"; }\n'
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


@pytest.mark.parametrize(
    ("source", "entrypoint"), [(INSTALLER, "install_main"), (ROLLBACK, "rollback_main")]
)
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
    assert not command_log.exists() or "del " not in command_log.read_text(
        encoding="utf-8"
    )


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


@pytest.mark.parametrize(
    ("source", "entrypoint"), [(INSTALLER, "install_main"), (ROLLBACK, "rollback_main")]
)
def test_manifest_is_closed_world_and_unlisted_created_marker_cannot_authorize_deletion(
    tmp_path: Path,
    source: Path,
    entrypoint: str,
) -> None:
    snapshot = _snapshot_path(tmp_path)
    (snapshot / "gateway-service-uid").write_text(
        "1234\n", encoding="ascii", newline="\n"
    )
    _seal_manifest(snapshot)
    accepted = _run_function(
        tmp_path,
        source,
        entrypoint,
        'SNAPSHOT=$(cygpath -u "$SNAPSHOT"); verify_manifest "$SNAPSHOT"',
        env={"SNAPSHOT": str(snapshot)},
    )
    assert accepted.returncode == 0, accepted.stderr

    (snapshot / "gateway-user.created").write_text(
        "opensandbox-gateway:x:1234:1234::/nonexistent:/usr/sbin/nologin\n",
        encoding="ascii",
        newline="\n",
    )
    rejected = _run_function(
        tmp_path,
        source,
        entrypoint,
        'SNAPSHOT=$(cygpath -u "$SNAPSHOT"); verify_manifest "$SNAPSHOT"',
        env={"SNAPSHOT": str(snapshot)},
    )
    assert rejected.returncode != 0


@pytest.mark.parametrize(
    ("source", "entrypoint"), [(INSTALLER, "install_main"), (ROLLBACK, "rollback_main")]
)
def test_manifest_rejects_duplicate_or_malformed_entries(
    tmp_path: Path,
    source: Path,
    entrypoint: str,
) -> None:
    snapshot = _snapshot_path(tmp_path)
    (snapshot / "marker").write_text("sealed\n", encoding="ascii", newline="\n")
    _seal_manifest(snapshot)
    manifest = snapshot / "MANIFEST.sha256"
    line = manifest.read_text(encoding="ascii")
    manifest.write_text(line + line, encoding="ascii", newline="\n")
    duplicate = _run_function(
        tmp_path,
        source,
        entrypoint,
        'SNAPSHOT=$(cygpath -u "$SNAPSHOT"); verify_manifest "$SNAPSHOT"',
        env={"SNAPSHOT": str(snapshot)},
    )
    assert duplicate.returncode != 0

    manifest.write_text(
        line.replace("  ./marker", " *../marker"), encoding="ascii", newline="\n"
    )
    malformed = _run_function(
        tmp_path,
        source,
        entrypoint,
        'SNAPSHOT=$(cygpath -u "$SNAPSHOT"); verify_manifest "$SNAPSHOT"',
        env={"SNAPSHOT": str(snapshot)},
    )
    assert malformed.returncode != 0


@pytest.mark.parametrize("drift", [False, True])
def test_config_snapshot_metadata_verifies_exact_numeric_identity_and_mode(
    tmp_path: Path, drift: bool
) -> None:
    source_tree = tmp_path / "source-config"
    restored_tree = tmp_path / "restored-config"
    source_tree.mkdir()
    restored_tree.mkdir()
    (source_tree / "gateway.env").write_text("source\n", encoding="ascii", newline="\n")
    (restored_tree / "gateway.env").write_text(
        "source\n", encoding="ascii", newline="\n"
    )
    metadata = tmp_path / "config.metadata"
    result = _run_function(
        tmp_path,
        INSTALLER,
        "install_main",
        r"""
        require_root_tree() { :; }
        chown() { :; }
        chmod() { :; }
        stat() {
          format=$2; target=$3
          case "$format" in
            %u:%g:%a)
              case "$target" in
                *config.metadata) printf '0:0:400\n' ;;
                *)
                  mode=640
                  test -d "$target" && mode=750
                  case "$(pwd -P):$target:${DRIFT_CONFIG_MODE:-0}" in
                    *restored-config*:./gateway.env:1) mode=600 ;;
                  esac
                  printf '0:1234:%s\n' "$mode"
                  ;;
              esac
              ;;
            *) command stat "$@" ;;
          esac
        }
        SOURCE_TREE=$(cygpath -u "$SOURCE_TREE")
        RESTORED_TREE=$(cygpath -u "$RESTORED_TREE")
        METADATA=$(cygpath -u "$METADATA")
        write_config_metadata "$SOURCE_TREE" "$METADATA"
        verify_config_metadata "$RESTORED_TREE" "$METADATA"
        """,
        env={
            "SOURCE_TREE": str(source_tree),
            "RESTORED_TREE": str(restored_tree),
            "METADATA": str(metadata),
            "DRIFT_CONFIG_MODE": "1" if drift else "0",
        },
    )
    assert (result.returncode != 0) is drift, result.stderr


@pytest.mark.parametrize("drift", [False, True])
def test_rollback_config_metadata_verification_is_symmetric(
    tmp_path: Path, drift: bool
) -> None:
    restored_tree = tmp_path / "restored-config"
    restored_tree.mkdir()
    (restored_tree / "gateway.env").write_text(
        "source\n", encoding="ascii", newline="\n"
    )
    metadata = tmp_path / "config.metadata"
    metadata.write_text(
        ".\td\t0:1234:750\n./gateway.env\tf\t0:1234:640\n",
        encoding="ascii",
        newline="\n",
    )
    result = _run_function(
        tmp_path,
        ROLLBACK,
        "rollback_main",
        r"""
        require_root_tree() { :; }
        stat() {
          format=$2; target=$3
          case "$format" in
            %u:%g:%a)
              case "$target" in
                *config.metadata) printf '0:0:400\n' ;;
                .) printf '0:1234:750\n' ;;
                ./gateway.env)
                  if test "${DRIFT_CONFIG_MODE:-0}" -eq 1; then
                    printf '0:1234:600\n'
                  else
                    printf '0:1234:640\n'
                  fi
                  ;;
                *) return 1 ;;
              esac
              ;;
            *) command stat "$@" ;;
          esac
        }
        RESTORED_TREE=$(cygpath -u "$RESTORED_TREE")
        METADATA=$(cygpath -u "$METADATA")
        verify_config_metadata "$RESTORED_TREE" "$METADATA"
        """,
        env={
            "RESTORED_TREE": str(restored_tree),
            "METADATA": str(metadata),
            "DRIFT_CONFIG_MODE": "1" if drift else "0",
        },
    )
    assert (result.returncode != 0) is drift, result.stderr


@pytest.mark.parametrize(
    ("source", "entrypoint"), [(INSTALLER, "install_main"), (ROLLBACK, "rollback_main")]
)
@pytest.mark.parametrize("config_gid", ["1234", "0"])
def test_service_restart_requires_readable_config_and_verifies_both_unit_states(
    tmp_path: Path,
    source: Path,
    entrypoint: str,
    config_gid: str,
) -> None:
    passwd = "opensandbox-gateway:x:1234:1234::/nonexistent:/usr/sbin/nologin\n"
    group = "opensandbox-gateway:x:1234:\n"
    env, _, _ = _fake_accounts(tmp_path, passwd=passwd, group=group)
    config = tmp_path / "config"
    _create_runtime_config(config)
    snapshot = _snapshot_path(tmp_path)
    for unit in ("opensandbox-gateway.service", "opensandbox-gateway-helper.service"):
        (snapshot / f"{unit}.present").touch()
        (snapshot / f"{unit}.active").touch()
        (snapshot / f"{unit}.enabled").touch()
    result = _run_function(
        tmp_path,
        source,
        entrypoint,
        r"""
        require_root_tree() { :; }
        stat() {
          format=$2; target=$3
          case "$format" in
            %u) printf '0\n' ;;
            %G) printf 'opensandbox-gateway\n' ;;
            %g) printf '%s\n' "$FAKE_CONFIG_GID" ;;
            %a)
              if test -d "$target"; then
                printf '750\n'
              else
                case "$target" in */privkey.pem|*/secrets/*) printf '440\n' ;; *) printf '640\n' ;; esac
              fi
              ;;
            *) command stat "$@" ;;
          esac
        }
        CONFIG_DIR=$(cygpath -u "$TEST_CONFIG_DIR")
        SNAPSHOT=$(cygpath -u "$SNAPSHOT")
        apply_snapshot_unit_states "$SNAPSHOT" 1234
        """,
        env={
            **env,
            "TEST_CONFIG_DIR": str(config),
            "SNAPSHOT": str(snapshot),
            "FAKE_CONFIG_GID": config_gid,
        },
    )
    systemd_log = (tmp_path / "systemd-commands.log").read_text(encoding="utf-8")
    if config_gid == "1234":
        assert result.returncode == 0, result.stderr
        assert systemd_log.count("restart ") == 2
        assert systemd_log.count("show ") == 2
        assert systemd_log.count("is-enabled ") == 2
    else:
        assert result.returncode != 0
        assert "restart " not in systemd_log


@pytest.mark.parametrize(
    ("source", "entrypoint"), [(INSTALLER, "install_main"), (ROLLBACK, "rollback_main")]
)
@pytest.mark.parametrize(
    "hostile",
    ["stop-active", "uid-process", "group-member", "primary-gid", "home", "runtime"],
)
def test_account_deletion_hostile_state_fails_before_userdel_or_groupdel(
    tmp_path: Path,
    source: Path,
    entrypoint: str,
    hostile: str,
) -> None:
    bad_home = tmp_path / "must-not-be-service-home"
    bad_runtime = tmp_path / "unsafe-runtime"
    bad_home.mkdir()
    bad_runtime.mkdir()
    home_value = "/nonexistent"
    if hostile == "home":
        home_value = "/" + bad_home.as_posix().replace(":", "", 1).lstrip("/")
    passwd = f"opensandbox-gateway:x:1234:1234::{home_value}:/usr/sbin/nologin\n"
    if hostile == "primary-gid":
        passwd += "other:x:2000:1234::/home/other:/bin/sh\n"
    group = "opensandbox-gateway:x:1234:\n"
    if hostile == "group-member":
        group = "opensandbox-gateway:x:1234:other\n"
    env, _, command_log = _fake_accounts(tmp_path, passwd=passwd, group=group)
    snapshot = _snapshot_path(tmp_path)
    (snapshot / "gateway-service-uid").write_text(
        "1234\n", encoding="ascii", newline="\n"
    )
    (snapshot / "gateway-user.absent").touch()
    (snapshot / "gateway-user.created").write_text(
        passwd.splitlines(keepends=True)[0], encoding="ascii", newline="\n"
    )
    (snapshot / "gateway-group.absent").touch()
    (snapshot / "gateway-group.created").write_text(
        "opensandbox-gateway:x:1234:\n", encoding="ascii", newline="\n"
    )
    if hostile == "stop-active":
        for unit in (
            "opensandbox-gateway.service",
            "opensandbox-gateway-helper.service",
        ):
            (tmp_path / "systemd-state" / f"{unit}.active").write_text(
                "active\n", encoding="ascii"
            )
        env.update(
            {"FAKE_SYSTEMCTL_STOP_EXIT": "1", "FAKE_SYSTEMCTL_STOP_LEAVES_ACTIVE": "1"}
        )
    if hostile == "uid-process":
        env["FAKE_PS_OUTPUT"] = "1234 7788"
    result = _run_function(
        tmp_path,
        source,
        entrypoint,
        r"""
        ps() { test "$*" = "-eo uid=,pid="; test -z "${FAKE_PS_OUTPUT:-}" || printf '%s\n' "$FAKE_PS_OUTPUT"; }
        SNAPSHOT=$(cygpath -u "$SNAPSHOT")
        test "$HOSTILE" != home || SERVICE_HOME=$BAD_HOME
        test "$HOSTILE" != runtime || RUNTIME_STATE=$(cygpath -u "$BAD_RUNTIME")
        restore_gateway_account_state "$SNAPSHOT"
        """,
        env={
            **env,
            "SNAPSHOT": str(snapshot),
            "HOSTILE": hostile,
            "BAD_HOME": home_value,
            "BAD_RUNTIME": str(bad_runtime),
        },
    )
    assert result.returncode != 0
    commands = command_log.read_text(encoding="utf-8") if command_log.exists() else ""
    assert "userdel " not in commands
    assert "groupdel " not in commands


def test_group_only_preexisting_identity_is_preserved_while_created_user_is_removed(
    tmp_path: Path,
) -> None:
    group = "opensandbox-gateway:x:1234:\n"
    env, database, command_log = _fake_accounts(tmp_path, group=group)
    snapshot = _snapshot_path(tmp_path)
    result = _run_function(
        tmp_path,
        INSTALLER,
        "install_main",
        r"""
        ps() { :; }
        snapshot_gateway_account_state "$SNAPSHOT" 1234
        ensure_gateway_account "$SNAPSHOT" 1234
        restore_gateway_account_state "$SNAPSHOT"
        """,
        env={**env, "SNAPSHOT": str(snapshot)},
    )
    assert result.returncode == 0, result.stderr
    assert (database / "passwd").read_text(encoding="utf-8") == ""
    assert (database / "group").read_text(encoding="utf-8") == group
    commands = command_log.read_text(encoding="utf-8").splitlines()
    assert commands[-1] == "userdel opensandbox-gateway"
    assert not any(command.startswith("groupdel ") for command in commands)


def test_user_only_preexisting_identity_is_rejected_without_mutation(
    tmp_path: Path,
) -> None:
    passwd = "opensandbox-gateway:x:1234:1234::/nonexistent:/usr/sbin/nologin\n"
    env, _, command_log = _fake_accounts(tmp_path, passwd=passwd)
    result = _run_function(
        tmp_path,
        INSTALLER,
        "install_main",
        "preflight_gateway_account_contract 1234",
        env=env,
    )
    assert result.returncode != 0
    assert not command_log.exists()


def test_stale_or_repeated_snapshot_is_rejected_by_exact_rollback_source_binding(
    tmp_path: Path,
) -> None:
    snapshot = _snapshot_path(tmp_path)
    (snapshot / "gateway-service-uid").write_text(
        "1234\n", encoding="ascii", newline="\n"
    )
    installed = "1" * 40
    previous = "2" * 40
    (snapshot / "rollback-from").write_text(
        f"{installed}\n", encoding="ascii", newline="\n"
    )
    exact = _run_function(
        tmp_path,
        ROLLBACK,
        "rollback_main",
        'require_snapshot_matches_current "$SNAPSHOT" "$CURRENT"',
        env={"SNAPSHOT": str(snapshot), "CURRENT": installed},
    )
    stale = _run_function(
        tmp_path,
        ROLLBACK,
        "rollback_main",
        'require_snapshot_matches_current "$SNAPSHOT" "$CURRENT"',
        env={"SNAPSHOT": str(snapshot), "CURRENT": previous},
    )
    assert exact.returncode == 0, exact.stderr
    assert stale.returncode != 0


def test_installer_validates_uid_and_config_before_first_mutation() -> None:
    script = INSTALLER.read_text(encoding="utf-8")

    assert "OPENSANDBOX_GATEWAY_SERVICE_UID" in script
    assert script.index('is_service_uid "$SERVICE_UID"') < script.index(
        "install -d -o root -g root -m 0755 /opt/opensandbox-gateway"
    )
    assert (
        'require_gateway_config_uid "$CONFIG_DIR/gateway.env" "$gateway_uid"' in script
    )
    assert script.index(
        'require_gateway_config_contract "$SERVICE_UID"'
    ) < script.index("install -d -o root -g root -m 0755 /opt/opensandbox-gateway")
    assert '. "$CONFIG_DIR/gateway.env"' not in script
    assert 'source "$CONFIG_DIR/gateway.env"' not in script
    assert "eval" not in script
    cleanup = script[script.index("cleanup_install()") : script.index("install_main()")]
    assert cleanup.index('write_manifest "$RESTORE_FROM"') < cleanup.index(
        'restore_snapshot "$RESTORE_FROM"'
    )


def test_shared_engine_keeps_unit_config_acl_authority_and_pointer_rollback_contracts() -> (
    None
):
    installer = INSTALLER.read_text(encoding="utf-8")
    rollback = ROLLBACK.read_text(encoding="utf-8")
    rollback_start = installer.index("rollback_main()")
    rollback_body = installer[rollback_start : installer.index("install_main()")]

    assert (
        'install -o root -g root -m 0644 "$snapshot/$unit" "$SYSTEMD_DIR/$unit"'
        in installer
    )
    assert 'setfacl --restore="$snapshot/workspaces.acl"' in installer
    assert (
        'install -o root -g root -m 0600 "$snapshot/authority-sha" "$AUTHORITY_SHA_STATE"'
        in installer
    )
    assert '. "$INSTALLER_ENGINE"' in rollback
    assert 'rollback_main "$@"' in rollback
    assert (
        'install -o root -g root -m 0644 "$SNAPSHOT/$unit" "$SYSTEMD_DIR/$unit"'
        in rollback_body
    )
    assert 'setfacl --restore="$SNAPSHOT/workspaces.acl"' in rollback_body
    assert (
        'install -o root -g root -m 0600 "$SNAPSHOT/authority-sha" "$AUTHORITY_SHA_STATE"'
        in rollback_body
    )
    assert 'rm -f "$AUTHORITY_SHA_STATE" "$AUTHORITY_EVIDENCE_STATE"' in rollback_body
    assert 'mv -Tf "$CURRENT_LINK.restore" "$CURRENT_LINK"' in installer
    assert 'mv -Tf "$CURRENT_LINK.next" "$CURRENT_LINK"' in rollback_body
    restore_start = rollback_body.index("PREVIOUS=")
    assert rollback_body.index(
        'preflight_gateway_account_restore "$SNAPSHOT"'
    ) < rollback_body.index(
        "for unit in opensandbox-gateway.service opensandbox-gateway-helper.service; do",
        restore_start,
    )
    assert rollback_body.index(
        'restore_gateway_account_state "$SNAPSHOT"'
    ) < rollback_body.index('mv -Tf "$CURRENT_LINK.next" "$CURRENT_LINK"')
