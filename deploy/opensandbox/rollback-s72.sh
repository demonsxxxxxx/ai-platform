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

is_commit() {
  test "${#1}" -eq 40 || return 1
  case "$1" in *[!0-9a-f]*) return 1 ;; esac
}

is_authority_evidence_id() {
  test -n "$1" && test "${#1}" -le 128 || return 1
  case "$1" in *[!A-Za-z0-9._:-]*) return 1 ;; esac
}

require_root_tree() {
  test -d "$1" && test ! -L "$1"
  test "$(stat -c %u "$1")" -eq 0
  test -z "$(find "$1" -type l -print -quit)"
  test -z "$(find "$1" ! -user root -print -quit)"
}

verify_manifest() {
  test -f "$1/MANIFEST.sha256" && test ! -L "$1/MANIFEST.sha256"
  (cd "$1" && sha256sum -c MANIFEST.sha256 >/dev/null)
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
  deployed_sha=$1
  authority_evidence=$2
  is_commit "$deployed_sha" || return 1
  is_authority_evidence_id "$authority_evidence" || return 1
  sha_tmp=$DEPLOY_STATE/.current-authority-sha.$$
  evidence_tmp=$DEPLOY_STATE/.current-authority-evidence.$$
  printf '%s\n' "$deployed_sha" > "$sha_tmp"
  printf '%s\n' "$authority_evidence" > "$evidence_tmp"
  chown root:root "$sha_tmp" "$evidence_tmp"
  chmod 0600 "$sha_tmp" "$evidence_tmp"
  mv -f "$sha_tmp" "$AUTHORITY_SHA_STATE"
  mv -f "$evidence_tmp" "$AUTHORITY_EVIDENCE_STATE"
  test "$(cat "$AUTHORITY_SHA_STATE")" = "$deployed_sha"
  test "$(cat "$AUTHORITY_EVIDENCE_STATE")" = "$authority_evidence"
}

require_marker_pair() {
  if test -f "$1"; then
    test ! -e "$2"
  else
    test -f "$2"
  fi
}

preflight_snapshot() {
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
    previous=$(cat "$snapshot/current")
    case "$previous" in releases/*) previous_commit=${previous#releases/} ;; *) return 1 ;; esac
    validate_release "$previous_commit" || return 1
    test -f "$snapshot/authority-sha" && test "$(cat "$snapshot/authority-sha")" = "$previous_commit" || return 1
    test -f "$snapshot/authority-evidence" || return 1
  else
    test -f "$snapshot/authority-sha.absent" && test -f "$snapshot/authority-evidence.absent" || return 1
  fi
  if test -f "$snapshot/authority-sha"; then
    is_commit "$(cat "$snapshot/authority-sha")" || return 1
    is_authority_evidence_id "$(cat "$snapshot/authority-evidence")" || return 1
  fi
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
