#!/bin/sh
set -eu

AUTHORITY_REF=${OPENSANDBOX_GATEWAY_AUTHORITY_REF:-origin/main}
EXPECTED_AUTHORITY_SHA=${OPENSANDBOX_GATEWAY_EXPECTED_AUTHORITY_SHA:-}
AUTHORITY_EVIDENCE_ID=${OPENSANDBOX_GATEWAY_AUTHORITY_EVIDENCE_ID:-}
RELEASES=/opt/opensandbox-gateway/releases
CURRENT_LINK=/opt/opensandbox-gateway/current
DEPLOY_STATE=/var/lib/opensandbox-gateway-deploy
ROLLBACK_POINTER=$DEPLOY_STATE/previous-snapshot
AUTHORITY_SHA_STATE=$DEPLOY_STATE/current-authority-sha
AUTHORITY_EVIDENCE_STATE=$DEPLOY_STATE/current-authority-evidence
SYSTEMD_DIR=/etc/systemd/system
CONFIG_DIR=/etc/opensandbox-gateway
WORKSPACE_ROOT=/data/opensandbox/workspaces

s72_entrypoint_source=$0
case "$(basename -- "$s72_entrypoint_source")" in
  rollback-s72.sh) ;;
  *)
    test ! -e "$s72_entrypoint_source" || exit 1
    test -n "${SCRIPT:-}" && test -f "$SCRIPT" || exit 1
    s72_entrypoint_source=$SCRIPT
    ;;
esac
S72_LIB_DIR=$(dirname -- "$s72_entrypoint_source")/lib
. "$S72_LIB_DIR/s72-atomic-recovery-authority.sh"
unset s72_entrypoint_source

is_commit() {
  s72_atomic_is_commit "$@"
}

is_authority_evidence_id() {
  s72_atomic_is_authority_evidence_id "$@"
}

require_root_tree() {
  s72_atomic_require_root_tree "$@"
}

verify_manifest() {
  s72_atomic_verify_manifest "$@"
}

validate_release() {
  commit=$1
  mode=${2:-rollback}
  is_commit "$EXPECTED_AUTHORITY_SHA" || return 1
  is_authority_evidence_id "$AUTHORITY_EVIDENCE_ID" || return 1
  is_commit "$commit" || return 1
  release=$RELEASES/$commit
  test "$(readlink -f "$release")" = "$(readlink -f "$RELEASES")/$commit" || return 1
  require_root_tree "$release" || return 1
  test "$(cat "$release/SOURCE_COMMIT")" = "$commit" || return 1
  verify_manifest "$release" || return 1
  source_root=$(cat "$release/SOURCE_ROOT") || return 1
  authority_ref=$(cat "$release/AUTHORITY_REF") || return 1
  authority_commit=$(cat "$release/AUTHORITY_COMMIT") || return 1
  authority_evidence=$(cat "$release/AUTHORITY_EVIDENCE_ID") || return 1
  is_commit "$authority_commit" || return 1
  is_authority_evidence_id "$authority_evidence" || return 1
  test "$authority_commit" = "$commit" || return 1
  test "$authority_ref" = "$AUTHORITY_REF" || return 1
  test "$(readlink -f "$source_root")" = "$source_root" || return 1
  require_root_tree "$source_root" || return 1
  git -C "$source_root" show-ref --verify --quiet "refs/remotes/$authority_ref" || return 1
  current_authority=$(git -C "$source_root" rev-parse --verify "refs/remotes/$AUTHORITY_REF^{commit}") || return 1
  is_commit "$current_authority" || return 1
  test "$current_authority" = "$EXPECTED_AUTHORITY_SHA" || return 1
  git -C "$source_root" cat-file -e "$EXPECTED_AUTHORITY_SHA^{commit}" || return 1
  case "$mode" in
    rollback) git -C "$source_root" merge-base --is-ancestor "$commit" "$EXPECTED_AUTHORITY_SHA" ;;
    *) return 1 ;;
  esac
}

record_authority_state() {
  s72_atomic_record_authority_state "$@"
}

require_marker_pair() {
  s72_atomic_require_marker_pair "$@"
}

preflight_snapshot() {
  s72_atomic_preflight_snapshot "$@"
}

