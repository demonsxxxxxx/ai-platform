#!/bin/sh

S72_ATOMIC_RECOVERY_AUTHORITY_SCHEMA=s72-atomic-recovery-authority-v1
S72_ATOMIC_TRANSACTION_SCHEMA=s72-atomic-transaction-v1

s72_atomic_is_commit() {
  test "${#1}" -eq 40 || return 1
  case "$1" in *[!0-9a-f]*) return 1 ;; esac
}

s72_atomic_is_authority_evidence_id() {
  test -n "$1" && test "${#1}" -le 128 || return 1
  case "$1" in *[!A-Za-z0-9._:-]*) return 1 ;; esac
}

s72_atomic_is_transaction_id() {
  test "${#1}" -eq 32 || return 1
  case "$1" in *[!0-9a-f]*) return 1 ;; esac
}

s72_atomic_is_snapshot_id() {
  case "$1" in .rollback.*) token=${1#.rollback.} ;; *) return 1 ;; esac
  s72_atomic_is_transaction_id "$token"
}

s72_atomic_is_sha256() {
  test "${#1}" -eq 64 || return 1
  case "$1" in *[!0-9a-f]*) return 1 ;; esac
}

s72_atomic_is_service_uid() {
  case "$1" in ""|0|0[0-9]*|*[!0-9]*) return 1 ;; esac
  test "${#1}" -le 10 || return 1
  test "${#1}" -lt 10 || test "$1" -le 4294967294
}

s72_atomic_node_identity() {
  stat -c '%d:%i:%F:%u:%g:%a:%s:%Y:%Z' -- "$1"
}

s72_atomic_require_identity() {
  test "$(s72_atomic_node_identity "$1")" = "$2"
}

s72_atomic_directory_identity() {
  stat -c '%d:%i:%u:%g:%a' -- "$1"
}

s72_atomic_remove_empty_directory() {
  path=$1
  expected=$2
  test "$(s72_atomic_directory_identity "$path")" = "$expected" || return 1
  if test "${s72_loader_mode:-}" = test-source-eval || test "$(uname -s)" != Linux; then
    rmdir -- "$path" || return 1
  else
    python3 - "$path" "$expected" <<'PY'
import os
import stat
import sys

path, expected_text = sys.argv[1:]
identity = expected_text.split(":")
expected = (*map(int, identity[:4]), int(identity[4], 8))
parent = os.path.dirname(path)
name = os.path.basename(path)
flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
parent_fd = os.open(parent, flags)
directory_fd = -1
try:
    before = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    actual = (before.st_dev, before.st_ino, before.st_uid, before.st_gid, stat.S_IMODE(before.st_mode))
    if not stat.S_ISDIR(before.st_mode) or actual != expected:
        raise SystemExit(1)
    directory_fd = os.open(name, flags, dir_fd=parent_fd)
    opened = os.fstat(directory_fd)
    if (opened.st_dev, opened.st_ino) != actual[:2] or os.listdir(directory_fd):
        raise SystemExit(1)
    os.close(directory_fd)
    directory_fd = -1
    after = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    if (after.st_dev, after.st_ino, after.st_uid, after.st_gid, stat.S_IMODE(after.st_mode)) != expected:
        raise SystemExit(1)
    os.rmdir(name, dir_fd=parent_fd)
    os.fsync(parent_fd)
finally:
    if directory_fd >= 0:
        os.close(directory_fd)
    os.close(parent_fd)
PY
  fi
  test ! -e "$path" && test ! -L "$path"
}

s72_atomic_require_root_tree() {
  test -d "$1" && test ! -L "$1" || return 1
  test "$(stat -c %u "$1")" -eq 0 || return 1
  test -z "$(find "$1" -type l -print -quit)" || return 1
  test -z "$(find "$1" ! -type f ! -type d -print -quit)" || return 1
  test -z "$(find "$1" ! -user root -print -quit)"
}

s72_atomic_require_root_owned_regular() (
  path=$1
  mode=$2
  test -f "$path" && test ! -L "$path" || return 1
  test "$(stat -c %u "$path")" -eq 0 || return 1
  case "$(stat -c %G "$path")" in root|opensandbox-gateway) ;; *) return 1 ;; esac
  test "$(stat -c %a "$path")" = "$mode"
)

s72_atomic_require_root_owned_directory() (
  path=$1
  mode=$2
  test -d "$path" && test ! -L "$path" || return 1
  test "$(stat -c %u "$path")" -eq 0 || return 1
  case "$(stat -c %G "$path")" in root|opensandbox-gateway) ;; *) return 1 ;; esac
  test "$(stat -c %a "$path")" = "$mode"
)

s72_atomic_fsync_path() {
  python3 - "$1" <<'PY'
import os
import stat
import sys

path = sys.argv[1]
flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
if stat.S_ISDIR(os.lstat(path).st_mode):
    flags |= getattr(os, "O_DIRECTORY", 0)
fd = os.open(path, flags)
try:
    os.fsync(fd)
finally:
    os.close(fd)
PY
}

