#!/bin/sh
set -eu

RELEASES=/opt/opensandbox-gateway/releases
CURRENT_LINK=/opt/opensandbox-gateway/current
DEPLOY_STATE=/var/lib/opensandbox-gateway-deploy
ROLLBACK_POINTER=$DEPLOY_STATE/previous-snapshot
AUTHORITY_SHA_STATE=$DEPLOY_STATE/current-authority-sha
SYSTEMD_DIR=/etc/systemd/system
CONFIG_DIR=/etc/opensandbox-gateway
WORKSPACE_ROOT=/data/opensandbox/workspaces

is_commit() {
  test "${#1}" -eq 40 || return 1
  case "$1" in *[!0-9a-f]*) return 1 ;; esac
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
  is_commit "$commit"
  release=$RELEASES/$commit
  test "$(readlink -f "$release")" = "$(readlink -f "$RELEASES")/$commit"
  require_root_tree "$release"
  test "$(cat "$release/SOURCE_COMMIT")" = "$commit"
  verify_manifest "$release"
  source_root=$(cat "$release/SOURCE_ROOT")
  authority_ref=$(cat "$release/AUTHORITY_REF")
  authority_commit=$(cat "$release/AUTHORITY_COMMIT")
  is_commit "$authority_commit"
  test "$authority_commit" = "$commit"
  test "$(readlink -f "$source_root")" = "$source_root"
  require_root_tree "$source_root"
  git -C "$source_root" show-ref --verify --quiet "refs/remotes/$authority_ref"
  current_authority=$(git -C "$source_root" rev-parse --verify "refs/remotes/$authority_ref^{commit}")
  is_commit "$current_authority"
  git -C "$source_root" merge-base --is-ancestor "$commit" "$current_authority"
}

rollback_main() {
test "$(id -u)" -eq 0
test "$(stat -c %u:%g:%a "$DEPLOY_STATE")" = 0:0:700
test -f "$ROLLBACK_POINTER" && test ! -L "$ROLLBACK_POINTER"
test "$(stat -c %u:%g:%a "$ROLLBACK_POINTER")" = 0:0:600
exec 9>"$DEPLOY_STATE/install.lock"
flock -n 9
SNAPSHOT_ID=$(cat "$ROLLBACK_POINTER")
case "$SNAPSHOT_ID" in .rollback.[A-Za-z0-9]*) ;; *) exit 1 ;; esac
SNAPSHOT=$DEPLOY_STATE/snapshots/$SNAPSHOT_ID
test "$(readlink -f "$SNAPSHOT")" = "$(readlink -f "$DEPLOY_STATE/snapshots")/$SNAPSHOT_ID"
require_root_tree "$SNAPSHOT"
verify_manifest "$SNAPSHOT"

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
elif test -f "$SNAPSHOT/authority-sha.absent"; then
  rm -f "$AUTHORITY_SHA_STATE"
else
  exit 1
fi
PREVIOUS=
if test -f "$SNAPSHOT/current"; then
  previous=$(cat "$SNAPSHOT/current")
  case "$previous" in releases/*) previous_commit=${previous#releases/} ;; *) exit 1 ;; esac
  validate_release "$previous_commit"
  PREVIOUS=$previous
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
else
  rm -f "$CURRENT_LINK"
fi
systemctl is-active --quiet opensandbox.service
ss -ltn | grep -q '127.0.0.1:8080'
}

rollback_main "$@"

# Docker provider configuration is never modified by deployment or rollback.
