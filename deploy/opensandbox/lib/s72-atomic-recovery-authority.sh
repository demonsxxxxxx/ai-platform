#!/bin/sh

S72_ATOMIC_RECOVERY_AUTHORITY_SCHEMA=s72-atomic-recovery-authority-v1

s72_atomic_is_commit() {
  test "${#1}" -eq 40 || return 1
  case "$1" in *[!0-9a-f]*) return 1 ;; esac
}

s72_atomic_is_authority_evidence_id() {
  test -n "$1" && test "${#1}" -le 128 || return 1
  case "$1" in *[!A-Za-z0-9._:-]*) return 1 ;; esac
}

s72_atomic_require_root_tree() {
  test -d "$1" && test ! -L "$1" || return 1
  test "$(stat -c %u "$1")" -eq 0 || return 1
  test -z "$(find "$1" -type l -print -quit)" || return 1
  test -z "$(find "$1" ! -user root -print -quit)"
}

s72_atomic_require_root_owned_regular() {
  path=$1
  mode=$2
  test -f "$path" && test ! -L "$path" || return 1
  test "$(stat -c %u "$path")" -eq 0 || return 1
  case "$(stat -c %G "$path")" in root|opensandbox-gateway) ;; *) return 1 ;; esac
  test "$(stat -c %a "$path")" = "$mode"
}

s72_atomic_require_root_owned_directory() {
  path=$1
  mode=$2
  test -d "$path" && test ! -L "$path" || return 1
  test "$(stat -c %u "$path")" -eq 0 || return 1
  case "$(stat -c %G "$path")" in root|opensandbox-gateway) ;; *) return 1 ;; esac
  test "$(stat -c %a "$path")" = "$mode"
}

s72_atomic_verify_manifest() {
  test -f "$1/MANIFEST.sha256" && test ! -L "$1/MANIFEST.sha256" || return 1
  (cd "$1" && sha256sum -c MANIFEST.sha256 >/dev/null)
}

s72_atomic_require_marker_pair() {
  if test -f "$1"; then
    test ! -e "$2"
  else
    test -f "$2"
  fi
}

s72_atomic_preflight_snapshot() {
  snapshot=$1
  require_root_tree "$snapshot" || return 1
  verify_manifest "$snapshot" || return 1
  for unit in opensandbox-gateway.service opensandbox-gateway-helper.service; do
    require_marker_pair "$snapshot/$unit.present" "$snapshot/$unit.absent" || return 1
    test ! -f "$snapshot/$unit.present" || test -f "$snapshot/$unit" || return 1
    require_marker_pair "$snapshot/$unit.active" "$snapshot/$unit.inactive" || return 1
    require_marker_pair "$snapshot/$unit.enabled" "$snapshot/$unit.disabled" || return 1
  done
  require_marker_pair "$snapshot/config.present" "$snapshot/config.absent" || return 1
  test ! -f "$snapshot/config.present" || test -d "$snapshot/etc-opensandbox-gateway" || return 1
  test -f "$snapshot/workspaces.acl" || return 1
  require_marker_pair "$snapshot/authority-sha" "$snapshot/authority-sha.absent" || return 1
  require_marker_pair "$snapshot/authority-evidence" "$snapshot/authority-evidence.absent" || return 1
  require_marker_pair "$snapshot/current" "$snapshot/current.absent" || return 1
  if test -f "$snapshot/current"; then
    s72_snapshot_target=$(cat "$snapshot/current")
    case "$s72_snapshot_target" in
      releases/*) s72_snapshot_commit=${s72_snapshot_target#releases/} ;;
      *) return 1 ;;
    esac
    validate_release "$s72_snapshot_commit" rollback || return 1
    test -f "$snapshot/authority-sha" && test "$(cat "$snapshot/authority-sha")" = "$s72_snapshot_commit" || return 1
    test -f "$snapshot/authority-evidence" || return 1
  else
    test -f "$snapshot/authority-sha.absent" && test -f "$snapshot/authority-evidence.absent" || return 1
  fi
  if test -f "$snapshot/authority-sha"; then
    is_commit "$(cat "$snapshot/authority-sha")" || return 1
    is_authority_evidence_id "$(cat "$snapshot/authority-evidence")" || return 1
  fi
}

s72_atomic_record_authority_state() {
  authority_sha=$1
  authority_evidence=$2
  is_commit "$authority_sha" || return 1
  is_authority_evidence_id "$authority_evidence" || return 1
  authority_tmp=$DEPLOY_STATE/.current-authority-sha.$$
  evidence_tmp=$DEPLOY_STATE/.current-authority-evidence.$$
  printf '%s\n' "$authority_sha" > "$authority_tmp"
  printf '%s\n' "$authority_evidence" > "$evidence_tmp"
  chown root:root "$authority_tmp" "$evidence_tmp"
  chmod 0600 "$authority_tmp" "$evidence_tmp"
  mv -f "$authority_tmp" "$AUTHORITY_SHA_STATE"
  mv -f "$evidence_tmp" "$AUTHORITY_EVIDENCE_STATE"
  test "$(cat "$AUTHORITY_SHA_STATE")" = "$authority_sha"
  test "$(cat "$AUTHORITY_EVIDENCE_STATE")" = "$authority_evidence"
}