s72_atomic_fsync_tree() {
  python3 - "$1" <<'PY'
import os
import stat
import sys

root = os.path.abspath(sys.argv[1])
root_stat = os.lstat(root)
if not stat.S_ISDIR(root_stat.st_mode):
    raise SystemExit(1)
for current, dirs, files in os.walk(root, topdown=False, followlinks=False):
    for name in sorted(files):
        path = os.path.join(current, name)
        before = os.lstat(path)
        if not stat.S_ISREG(before.st_mode):
            raise SystemExit(1)
        fd = os.open(
            path,
            os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
        )
        try:
            opened = os.fstat(fd)
            if (opened.st_dev, opened.st_ino, opened.st_mode, opened.st_size) != (
                before.st_dev,
                before.st_ino,
                before.st_mode,
                before.st_size,
            ):
                raise SystemExit(1)
            os.fsync(fd)
        finally:
            os.close(fd)
    for name in sorted(dirs):
        path = os.path.join(current, name)
        before = os.lstat(path)
        if not stat.S_ISDIR(before.st_mode):
            raise SystemExit(1)
        fd = os.open(
            path,
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
        try:
            opened = os.fstat(fd)
            if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
                raise SystemExit(1)
            os.fsync(fd)
        finally:
            os.close(fd)
root_fd = os.open(
    root,
    os.O_RDONLY
    | getattr(os, "O_DIRECTORY", 0)
    | getattr(os, "O_CLOEXEC", 0)
    | getattr(os, "O_NOFOLLOW", 0),
)
try:
    opened = os.fstat(root_fd)
    if (opened.st_dev, opened.st_ino) != (root_stat.st_dev, root_stat.st_ino):
        raise SystemExit(1)
    os.fsync(root_fd)
finally:
    os.close(root_fd)
PY
}

s72_atomic_publish_new_file() {
  target=$1
  mode=$2
  contents=$3
  parent=${target%/*}
  name=${target##*/}
  case "$target" in
    /*) ;;
    [A-Za-z]:/*) test "$(uname -s)" != Linux || return 1 ;;
    *) return 1 ;;
  esac
  test "$parent" != "$target" && test -d "$parent" && test ! -L "$parent" || return 1
  test ! -e "$target" && test ! -L "$target" || return 1
  if test "${s72_loader_mode:-}" = test-source-eval || test "$(uname -s)" != Linux; then
    (set -C; umask 077; printf '%s\n' "$contents" > "$target") || return 1
    test "$(uname -s)" != Linux || chown root:root "$target" || return 1
    chmod "$mode" "$target" || return 1
    return 0
  fi
  printf '%s\n' "$contents" | python3 -c '
import ctypes
import os
import sys
parent, name, mode_text = sys.argv[1:]
payload = sys.stdin.buffer.read()
dir_fd = os.open(parent, os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_CLOEXEC", 0))
fd = -1
try:
    fd = os.open(".", os.O_TMPFILE | os.O_WRONLY | getattr(os, "O_CLOEXEC", 0), int(mode_text, 8), dir_fd=dir_fd)
    os.fchown(fd, 0, 0)
    os.fchmod(fd, int(mode_text, 8))
    view = memoryview(payload)
    while view:
        written = os.write(fd, view)
        view = view[written:]
    os.fsync(fd)
    libc = ctypes.CDLL(None, use_errno=True)
    libc.linkat.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_int]
    libc.linkat.restype = ctypes.c_int
    AT_EMPTY_PATH = 0x1000
    result = libc.linkat(fd, b"", dir_fd, os.fsencode(name), AT_EMPTY_PATH)
    if result != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error), name)
    os.fsync(dir_fd)
finally:
    if fd >= 0:
        os.close(fd)
    os.close(dir_fd)
' "$parent" "$name" "$mode"
}

s72_atomic_write_atomic_file() {
  target=$1
  mode=$2
  contents=$3
  parent=${target%/*}
  name=${target##*/}
  if test "${s72_loader_mode:-}" = test-source-eval || test "$(uname -s)" != Linux; then
    temporary=$parent/.$name.s72-$$
    test ! -e "$temporary" && test ! -L "$temporary" || return 1
    (umask 077; printf '%s\n' "$contents" > "$temporary") || return 1
    test "$(uname -s)" != Linux || chown root:root "$temporary" || return 1
    chmod "$mode" "$temporary" || return 1
    mv -f "$temporary" "$target" || return 1
    return 0
  fi
  printf '%s\n' "$contents" | python3 -c '
import os
import secrets
import sys
parent, name, mode_text = sys.argv[1:]
payload = sys.stdin.buffer.read()
dir_fd = os.open(parent, os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_CLOEXEC", 0))
temp_name = f".{name}.s72-{secrets.token_hex(16)}"
fd = -1
try:
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(temp_name, flags, int(mode_text, 8), dir_fd=dir_fd)
    os.fchown(fd, 0, 0)
    os.fchmod(fd, int(mode_text, 8))
    view = memoryview(payload)
    while view:
        written = os.write(fd, view)
        view = view[written:]
    os.fsync(fd)
    os.close(fd)
    fd = -1
    os.replace(temp_name, name, src_dir_fd=dir_fd, dst_dir_fd=dir_fd)
    os.fsync(dir_fd)
except BaseException:
    try:
        os.unlink(temp_name, dir_fd=dir_fd)
    except FileNotFoundError:
        pass
    raise
finally:
    if fd >= 0:
        os.close(fd)
    os.close(dir_fd)
' "$parent" "$name" "$mode"
}

s72_atomic_write_manifest() (
  target=$1
  test -d "$target" && test ! -L "$target" || return 1
  test -z "$(find "$target" -type l -print -quit)" || return 1
  test -z "$(find "$target" ! -type f ! -type d -print -quit)" || return 1
  contents=$(
    cd "$target" || exit 1
    find . -type f ! -name MANIFEST.sha256 ! -name MANIFEST.identity \
      ! -name SNAPSHOT.seal -print0 | LC_ALL=C sort -z | xargs -0 sha256sum
  ) || return 1
  if test -e "$target/MANIFEST.sha256" || test -L "$target/MANIFEST.sha256"; then
    test -f "$target/MANIFEST.sha256" && test ! -L "$target/MANIFEST.sha256" || return 1
    s72_atomic_write_atomic_file "$target/MANIFEST.sha256" 0444 "$contents"
  else
    s72_atomic_publish_new_file "$target/MANIFEST.sha256" 0444 "$contents"
  fi
)

s72_atomic_verify_manifest() (
  target=$1
  test -d "$target" && test ! -L "$target" || return 1
  test -z "$(find "$target" -type l -print -quit)" || return 1
  test -z "$(find "$target" ! -type f ! -type d -print -quit)" || return 1
  manifest=$target/MANIFEST.sha256
  test -f "$manifest" && test ! -L "$manifest" || return 1
  listed=$(mktemp "${TMPDIR:-/tmp}/s72-listed.XXXXXX") || return 1
  actual=$(mktemp "${TMPDIR:-/tmp}/s72-actual.XXXXXX") || {
    rm -f "$listed"
    return 1
  }
  trap 'rm -f "$listed" "$actual"' EXIT HUP INT TERM
  awk '
    length($0) < 68 || (substr($0, 65, 2) != "  " && substr($0, 65, 2) != " *") { exit 1 }
    length(substr($0, 1, 64)) != 64 || substr($0, 1, 64) !~ /^[0-9a-f]+$/ { exit 1 }
    substr($0, 67) !~ /^\.\/[A-Za-z0-9._\/-]+$/ { exit 1 }
    substr($0, 69) ~ /(^|\/)\.\.?(\/|$)/ || substr($0, 69) ~ /\/\// { exit 1 }
    { print substr($0, 67) }
  ' "$manifest" > "$listed" || return 1
  LC_ALL=C sort "$listed" -o "$listed" || return 1
  awk 'seen[$0]++ { exit 1 }' "$listed" || return 1
  (cd "$target" && find . -type f ! -name MANIFEST.sha256 ! -name MANIFEST.identity \
    ! -name SNAPSHOT.seal -print | LC_ALL=C sort) > "$actual" || return 1
  cmp -s "$listed" "$actual" || return 1
  (cd "$target" && sha256sum -c MANIFEST.sha256 >/dev/null)
)

s72_atomic_capture_tree_identity() {
  python3 - "$1" <<'PY'
import hashlib
import os
import stat
import sys

root = os.path.abspath(sys.argv[1])
excluded = {"MANIFEST.identity", "SNAPSHOT.seal"}
rows = []
for current, dirs, files in os.walk(root, topdown=True, followlinks=False):
    names = sorted(dirs + files)
    for name in names:
        path = os.path.join(current, name)
        relative = os.path.relpath(path, root).replace(os.sep, "/")
        if relative in excluded:
            continue
        value = os.lstat(path)
        if stat.S_ISLNK(value.st_mode):
            raise SystemExit(1)
        if stat.S_ISDIR(value.st_mode):
            kind = "d"
            digest = "-"
        elif stat.S_ISREG(value.st_mode):
            kind = "f"
            hasher = hashlib.sha256()
            flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
            fd = os.open(path, flags)
            try:
                opened = os.fstat(fd)
                if (opened.st_dev, opened.st_ino) != (value.st_dev, value.st_ino):
                    raise SystemExit(1)
                while True:
                    chunk = os.read(fd, 65536)
                    if not chunk:
                        break
                    hasher.update(chunk)
                after = os.fstat(fd)
                if (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns) != (
                    value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns
                ):
                    raise SystemExit(1)
            finally:
                os.close(fd)
            digest = hasher.hexdigest()
        else:
            raise SystemExit(1)
        rows.append(
            "\t".join(
                (
                    relative,
                    kind,
                    str(value.st_dev),
                    str(value.st_ino),
                    str(value.st_uid),
                    str(value.st_gid),
                    format(stat.S_IMODE(value.st_mode), "o"),
                    str(value.st_size),
                    str(value.st_mtime_ns),
                    str(value.st_ctime_ns),
                    digest,
                )
            )
        )
print("\n".join(sorted(rows)))
PY
}

s72_atomic_write_snapshot_seal() {
  snapshot=$1
  test -f "$snapshot/MANIFEST.sha256" && test ! -e "$snapshot/MANIFEST.identity" \
    && test ! -e "$snapshot/SNAPSHOT.seal" || return 1
  identity=$(s72_atomic_capture_tree_identity "$snapshot") || return 1
  s72_atomic_publish_new_file "$snapshot/MANIFEST.identity" 0400 "$identity" || return 1
  root_identity=$(stat -c '%d:%i:%u:%g:%a' -- "$snapshot") || return 1
  identity_hash=$(sha256sum "$snapshot/MANIFEST.identity" | awk '{ print $1 }') || return 1
  manifest_hash=$(sha256sum "$snapshot/MANIFEST.sha256" | awk '{ print $1 }') || return 1
  seal_payload=$(printf '%s\n' \
    'schema=s72-snapshot-seal-v1' \
    "root=$root_identity" \
    "identity-sha256=$identity_hash" \
    "manifest-sha256=$manifest_hash") || return 1
  seal=$(printf '%s\n' "$seal_payload" | sha256sum | awk '{ print $1 }') || return 1
  s72_atomic_publish_new_file "$snapshot/SNAPSHOT.seal" 0400 \
    "$seal_payload
seal=$seal" || return 1
  s72_atomic_fsync_path "$snapshot"
}

s72_atomic_verify_snapshot_seal() (
  snapshot=$1
  test -f "$snapshot/MANIFEST.identity" && test ! -L "$snapshot/MANIFEST.identity" || return 1
  test -f "$snapshot/SNAPSHOT.seal" && test ! -L "$snapshot/SNAPSHOT.seal" || return 1
  test "$(wc -l < "$snapshot/SNAPSHOT.seal")" -eq 5 || return 1
  payload=$(sed -n '1,4p' "$snapshot/SNAPSHOT.seal") || return 1
  recorded=$(sed -n '5s/^seal=//p' "$snapshot/SNAPSHOT.seal") || return 1
  s72_atomic_is_sha256 "$recorded" || return 1
  test "$recorded" = "$(printf '%s\n' "$payload" | sha256sum | awk '{ print $1 }')" || return 1
  test "$(sed -n '1p' "$snapshot/SNAPSHOT.seal")" = schema=s72-snapshot-seal-v1 || return 1
  test "$(sed -n '2s/^root=//p' "$snapshot/SNAPSHOT.seal")" = \
    "$(stat -c '%d:%i:%u:%g:%a' -- "$snapshot")" || return 1
  test "$(sed -n '3s/^identity-sha256=//p' "$snapshot/SNAPSHOT.seal")" = \
    "$(sha256sum "$snapshot/MANIFEST.identity" | awk '{ print $1 }')" || return 1
  test "$(sed -n '4s/^manifest-sha256=//p' "$snapshot/SNAPSHOT.seal")" = \
    "$(sha256sum "$snapshot/MANIFEST.sha256" | awk '{ print $1 }')" || return 1
  actual=$(s72_atomic_capture_tree_identity "$snapshot") || return 1
  test "$actual" = "$(cat "$snapshot/MANIFEST.identity")" || return 1
  s72_atomic_verify_manifest "$snapshot"
)

s72_atomic_require_marker_file() {
  require_root_owned_regular "$1" 600 || return 1
  test "$(stat -c %s "$1")" -eq 0
}

s72_atomic_require_marker_pair() {
  if test -f "$1" && test ! -L "$1"; then
    s72_atomic_require_marker_file "$1" && test ! -e "$2" && test ! -L "$2"
  else
    test ! -e "$1" && test ! -L "$1" && s72_atomic_require_marker_file "$2"
  fi
}

s72_atomic_require_payload_or_absent() {
  if test -f "$1" && test ! -L "$1"; then
    require_root_owned_regular "$1" 400 || return 1
    test -s "$1" && test ! -e "$2" && test ! -L "$2"
  else
    test ! -e "$1" && test ! -L "$1" && s72_atomic_require_marker_file "$2"
  fi
}

s72_atomic_preflight_gateway_identity_snapshot() (
  snapshot=$1
  s72_atomic_require_root_owned_regular "$snapshot/gateway-service-uid" 400 || return 1
  test "$(wc -l < "$snapshot/gateway-service-uid")" -eq 1 || return 1
  gateway_uid=$(cat "$snapshot/gateway-service-uid") || return 1
  s72_atomic_is_service_uid "$gateway_uid" || return 1
  expected_group=opensandbox-gateway:x:$gateway_uid:
  expected_user=opensandbox-gateway:x:$gateway_uid:$gateway_uid::/nonexistent:/usr/sbin/nologin

  s72_atomic_require_marker_pair "$snapshot/gateway-group.present" \
    "$snapshot/gateway-group.absent" || return 1
  s72_atomic_require_marker_pair "$snapshot/gateway-user.present" \
    "$snapshot/gateway-user.absent" || return 1
  if test -f "$snapshot/gateway-group.present"; then
    test -f "$snapshot/gateway-user.present" || return 1
    s72_atomic_require_root_owned_regular "$snapshot/gateway-group.entry" 400 || return 1
    s72_atomic_require_root_owned_regular "$snapshot/gateway-user.entry" 400 || return 1
    test "$(cat "$snapshot/gateway-group.entry")" = "$expected_group" || return 1
    test "$(cat "$snapshot/gateway-user.entry")" = "$expected_user" || return 1
  else
    test -f "$snapshot/gateway-user.absent" || return 1
    test ! -e "$snapshot/gateway-group.entry" && test ! -L "$snapshot/gateway-group.entry" || return 1
    test ! -e "$snapshot/gateway-user.entry" && test ! -L "$snapshot/gateway-user.entry" || return 1
  fi

  s72_atomic_require_marker_pair "$snapshot/runtime-state.present" \
    "$snapshot/runtime-state.absent" || return 1
  if test -f "$snapshot/runtime-state.present"; then
    s72_atomic_require_root_owned_regular "$snapshot/runtime-state.identity" 400 || return 1
    IFS=: read -r device inode owner group mode extra < "$snapshot/runtime-state.identity" || return 1
    test -z "${extra:-}" || return 1
    case "$device:$inode:$owner:$group:$mode" in *[!0-9:]*) return 1 ;; esac
    test "$owner:$group:$mode" = "$gateway_uid:$gateway_uid:700" || return 1
  else
    test ! -e "$snapshot/runtime-state.identity" && test ! -L "$snapshot/runtime-state.identity" || return 1
  fi
)