rollback_main() {
test "$(id -u)" -eq 0
case "$AUTHORITY_REF" in ""|*[!A-Za-z0-9._/-]*|*..*) exit 1 ;; esac
is_commit "$EXPECTED_AUTHORITY_SHA"
is_authority_evidence_id "$AUTHORITY_EVIDENCE_ID"
test "$(stat -c %u:%g:%a "$DEPLOY_STATE")" = 0:0:700
test -f "$ROLLBACK_POINTER" && test ! -L "$ROLLBACK_POINTER"
test "$(stat -c %u:%g:%a "$ROLLBACK_POINTER")" = 0:0:600
exec 9>"$DEPLOY_STATE/install.lock"
flock -n 9
test -L "$CURRENT_LINK"
CURRENT_TARGET=$(readlink "$CURRENT_LINK")
case "$CURRENT_TARGET" in releases/*) CURRENT_COMMIT=${CURRENT_TARGET#releases/} ;; *) exit 1 ;; esac
validate_release "$CURRENT_COMMIT" rollback
SNAPSHOT_ID=$(cat "$ROLLBACK_POINTER")
case "$SNAPSHOT_ID" in .rollback.[A-Za-z0-9]*) ;; *) exit 1 ;; esac
SNAPSHOT=$DEPLOY_STATE/snapshots/$SNAPSHOT_ID
test "$(readlink -f "$SNAPSHOT")" = "$(readlink -f "$DEPLOY_STATE/snapshots")/$SNAPSHOT_ID"
require_root_tree "$SNAPSHOT"
verify_manifest "$SNAPSHOT"
preflight_snapshot "$SNAPSHOT"

PREVIOUS=
if test -f "$SNAPSHOT/current"; then
  PREVIOUS=$(cat "$SNAPSHOT/current")
  previous_commit=${PREVIOUS#releases/}
fi

for unit in opensandbox-gateway.service opensandbox-gateway-helper.service; do
  if test -f "$SNAPSHOT/$unit.present"; then
    install -o root -g root -m 0644 "$SNAPSHOT/$unit" "$SYSTEMD_DIR/$unit"
  else
    rm -f "$SYSTEMD_DIR/$unit"
  fi
done
if test -f "$SNAPSHOT/config.present"; then
  rm -rf "$CONFIG_DIR"
  cp -a "$SNAPSHOT/etc-opensandbox-gateway" "$CONFIG_DIR"
else
  rm -rf "$CONFIG_DIR"
fi
setfacl --restore="$SNAPSHOT/workspaces.acl"
if test -f "$SNAPSHOT/authority-sha"; then
  authority_sha=$(cat "$SNAPSHOT/authority-sha")
  is_commit "$authority_sha"
  install -o root -g root -m 0600 "$SNAPSHOT/authority-sha" "$AUTHORITY_SHA_STATE"
  install -o root -g root -m 0600 "$SNAPSHOT/authority-evidence" "$AUTHORITY_EVIDENCE_STATE"
elif test -f "$SNAPSHOT/authority-sha.absent"; then
  rm -f "$AUTHORITY_SHA_STATE" "$AUTHORITY_EVIDENCE_STATE"
else
  exit 1
fi
systemctl daemon-reload
for unit in opensandbox-gateway-helper.service opensandbox-gateway.service; do
  if test -f "$SNAPSHOT/$unit.enabled"; then
    systemctl enable "$unit" >/dev/null 2>&1
  else
    systemctl disable "$unit" >/dev/null 2>&1 || true
  fi
  if test -f "$SNAPSHOT/$unit.active"; then
    systemctl restart "$unit"
  else
    systemctl stop "$unit" >/dev/null 2>&1 || true
  fi
done
if test -n "$PREVIOUS"; then
  ln -s "$PREVIOUS" "$CURRENT_LINK.next"
  mv -Tf "$CURRENT_LINK.next" "$CURRENT_LINK"
  test "$(readlink -f "$CURRENT_LINK")" = "$RELEASES/$previous_commit"
  record_authority_state "$previous_commit" "$AUTHORITY_EVIDENCE_ID"
else
  rm -f "$CURRENT_LINK"
fi
systemctl is-active --quiet opensandbox.service
ss -ltn | grep -q '127.0.0.1:8080'
}

rollback_main "$@"

# Docker provider configuration is never modified by deployment or rollback.