s72_atomic_require_transaction_owner() {
  root=$1
  owner=$root/transaction-owner
  s72_atomic_require_root_owned_regular "$owner" 400 || return 1
  test "$(wc -l < "$owner")" -eq 3 || return 1
  test "$(sed -n '1p' "$owner")" = schema=s72-transaction-owner-v1 || return 1
  transaction=$(sed -n '2s/^transaction=//p' "$owner") || return 1
  s72_atomic_is_transaction_id "$transaction" || return 1
  test "$(sed -n '3s/^root=//p' "$owner")" = "$(stat -c %d:%i -- "$root")"
}

s72_atomic_require_exact_lifecycle() {
  test "$(systemctl show opensandbox.service -p ActiveState --value)" = active || return 1
  test "$(systemctl show opensandbox.service -p FragmentPath --value)" = \
    /etc/systemd/system/opensandbox.service || return 1
  listener_rows=$(ss -H -ltn 'sport = :8080') || return 1
  printf '%s\n' "$listener_rows" | awk '
    NF {
      total++
      if ($1 == "LISTEN" && $4 == "127.0.0.1:8080") exact++
    }
    END { exit !(total == 1 && exact == 1) }
  '
}

s72_atomic_write_lifecycle_authority() {
  target=$1
  s72_atomic_require_exact_lifecycle || return 1
  contents=$(printf '%s\n' \
    'schema=s72-lifecycle-authority-v1' \
    'service=opensandbox.service' \
    'active=active' \
    'fragment=/etc/systemd/system/opensandbox.service' \
    'listener=127.0.0.1:8080' \
    'listener-count=1') || return 1
  s72_atomic_publish_new_file "$target" 0400 "$contents"
}

s72_atomic_require_lifecycle_authority_file() {
  authority=$1
  if command -v require_root_owned_regular >/dev/null 2>&1; then
    require_root_owned_regular "$authority" 400 || return 1
  else
    s72_atomic_require_root_owned_regular "$authority" 400 || return 1
  fi
  test "$(cat "$authority")" = "$(printf '%s\n' \
    'schema=s72-lifecycle-authority-v1' \
    'service=opensandbox.service' \
    'active=active' \
    'fragment=/etc/systemd/system/opensandbox.service' \
    'listener=127.0.0.1:8080' \
    'listener-count=1')"
}

s72_atomic_require_snapshot_inventory() (
  snapshot=$1
  require_root_tree "$snapshot" || return 1
  test -z "$(find "$snapshot" -mindepth 1 -maxdepth 1 -type d \
    ! -name etc-opensandbox-gateway -print -quit)" || return 1
  for path in "$snapshot"/*; do
    test -e "$path" || continue
    name=${path##*/}
    case "$name" in
      MANIFEST.sha256|MANIFEST.identity|SNAPSHOT.seal|transaction-owner|lifecycle.authority|\
      captured-authority-sha|captured-authority-evidence|workspaces.acl|\
      config.present|config.absent|config.metadata|etc-opensandbox-gateway|\
      authority-sha|authority-sha.absent|authority-evidence|authority-evidence.absent|\
      current|current.absent|rollback-pointer|rollback-pointer.absent|\
      gateway-service-uid|gateway-user.present|gateway-user.absent|gateway-user.entry|\
      gateway-group.present|gateway-group.absent|gateway-group.entry|\
      runtime-state.present|runtime-state.absent|runtime-state.identity|\
      opensandbox-gateway.service|opensandbox-gateway.service.present|opensandbox-gateway.service.absent|\
      opensandbox-gateway.service.active|opensandbox-gateway.service.inactive|\
      opensandbox-gateway.service.enabled|opensandbox-gateway.service.disabled|\
      opensandbox-gateway-helper.service|opensandbox-gateway-helper.service.present|\
      opensandbox-gateway-helper.service.absent|opensandbox-gateway-helper.service.active|\
      opensandbox-gateway-helper.service.inactive|opensandbox-gateway-helper.service.enabled|\
      opensandbox-gateway-helper.service.disabled) ;;
      *) return 1 ;;
    esac
  done
)

s72_atomic_preflight_snapshot() (
  snapshot=$1
  s72_atomic_require_snapshot_inventory "$snapshot" || return 1
  s72_atomic_verify_manifest "$snapshot" || return 1
  if test -e "$snapshot/MANIFEST.identity" || test -e "$snapshot/SNAPSHOT.seal"; then
    s72_atomic_verify_snapshot_seal "$snapshot" || return 1
  fi
  s72_atomic_require_lifecycle_authority_file "$snapshot/lifecycle.authority" || return 1
  s72_atomic_require_transaction_owner "$snapshot" || return 1
  s72_atomic_preflight_gateway_identity_snapshot "$snapshot" || return 1
  require_root_owned_regular "$snapshot/captured-authority-sha" 400 || return 1
  require_root_owned_regular "$snapshot/captured-authority-evidence" 400 || return 1
  s72_atomic_is_commit "$(cat "$snapshot/captured-authority-sha")" || return 1
  s72_atomic_is_authority_evidence_id "$(cat "$snapshot/captured-authority-evidence")" || return 1
  for unit in opensandbox-gateway.service opensandbox-gateway-helper.service; do
    s72_atomic_require_marker_pair "$snapshot/$unit.present" "$snapshot/$unit.absent" || return 1
    if test -f "$snapshot/$unit.present"; then
      require_root_owned_regular "$snapshot/$unit" 644 || return 1
    else
      test ! -e "$snapshot/$unit" && test ! -L "$snapshot/$unit" || return 1
    fi
    s72_atomic_require_marker_pair "$snapshot/$unit.active" "$snapshot/$unit.inactive" || return 1
    s72_atomic_require_marker_pair "$snapshot/$unit.enabled" "$snapshot/$unit.disabled" || return 1
    if test -f "$snapshot/$unit.absent"; then
      test -f "$snapshot/$unit.inactive" && test -f "$snapshot/$unit.disabled" || return 1
    fi
  done
  s72_atomic_require_marker_pair "$snapshot/config.present" "$snapshot/config.absent" || return 1
  if test -f "$snapshot/config.present"; then
    test -d "$snapshot/etc-opensandbox-gateway" && test ! -L "$snapshot/etc-opensandbox-gateway" || return 1
    test -f "$snapshot/config.metadata" && test ! -L "$snapshot/config.metadata" || return 1
    command -v require_gateway_config_contract_at >/dev/null 2>&1 || return 1
    require_gateway_config_contract_at "$snapshot/etc-opensandbox-gateway" || return 1
    command -v verify_config_metadata >/dev/null 2>&1 || return 1
    verify_config_metadata "$snapshot/etc-opensandbox-gateway" "$snapshot/config.metadata" || return 1
  else
    test ! -e "$snapshot/etc-opensandbox-gateway" && test ! -L "$snapshot/etc-opensandbox-gateway" || return 1
    test ! -e "$snapshot/config.metadata" && test ! -L "$snapshot/config.metadata" || return 1
  fi
  require_root_owned_regular "$snapshot/workspaces.acl" 600 || return 1
  s72_atomic_require_payload_or_absent \
    "$snapshot/authority-sha" "$snapshot/authority-sha.absent" || return 1
  s72_atomic_require_payload_or_absent \
    "$snapshot/authority-evidence" "$snapshot/authority-evidence.absent" || return 1
  if test -f "$snapshot/authority-sha"; then
    test -f "$snapshot/authority-evidence" || return 1
    s72_atomic_is_commit "$(cat "$snapshot/authority-sha")" || return 1
    s72_atomic_is_authority_evidence_id \
      "$(cat "$snapshot/authority-evidence")" || return 1
  else
    test ! -e "$snapshot/authority-evidence" && test ! -L "$snapshot/authority-evidence" || return 1
  fi
  s72_atomic_require_payload_or_absent \
    "$snapshot/current" "$snapshot/current.absent" || return 1
  if test -f "$snapshot/current"; then
    old_target=$(cat "$snapshot/current") || return 1
    case "$old_target" in releases/*) old_commit=${old_target#releases/} ;; *) return 1 ;; esac
    validate_release "$old_commit" rollback || return 1
    test -f "$snapshot/authority-sha" && test "$(cat "$snapshot/authority-sha")" = "$old_commit" || return 1
    s72_atomic_is_authority_evidence_id "$(cat "$snapshot/authority-evidence")" || return 1
  else
    test -f "$snapshot/authority-sha.absent" && test -f "$snapshot/authority-evidence.absent" || return 1
  fi
  s72_atomic_require_payload_or_absent \
    "$snapshot/rollback-pointer" "$snapshot/rollback-pointer.absent" || return 1
  test ! -f "$snapshot/rollback-pointer" \
    || s72_atomic_is_snapshot_id "$(cat "$snapshot/rollback-pointer")" || return 1
)

s72_atomic_create_snapshot_stage() {
  snapshots_parent=$1
  transaction_id=$2
  s72_atomic_is_transaction_id "$transaction_id" || return 1
  s72_atomic_require_root_owned_directory "$snapshots_parent" 700 || return 1
  stage_parent="$snapshots_parent"
  stage=$stage_parent/.snapshot-stage-$transaction_id
  test ! -e "$stage" && test ! -L "$stage" || return 1
  mkdir -m 0700 -- "$stage" || return 1
  chown root:root "$stage" || return 1
  stage_root=$(stat -c %d:%i -- "$stage") || return 1
  owner=$(printf '%s\n' \
    'schema=s72-transaction-owner-v1' \
    "transaction=$transaction_id" \
    "root=$stage_root") || return 1
  s72_atomic_publish_new_file "$stage/transaction-owner" 0400 "$owner" || return 1
  s72_atomic_fsync_path "$stage" || return 1
  printf '%s\n' "$stage"
}

s72_atomic_publish_snapshot() {
  stage=$1
  snapshots_parent=$2
  snapshot_id=$3
  s72_atomic_is_snapshot_id "$snapshot_id" || return 1
  test "${stage%/*}" = "$snapshots_parent" || return 1
  s72_atomic_preflight_snapshot "$stage" || return 1
  test ! -e "$stage/MANIFEST.identity" && test ! -e "$stage/SNAPSHOT.seal" || return 1
  stage_device=$(stat -c %d -- "$stage") || return 1
  parent_device=$(stat -c %d -- "$snapshots_parent") || return 1
  test "$stage_device" = "$parent_device" || return 1
  s72_atomic_fsync_tree "$stage" || return 1
  s72_atomic_write_snapshot_seal "$stage" || return 1
  s72_atomic_verify_snapshot_seal "$stage" || return 1
  s72_atomic_fsync_tree "$stage" || return 1
  stage_identity=$(s72_atomic_directory_identity "$stage") || return 1
  published=$snapshots_parent/$snapshot_id
  test ! -e "$published" && test ! -L "$published" || return 1
  mv -T -n "$stage" "$published" || return 1
  test ! -e "$stage" && test ! -L "$stage" || return 1
  test -d "$published" && test ! -L "$published" || return 1
  test "$(s72_atomic_directory_identity "$published")" = "$stage_identity" || return 1
  s72_atomic_fsync_path "$snapshots_parent" || return 1
  s72_atomic_verify_snapshot_seal "$published" || return 1
  printf '%s\n' "$published"
}

s72_atomic_phase_transition_allowed() (
  from=$1
  to=$2
  if test "$to" = recovering; then
    case "$from" in committed|cleaned) return 1 ;; *) return 0 ;; esac
  fi
  case "$from:$to" in
    reserved:reserved|reserved:snapshot-published|reserved:committed|\
    snapshot-published:identity-group-intent|identity-group-intent:identity-group-ready|\
    identity-group-ready:identity-user-intent|identity-user-intent:identity-user-ready|\
    identity-user-ready:identity-runtime-intent|identity-runtime-intent:identity-ready|\
    identity-ready:release-published|snapshot-published:release-published|snapshot-published:staged|\
    release-published:staged|staged:stop-intent|stop-intent:stopped|\
    stopped:identity-applied|identity-applied:units-applied|stopped:units-applied|\
    units-applied:config-applied|config-applied:acl-applied|\
    acl-applied:authority-applied|authority-applied:pointer-applied|\
    pointer-applied:current-applied|current-applied:revalidated|\
    revalidated:runtime-restored|runtime-restored:committed|committed:cleaned|\
    recovering:staged) return 0 ;;
    *) return 1 ;;
  esac
)

s72_atomic_validate_transaction_fields() (
  transaction_id=$1
  sequence=$2
  operation=$3
  phase=$4
  recovery_snapshot=$5
  apply_snapshot=$6
  from_commit=$7
  to_commit=$8
  evidence=$9
  shift 9
  stage_identity=$1
  previous_seal=$2
  s72_atomic_is_transaction_id "$transaction_id" || return 1
  case "$sequence" in [0-9][0-9][0-9][0-9][0-9][0-9]) ;; *) return 1 ;; esac
  case "$operation" in install|rollback) ;; *) return 1 ;; esac
  case "$phase" in reserved|snapshot-published|identity-group-intent|identity-group-ready|\
    identity-user-intent|identity-user-ready|identity-runtime-intent|identity-ready|\
    release-published|staged|stop-intent|stopped|identity-applied|\
    units-applied|config-applied|acl-applied|authority-applied|current-applied|\
    revalidated|runtime-restored|pointer-applied|recovering|committed|cleaned) ;; *) return 1 ;; esac
  s72_atomic_is_snapshot_id "$recovery_snapshot" || return 1
  s72_atomic_is_snapshot_id "$apply_snapshot" || return 1
  case "$from_commit" in none) ;; *) s72_atomic_is_commit "$from_commit" || return 1 ;; esac
  case "$to_commit" in none) ;; *) s72_atomic_is_commit "$to_commit" || return 1 ;; esac
  case "$operation" in
    install) s72_atomic_is_commit "$to_commit" || return 1 ;;
    rollback) s72_atomic_is_commit "$from_commit" || return 1 ;;
  esac
  s72_atomic_is_authority_evidence_id "$evidence" || return 1
  case "$stage_identity" in none|*:*:*:*:*:*:*:*:*) ;; *) return 1 ;; esac
  case "$previous_seal" in none) ;; *) s72_atomic_is_sha256 "$previous_seal" || return 1 ;; esac
)

s72_atomic_transaction_record_payload() {
  printf '%s\n' \
    "schema=$S72_ATOMIC_TRANSACTION_SCHEMA" \
    "id=$1" \
    "sequence=$2" \
    "operation=$3" \
    "phase=$4" \
    "recovery-snapshot=$5" \
    "apply-snapshot=$6" \
    "from=$7" \
    "to=$8" \
    "evidence=$9" \
    "stage-identity=${10}" \
    "previous-seal=${11}"
}

s72_atomic_publish_transaction_record() (
  records=$1
  transaction_id=$2
  sequence=$3
  operation=$4
  phase=$5
  recovery_snapshot=$6
  apply_snapshot=$7
  from_commit=$8
  to_commit=$9
  shift 9
  evidence=$1
  stage_identity=$2
  previous_seal=$3
  s72_atomic_validate_transaction_fields "$transaction_id" "$sequence" "$operation" "$phase" \
    "$recovery_snapshot" "$apply_snapshot" "$from_commit" "$to_commit" "$evidence" \
    "$stage_identity" "$previous_seal" || return 1
  payload=$(s72_atomic_transaction_record_payload "$transaction_id" "$sequence" "$operation" "$phase" \
    "$recovery_snapshot" "$apply_snapshot" "$from_commit" "$to_commit" "$evidence" \
    "$stage_identity" "$previous_seal") || return 1
  seal=$(printf '%s\n' "$payload" | sha256sum | awk '{ print $1 }') || return 1
  record=$records/transaction-$transaction_id-$sequence.record
  s72_atomic_publish_new_file "$record" 0400 "$payload
record-seal=$seal" || return 1
  printf '%s\n' "$record"
)

s72_atomic_verify_transaction_record() (
  record=$1
  test -f "$record" && test ! -L "$record" || return 1
  test "$(stat -c %u:%g:%a "$record")" = 0:0:400 || return 1
  test "$(wc -l < "$record")" -eq 13 || return 1
  payload=$(sed -n '1,12p' "$record") || return 1
  seal=$(sed -n '13s/^record-seal=//p' "$record") || return 1
  s72_atomic_is_sha256 "$seal" || return 1
  test "$seal" = "$(printf '%s\n' "$payload" | sha256sum | awk '{ print $1 }')" || return 1
  test "$(sed -n '1p' "$record")" = schema=s72-atomic-transaction-v1 || return 1
  id=$(sed -n '2s/^id=//p' "$record") || return 1
  sequence=$(sed -n '3s/^sequence=//p' "$record") || return 1
  test "${record##*/}" = transaction-$id-$sequence.record || return 1
  operation=$(sed -n '4s/^operation=//p' "$record") || return 1
  phase=$(sed -n '5s/^phase=//p' "$record") || return 1
  recovery_snapshot=$(sed -n '6s/^recovery-snapshot=//p' "$record") || return 1
  apply_snapshot=$(sed -n '7s/^apply-snapshot=//p' "$record") || return 1
  from_commit=$(sed -n '8s/^from=//p' "$record") || return 1
  to_commit=$(sed -n '9s/^to=//p' "$record") || return 1
  evidence=$(sed -n '10s/^evidence=//p' "$record") || return 1
  stage_identity=$(sed -n '11s/^stage-identity=//p' "$record") || return 1
  previous_seal=$(sed -n '12s/^previous-seal=//p' "$record") || return 1
  s72_atomic_validate_transaction_fields "$id" "$sequence" "$operation" "$phase" \
    "$recovery_snapshot" "$apply_snapshot" "$from_commit" "$to_commit" "$evidence" \
    "$stage_identity" "$previous_seal"
)

s72_atomic_require_transaction_inventory() (
  records=$1
  s72_atomic_require_root_owned_directory "$records" 700 || return 1
  test -z "$(find "$records" -mindepth 1 -maxdepth 1 ! -type f -print -quit)" || return 1
  for record in "$records"/*; do
    test -e "$record" || continue
    case "${record##*/}" in
      transaction-[0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f]-[0-9][0-9][0-9][0-9][0-9][0-9].record) ;;
      *) return 1 ;;
    esac
    s72_atomic_verify_transaction_record "$record" || return 1
  done
)

s72_atomic_load_transaction() {
  records=$1
  transaction_id=$2
  s72_atomic_is_transaction_id "$transaction_id" || return 1
  s72_atomic_require_transaction_inventory "$records" || return 1
  previous=none
  previous_phase=
  latest_previous_phase=
  expected_operation=
  expected_recovery=
  expected_apply=
  expected_from=
  expected_to=
  expected_evidence=
  expected_stage=none
  expected=0
  latest=
  for record in "$records"/transaction-$transaction_id-*.record; do
    test -e "$record" || continue
    s72_atomic_verify_transaction_record "$record" || return 1
    sequence=$(sed -n '3s/^sequence=//p' "$record") || return 1
    sequence_value=$(printf '%s\n' "$sequence" | sed 's/^0*//')
    test -n "$sequence_value" || sequence_value=0
    test "$sequence_value" -eq "$expected" || return 1
    test "$(sed -n '12s/^previous-seal=//p' "$record")" = "$previous" || return 1
    operation=$(sed -n '4s/^operation=//p' "$record") || return 1
    phase=$(sed -n '5s/^phase=//p' "$record") || return 1
    recovery=$(sed -n '6s/^recovery-snapshot=//p' "$record") || return 1
    apply=$(sed -n '7s/^apply-snapshot=//p' "$record") || return 1
    from=$(sed -n '8s/^from=//p' "$record") || return 1
    to=$(sed -n '9s/^to=//p' "$record") || return 1
    evidence=$(sed -n '10s/^evidence=//p' "$record") || return 1
    stage=$(sed -n '11s/^stage-identity=//p' "$record") || return 1
    if test "$expected" -eq 0; then
      test "$phase" = reserved && test "$stage" = none && test "$previous" = none || return 1
      expected_operation=$operation
      expected_recovery=$recovery
      expected_apply=$apply
      expected_from=$from
      expected_to=$to
      expected_evidence=$evidence
    else
      test "$operation:$recovery:$apply:$from:$to:$evidence" = \
        "$expected_operation:$expected_recovery:$expected_apply:$expected_from:$expected_to:$expected_evidence" || return 1
      s72_atomic_phase_transition_allowed "$previous_phase" "$phase" || return 1
      if test "$expected_stage" = none; then
        if test "$stage" = none; then
          case "$phase" in committed|cleaned) ;; *) return 1 ;; esac
        else
          expected_stage=$stage
        fi
      else
        test "$stage" = "$expected_stage" || return 1
      fi
    fi
    previous=$(sed -n '13s/^record-seal=//p' "$record") || return 1
    latest_previous_phase=$previous_phase
    previous_phase=$phase
    latest=$record
    expected=$((expected + 1))
  done
  test -n "$latest" || return 1
  S72_TX_ID=$transaction_id
  S72_TX_SEQUENCE=$(sed -n '3s/^sequence=//p' "$latest")
  S72_TX_OPERATION=$(sed -n '4s/^operation=//p' "$latest")
  S72_TX_PHASE=$(sed -n '5s/^phase=//p' "$latest")
  S72_TX_PREVIOUS_PHASE=$latest_previous_phase
  S72_TX_RECOVERY_SNAPSHOT=$(sed -n '6s/^recovery-snapshot=//p' "$latest")
  S72_TX_APPLY_SNAPSHOT=$(sed -n '7s/^apply-snapshot=//p' "$latest")
  S72_TX_FROM=$(sed -n '8s/^from=//p' "$latest")
  S72_TX_TO=$(sed -n '9s/^to=//p' "$latest")
  S72_TX_EVIDENCE=$(sed -n '10s/^evidence=//p' "$latest")
  S72_TX_STAGE_IDENTITY=$(sed -n '11s/^stage-identity=//p' "$latest")
  S72_TX_SEAL=$previous
}

s72_atomic_load_active_transaction() {
  records=$1
  s72_atomic_require_transaction_inventory "$records" || return 1
  active=
  for record in "$records"/transaction-*-000000.record; do
    test -e "$record" || continue
    id=$(sed -n '2s/^id=//p' "$record") || return 1
    s72_atomic_load_transaction "$records" "$id" || return 1
    if test "$S72_TX_PHASE" != cleaned; then
      test -z "$active" || return 1
      active=$id
    fi
  done
  test -n "$active" || return 2
  s72_atomic_load_transaction "$records" "$active"
}

s72_atomic_advance_transaction() {
  records=$1
  transaction_id=$2
  s72_requested_phase=$3
  s72_atomic_load_transaction "$records" "$transaction_id" || return 1
  s72_atomic_phase_transition_allowed "$S72_TX_PHASE" "$s72_requested_phase" || return 1
  sequence_value=$(printf '%s\n' "$S72_TX_SEQUENCE" | sed 's/^0*//')
  test -n "$sequence_value" || sequence_value=0
  next=$(printf '%06d' "$((sequence_value + 1))") || return 1
  s72_atomic_publish_transaction_record "$records" "$transaction_id" "$next" \
    "$S72_TX_OPERATION" "$s72_requested_phase" "$S72_TX_RECOVERY_SNAPSHOT" "$S72_TX_APPLY_SNAPSHOT" \
    "$S72_TX_FROM" "$S72_TX_TO" "$S72_TX_EVIDENCE" "$S72_TX_STAGE_IDENTITY" "$S72_TX_SEAL" >/dev/null
}

s72_atomic_bind_transaction_stage() {
  records=$1
  transaction_id=$2
  s72_requested_stage_identity=$3
  s72_atomic_load_transaction "$records" "$transaction_id" || return 1
  test "$S72_TX_STAGE_IDENTITY" = none || return 1
  case "$s72_requested_stage_identity" in *:*:*:*:*:*:*:*:*) ;; *) return 1 ;; esac
  sequence_value=$(printf '%s\n' "$S72_TX_SEQUENCE" | sed 's/^0*//')
  test -n "$sequence_value" || sequence_value=0
  next=$(printf '%06d' "$((sequence_value + 1))") || return 1
  s72_atomic_publish_transaction_record "$records" "$transaction_id" "$next" \
    "$S72_TX_OPERATION" "$S72_TX_PHASE" "$S72_TX_RECOVERY_SNAPSHOT" "$S72_TX_APPLY_SNAPSHOT" \
    "$S72_TX_FROM" "$S72_TX_TO" "$S72_TX_EVIDENCE" "$s72_requested_stage_identity" "$S72_TX_SEAL" >/dev/null
}

s72_atomic_record_authority_state() {
  authority_sha=$1
  authority_evidence=$2
  is_commit "$authority_sha" || return 1
  is_authority_evidence_id "$authority_evidence" || return 1
  s72_atomic_write_atomic_file "$AUTHORITY_SHA_STATE" 0600 "$authority_sha" || return 1
  s72_atomic_write_atomic_file "$AUTHORITY_EVIDENCE_STATE" 0600 "$authority_evidence" || return 1
  test "$(cat "$AUTHORITY_SHA_STATE")" = "$authority_sha"
  test "$(cat "$AUTHORITY_EVIDENCE_STATE")" = "$authority_evidence"
}

s72_atomic_remove_owned_stage() {
  stage=$1
  transaction_id=$2
  s72_atomic_is_transaction_id "$transaction_id" || return 1
  case "${stage##*/}" in
    .snapshot-stage-$transaction_id|.s72-release-$transaction_id|.s72-runtime-$transaction_id|\
    .s72-transaction-$transaction_id|.s72-units-apply-$transaction_id|\
    .s72-units-recovery-$transaction_id|.s72-config-apply-$transaction_id|\
    .s72-config-recovery-$transaction_id|.s72-state-apply-$transaction_id|\
    .s72-state-recovery-$transaction_id|.s72-current-apply-$transaction_id|\
    .s72-current-recovery-$transaction_id) ;;
    *) return 1 ;;
  esac
  s72_atomic_require_root_tree "$stage" || return 1
  test -f "$stage/transaction-owner" && test ! -L "$stage/transaction-owner" || return 1
  test "$(wc -l < "$stage/transaction-owner")" -eq 3 || return 1
  test "$(sed -n '1p' "$stage/transaction-owner")" = schema=s72-transaction-owner-v1 || return 1
  test "$(sed -n '2s/^transaction=//p' "$stage/transaction-owner")" = "$transaction_id" || return 1
  expected_root=$(sed -n '3s/^root=//p' "$stage/transaction-owner") || return 1
  test "$expected_root" = "$(stat -c %d:%i -- "$stage")" || return 1
  if test "${s72_loader_mode:-}" = test-source-eval || test "$(uname -s)" != Linux; then
    rm -rf -- "$stage" || return 1
  else
    python3 - "$stage" "$expected_root" <<'PY'
import os
import stat
import sys

path, expected_text = sys.argv[1:]
expected = tuple(int(value) for value in expected_text.split(":"))
parent = os.path.dirname(path)
name = os.path.basename(path)
flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)

def remove_directory(parent_fd: int, entry: str, identity: tuple[int, int]) -> None:
    before = os.stat(entry, dir_fd=parent_fd, follow_symlinks=False)
    if not stat.S_ISDIR(before.st_mode) or (before.st_dev, before.st_ino) != identity:
        raise SystemExit(1)
    directory_fd = os.open(entry, flags, dir_fd=parent_fd)
    try:
        opened = os.fstat(directory_fd)
        if (opened.st_dev, opened.st_ino) != identity:
            raise SystemExit(1)
        for child in sorted(os.listdir(directory_fd)):
            child_before = os.stat(child, dir_fd=directory_fd, follow_symlinks=False)
            child_identity = (child_before.st_dev, child_before.st_ino)
            if stat.S_ISDIR(child_before.st_mode):
                remove_directory(directory_fd, child, child_identity)
            elif stat.S_ISREG(child_before.st_mode):
                child_fd = os.open(
                    child,
                    os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
                    dir_fd=directory_fd,
                )
                try:
                    child_opened = os.fstat(child_fd)
                    if (child_opened.st_dev, child_opened.st_ino) != child_identity:
                        raise SystemExit(1)
                finally:
                    os.close(child_fd)
                child_after = os.stat(child, dir_fd=directory_fd, follow_symlinks=False)
                if (child_after.st_dev, child_after.st_ino) != child_identity:
                    raise SystemExit(1)
                os.unlink(child, dir_fd=directory_fd)
                os.fsync(directory_fd)
            else:
                raise SystemExit(1)
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)
    after = os.stat(entry, dir_fd=parent_fd, follow_symlinks=False)
    if (after.st_dev, after.st_ino) != identity:
        raise SystemExit(1)
    os.rmdir(entry, dir_fd=parent_fd)
    os.fsync(parent_fd)

parent_fd = os.open(parent, flags)
try:
    remove_directory(parent_fd, name, expected)
finally:
    os.close(parent_fd)
PY
  fi
  test ! -e "$stage" && test ! -L "$stage"
}

s72_atomic_prepare_workspace() {
  parent=$1
  label=$2
  transaction_id=$3
  workspace=$parent/.s72-$label-$transaction_id
  if test -e "$workspace" || test -L "$workspace"; then
    test -d "$workspace" && test ! -L "$workspace" || return 1
    s72_atomic_require_root_tree "$workspace" || return 1
    s72_atomic_require_transaction_owner "$workspace" || return 1
    test "$(sed -n '2s/^transaction=//p' "$workspace/transaction-owner")" = "$transaction_id" || return 1
  else
    mkdir -m 0700 -- "$workspace" || return 1
    chown root:root "$workspace" || return 1
    root_id=$(stat -c %d:%i "$workspace") || return 1
    s72_atomic_publish_new_file "$workspace/transaction-owner" 0400 \
      "$(printf '%s\n' 'schema=s72-transaction-owner-v1' \
        "transaction=$transaction_id" "root=$root_id")" || return 1
    s72_atomic_fsync_path "$workspace" || return 1
    s72_atomic_fsync_path "$parent" || return 1
  fi
  printf '%s\n' "$workspace"
}

s72_atomic_file_matches() (
  live=$1
  source=$2
  expected_mode=${3:-$(stat -c %a "$source")}
  test -f "$live" && test ! -L "$live" || return 1
  test -f "$source" && test ! -L "$source" || return 1
  cmp -s "$live" "$source" || return 1
  test "$(stat -c %u:%g:%a "$live")" = "0:0:$expected_mode"
)

s72_atomic_directory_matches() (
  live=$1
  source=$2
  test -d "$live" && test ! -L "$live" || return 1
  test -d "$source" && test ! -L "$source" || return 1
  live_metadata=$(capture_config_metadata "$live") || return 1
  source_metadata=$(capture_config_metadata "$source") || return 1
  test "$live_metadata" = "$source_metadata"
)

s72_atomic_directory_matches_identity_except_ctime() (
  live=$1
  source=$2
  expected=$3
  test "$source" != absent || return 1
  actual=$(s72_atomic_node_identity "$live") || return 1
  test "${actual%:*}" = "${expected%:*}" || return 1
  s72_atomic_directory_matches "$live" "$source"
)

s72_atomic_apply_file() {
  live=$1
  source=$2
  workspace=$3
  key=$4
  expected_mode=${5:-}
  parent=${live%/*}
  new=$workspace/$key.new
  old=$workspace/$key.old
  identity_file=$workspace/$key.live-identity
  applied=$workspace/$key.applied
  if test -f "$applied"; then
    if test "$source" = absent; then
      test ! -e "$live" && test ! -L "$live"
    else
      s72_atomic_file_matches "$live" "$source" "${expected_mode:-$(stat -c %a "$source")}"
    fi
    return $?
  fi
  if test "$source" != absent && test ! -e "$new"; then
    test -n "$expected_mode" || expected_mode=$(stat -c %a "$source")
    install -o root -g root -m "$expected_mode" "$source" "$new" || return 1
    s72_atomic_file_matches "$new" "$source" "$expected_mode" || return 1
  fi
  if test ! -e "$identity_file"; then
    if test -e "$live" || test -L "$live"; then
      test -f "$live" && test ! -L "$live" || return 1
      live_identity=$(s72_atomic_node_identity "$live") || return 1
      s72_atomic_publish_new_file "$identity_file" 0400 "$live_identity" || return 1
    else
      s72_atomic_publish_new_file "$identity_file" 0400 absent || return 1
    fi
  fi
  expected=$(cat "$identity_file") || return 1
  if test "$expected" != absent && test ! -e "$old"; then
    s72_atomic_require_identity "$live" "$expected" || return 1
    mv -T -n "$live" "$old" || return 1
    s72_atomic_require_identity "$old" "$expected" || {
      test -e "$live" || mv -T -n "$old" "$live" || :
      return 1
    }
    s72_atomic_fsync_path "$parent" || return 1
  elif test "$expected" = absent; then
    test ! -e "$live" && test ! -L "$live" || return 1
  fi
  if test "$source" != absent; then
    if test -e "$live" || test -L "$live"; then
      s72_atomic_file_matches "$live" "$source" "$expected_mode" || return 1
    else
      mv -T -n "$new" "$live" || return 1
      s72_atomic_file_matches "$live" "$source" "$expected_mode" || return 1
      s72_atomic_fsync_path "$parent" || return 1
    fi
  else
    test ! -e "$live" && test ! -L "$live" || return 1
  fi
  s72_atomic_publish_new_file "$applied" 0400 applied
}

s72_atomic_apply_directory() {
  live=$1
  source=$2
  workspace=$3
  key=$4
  parent=${live%/*}
  new=$workspace/$key.new
  old=$workspace/$key.old
  identity_file=$workspace/$key.live-identity
  applied=$workspace/$key.applied
  if test -f "$applied"; then
    if test "$source" = absent; then
      test ! -e "$live" && test ! -L "$live"
    else
      s72_atomic_directory_matches "$live" "$source"
    fi
    return $?
  fi
  if test "$source" != absent && test ! -e "$new"; then
    cp -a "$source" "$new" || return 1
    s72_atomic_directory_matches "$new" "$source" || return 1
  fi
  if test ! -e "$identity_file"; then
    if test -e "$live" || test -L "$live"; then
      test -d "$live" && test ! -L "$live" || return 1
      live_identity=$(s72_atomic_node_identity "$live") || return 1
      s72_atomic_publish_new_file "$identity_file" 0400 "$live_identity" || return 1
    else
      s72_atomic_publish_new_file "$identity_file" 0400 absent || return 1
    fi
  fi
  expected=$(cat "$identity_file") || return 1
  if test "$expected" != absent && test ! -e "$old"; then
    s72_atomic_require_identity "$live" "$expected" || return 1
    mv -T -n "$live" "$old" || return 1
    s72_atomic_require_identity "$old" "$expected" \
      || s72_atomic_directory_matches_identity_except_ctime "$old" "$source" "$expected" \
      || {
        test -e "$live" || mv -T -n "$old" "$live" || :
        return 1
      }
    s72_atomic_fsync_path "$parent" || return 1
  elif test "$expected" = absent; then
    test ! -e "$live" && test ! -L "$live" || return 1
  fi
  if test "$source" != absent; then
    if test -e "$live" || test -L "$live"; then
      s72_atomic_directory_matches "$live" "$source" || return 1
    else
      mv -T -n "$new" "$live" || return 1
      s72_atomic_directory_matches "$live" "$source" || return 1
      s72_atomic_fsync_path "$parent" || return 1
    fi
  else
    test ! -e "$live" && test ! -L "$live" || return 1
  fi
  s72_atomic_publish_new_file "$applied" 0400 applied
}

s72_atomic_apply_current_link() {
  snapshot=$1
  transaction_id=$2
  scope=${3:-apply}
  workspace=$(s72_atomic_prepare_workspace "${CURRENT_LINK%/*}" current-$scope "$transaction_id") || return 1
  old=$workspace/current.old
  new=$workspace/current.new
  identity_file=$workspace/current.live-identity
  applied=$workspace/current.applied
  if test -f "$snapshot/current"; then
    desired=$(cat "$snapshot/current") || return 1
    case "$desired" in releases/*) ;; *) return 1 ;; esac
  else
    desired=absent
  fi
  if test -f "$applied"; then
    if test "$desired" = absent; then
      test ! -e "$CURRENT_LINK" && test ! -L "$CURRENT_LINK"
    else
      test -L "$CURRENT_LINK" && test "$(readlink "$CURRENT_LINK")" = "$desired"
    fi
    return $?
  fi
  if test ! -e "$identity_file"; then
    if test -L "$CURRENT_LINK"; then
      current_identity=$(s72_atomic_node_identity "$CURRENT_LINK") || return 1
      s72_atomic_publish_new_file "$identity_file" 0400 "$current_identity" || return 1
    elif test -e "$CURRENT_LINK"; then
      return 1
    else
      s72_atomic_publish_new_file "$identity_file" 0400 absent || return 1
    fi
  fi
  expected=$(cat "$identity_file") || return 1
  if test "$expected" != absent && test ! -e "$old" && test ! -L "$old"; then
    s72_atomic_require_identity "$CURRENT_LINK" "$expected" || return 1
    mv -T -n "$CURRENT_LINK" "$old" || return 1
    s72_atomic_require_identity "$old" "$expected" || {
      test -e "$CURRENT_LINK" || test -L "$CURRENT_LINK" || mv -T -n "$old" "$CURRENT_LINK" || :
      return 1
    }
  elif test "$expected" = absent; then
    test ! -e "$CURRENT_LINK" && test ! -L "$CURRENT_LINK" || return 1
  fi
  if test "$desired" != absent; then
    if test ! -L "$new"; then
      test ! -e "$new" || return 1
      ln -s "$desired" "$new" || return 1
    fi
    if test -L "$CURRENT_LINK"; then
      test "$(readlink "$CURRENT_LINK")" = "$desired" || return 1
    else
      mv -T -n "$new" "$CURRENT_LINK" || return 1
      test -L "$CURRENT_LINK" && test "$(readlink "$CURRENT_LINK")" = "$desired" || return 1
    fi
  else
    test ! -e "$CURRENT_LINK" && test ! -L "$CURRENT_LINK" || return 1
  fi
  s72_atomic_fsync_path "${CURRENT_LINK%/*}" || return 1
  s72_atomic_publish_new_file "$applied" 0400 applied
}

s72_atomic_restore_snapshot() {
  snapshot=$1
  transaction_id=$2
  records=$3
  s72_atomic_is_transaction_id "$transaction_id" || return 1
  s72_atomic_preflight_snapshot "$snapshot" || return 1
  s72_atomic_verify_snapshot_seal "$snapshot" || return 1
  s72_atomic_load_transaction "$records" "$transaction_id" || return 1
  if test "$S72_TX_PHASE" = recovering; then
    S72_RESTORE_SCOPE=recovery
    S72_RECOVERY_APPLY_OPTIONAL=0
    test "$S72_TX_OPERATION" != install || S72_RECOVERY_APPLY_OPTIONAL=1
    preflight_recoverable_live "$S72_TX_RECOVERY_SNAPSHOT" "$S72_TX_APPLY_SNAPSHOT" || return 1
  else
    S72_RESTORE_SCOPE=apply
    preflight_live_state || return 1
  fi
  s72_atomic_require_exact_lifecycle || return 1
  s72_atomic_advance_transaction "$records" "$transaction_id" staged || return 1
  if test "$S72_RESTORE_SCOPE" = apply; then
    preflight_live_state || return 1
  else
    preflight_recoverable_live "$S72_TX_RECOVERY_SNAPSHOT" "$S72_TX_APPLY_SNAPSHOT" || return 1
  fi
  s72_atomic_verify_snapshot_seal "$snapshot" || return 1
  s72_atomic_advance_transaction "$records" "$transaction_id" stop-intent || return 1
  for unit in opensandbox-gateway-helper.service opensandbox-gateway.service; do
    active_state=$(systemctl show "$unit" -p ActiveState --value) || return 1
    case "$active_state" in
      active) systemctl stop "$unit" >/dev/null 2>&1 || return 1 ;;
      inactive) ;;
      *) return 1 ;;
    esac
    active_state=$(systemctl show "$unit" -p ActiveState --value) || return 1
    test "$active_state" = inactive || return 1
  done
  s72_atomic_advance_transaction "$records" "$transaction_id" stopped || return 1
  restore_snapshot_payload "$snapshot" "$transaction_id" || return 1
  s72_atomic_advance_transaction "$records" "$transaction_id" current-applied || return 1
  preflight_live_state || return 1
  s72_atomic_require_exact_lifecycle || return 1
  s72_atomic_advance_transaction "$records" "$transaction_id" revalidated || return 1
  restore_snapshot_runtime "$snapshot" || return 1
  preflight_live_state || return 1
  s72_atomic_require_exact_lifecycle || return 1
  s72_atomic_advance_transaction "$records" "$transaction_id" runtime-restored
}
