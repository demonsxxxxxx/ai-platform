#!/bin/sh
set -eu

AUTHORITY_REF=${OPENSANDBOX_GATEWAY_AUTHORITY_REF:-origin/main}
RELEASES=/opt/opensandbox-gateway/releases
CURRENT_LINK=/opt/opensandbox-gateway/current
DEPLOY_STATE=/var/lib/opensandbox-gateway-deploy
ROLLBACK_POINTER=$DEPLOY_STATE/previous-snapshot
AUTHORITY_SHA_STATE=$DEPLOY_STATE/current-authority-sha
SYSTEMD_DIR=/etc/systemd/system
CONFIG_DIR=/etc/opensandbox-gateway
WORKSPACE_ROOT=/data/opensandbox/workspaces
RUNTIME_STATE=/var/lib/opensandbox-gateway

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

require_exact_authority_head() {
  source_root=$1
  authority_ref=$2
  git -C "$source_root" show-ref --verify --quiet "refs/remotes/$authority_ref"
  head_commit=$(git -C "$source_root" rev-parse --verify 'HEAD^{commit}')
  authority_commit=$(git -C "$source_root" rev-parse --verify "refs/remotes/$authority_ref^{commit}")
  is_commit "$head_commit"
  is_commit "$authority_commit"
  test "$head_commit" = "$authority_commit" || return 1
  printf '%s\n' "$authority_commit"
}

write_manifest() {
  target=$1
  rm -f "$target/MANIFEST.sha256"
  (cd "$target" && find . -type f ! -name MANIFEST.sha256 -print0 | LC_ALL=C sort -z | xargs -0 sha256sum) > "$target/MANIFEST.sha256"
  chown root:root "$target/MANIFEST.sha256"
  chmod 0444 "$target/MANIFEST.sha256"
}

verify_manifest() {
  test -f "$1/MANIFEST.sha256" && test ! -L "$1/MANIFEST.sha256"
  (cd "$1" && sha256sum -c MANIFEST.sha256 >/dev/null)
}

validate_release() {
  commit=$1
  mode=${2:-rollback}
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
  case "$mode" in
    exact) test "$commit" = "$current_authority" ;;
    rollback) git -C "$source_root" merge-base --is-ancestor "$commit" "$current_authority" ;;
    *) return 1 ;;
  esac
}

snapshot_state() {
  snapshot=$1
  install -d -o root -g root -m 0700 "$snapshot"
  for unit in opensandbox-gateway.service opensandbox-gateway-helper.service; do
    if test -e "$SYSTEMD_DIR/$unit"; then
      test -f "$SYSTEMD_DIR/$unit" && test ! -L "$SYSTEMD_DIR/$unit"
      test "$(stat -c %u "$SYSTEMD_DIR/$unit")" -eq 0
      cp -a "$SYSTEMD_DIR/$unit" "$snapshot/$unit"
      : > "$snapshot/$unit.present"
    else
      : > "$snapshot/$unit.absent"
    fi
    systemctl is-active --quiet "$unit" && : > "$snapshot/$unit.active" || : > "$snapshot/$unit.inactive"
    systemctl is-enabled --quiet "$unit" && : > "$snapshot/$unit.enabled" || : > "$snapshot/$unit.disabled"
  done
  if test -e "$CONFIG_DIR"; then
    require_root_tree "$CONFIG_DIR"
    cp -a "$CONFIG_DIR" "$snapshot/etc-opensandbox-gateway"
    : > "$snapshot/config.present"
  else
    : > "$snapshot/config.absent"
  fi
  getfacl -p "$WORKSPACE_ROOT" > "$snapshot/workspaces.acl"
  if test -e "$AUTHORITY_SHA_STATE"; then
    test -f "$AUTHORITY_SHA_STATE" && test ! -L "$AUTHORITY_SHA_STATE"
    test "$(stat -c %u:%g:%a "$AUTHORITY_SHA_STATE")" = 0:0:600
    authority_sha=$(cat "$AUTHORITY_SHA_STATE")
    is_commit "$authority_sha"
    printf '%s\n' "$authority_sha" > "$snapshot/authority-sha"
  else
    : > "$snapshot/authority-sha.absent"
  fi
  if test -L "$CURRENT_LINK"; then
    current=$(readlink "$CURRENT_LINK")
    case "$current" in releases/*) current_commit=${current#releases/}; validate_release "$current_commit" ;; *) return 1 ;; esac
    printf '%s\n' "$current" > "$snapshot/current"
  elif test -e "$CURRENT_LINK"; then
    return 1
  else
    : > "$snapshot/current.absent"
  fi
  chown -R root:root "$snapshot"
  write_manifest "$snapshot"
  require_root_tree "$snapshot"
  verify_manifest "$snapshot"
}

restore_snapshot() {
  snapshot=$1
  require_root_tree "$snapshot"
  verify_manifest "$snapshot"
  for unit in opensandbox-gateway.service opensandbox-gateway-helper.service; do
    if test -f "$snapshot/$unit.present"; then
      install -o root -g root -m 0644 "$snapshot/$unit" "$SYSTEMD_DIR/$unit"
    else
      rm -f "$SYSTEMD_DIR/$unit"
    fi
  done
  if test -f "$snapshot/config.present"; then
    rm -rf "$CONFIG_DIR"
    cp -a "$snapshot/etc-opensandbox-gateway" "$CONFIG_DIR"
  else
    rm -rf "$CONFIG_DIR"
  fi
  setfacl --restore="$snapshot/workspaces.acl"
  if test -f "$snapshot/authority-sha"; then
    authority_sha=$(cat "$snapshot/authority-sha")
    is_commit "$authority_sha"
    install -o root -g root -m 0600 "$snapshot/authority-sha" "$AUTHORITY_SHA_STATE"
  elif test -f "$snapshot/authority-sha.absent"; then
    rm -f "$AUTHORITY_SHA_STATE"
  else
    return 1
  fi
  old_target=
  if test -f "$snapshot/current"; then
    old_target=$(cat "$snapshot/current")
    old_commit=${old_target#releases/}
    validate_release "$old_commit"
  fi
  systemctl daemon-reload
  for unit in opensandbox-gateway-helper.service opensandbox-gateway.service; do
    if test -f "$snapshot/$unit.enabled"; then
      systemctl enable "$unit" >/dev/null 2>&1
    else
      systemctl disable "$unit" >/dev/null 2>&1 || true
    fi
    if test -f "$snapshot/$unit.active"; then
      systemctl restart "$unit"
    else
      systemctl stop "$unit" >/dev/null 2>&1 || true
    fi
  done
  if test -n "$old_target"; then
    ln -s "$old_target" "$CURRENT_LINK.restore"
    mv -Tf "$CURRENT_LINK.restore" "$CURRENT_LINK"
    test "$(readlink -f "$CURRENT_LINK")" = "$RELEASES/$old_commit"
  else
    rm -f "$CURRENT_LINK"
  fi
}

install_main() {
SOURCE_ROOT=${1:?usage: install-s72.sh /path/to/root-owned-clean-ai-platform-clone}
test "$(id -u)" -eq 0
case "$AUTHORITY_REF" in ""|*[!A-Za-z0-9._/-]*|*..*) exit 1 ;; esac
SOURCE_REAL=$(readlink -f "$SOURCE_ROOT")
test "$SOURCE_REAL" = "$(cd "$SOURCE_ROOT" && pwd -P)"
require_root_tree "$SOURCE_REAL"
test "$(git -C "$SOURCE_REAL" rev-parse --show-toplevel)" = "$SOURCE_REAL"
SOURCE_COMMIT=$(git -C "$SOURCE_REAL" rev-parse --verify 'HEAD^{commit}')
is_commit "$SOURCE_COMMIT"
git -C "$SOURCE_REAL" diff-index --quiet HEAD --
test -z "$(git -C "$SOURCE_REAL" ls-files --others --exclude-standard)"
AUTHORITY_COMMIT=$(require_exact_authority_head "$SOURCE_REAL" "$AUTHORITY_REF")
test "$SOURCE_COMMIT" = "$AUTHORITY_COMMIT"
test -f "$CONFIG_DIR/gateway.env"
test -f "$CONFIG_DIR/egress-policy.v1.json"
test -f "$CONFIG_DIR/tls/fullchain.pem"
test -f "$CONFIG_DIR/tls/privkey.pem"
require_root_tree "$CONFIG_DIR"
test "$(systemctl show opensandbox.service -p ActiveState --value)" = active
test "$(systemctl show opensandbox.service -p FragmentPath --value)" = /etc/systemd/system/opensandbox.service
ss -ltn | grep -q '127.0.0.1:8080'

getent group opensandbox-gateway >/dev/null 2>&1 || groupadd --system opensandbox-gateway
id opensandbox-gateway >/dev/null 2>&1 || useradd --system --gid opensandbox-gateway --home-dir /nonexistent --shell /usr/sbin/nologin opensandbox-gateway
install -d -o root -g root -m 0755 /opt/opensandbox-gateway "$RELEASES"
install -d -o opensandbox-gateway -g opensandbox-gateway -m 0700 "$RUNTIME_STATE"
install -d -o root -g root -m 0700 "$DEPLOY_STATE" "$DEPLOY_STATE/snapshots"
test "$(stat -c %u:%g:%a "$DEPLOY_STATE")" = 0:0:700
exec 9>"$DEPLOY_STATE/install.lock"
flock -n 9

RELEASE_ROOT=$RELEASES/$SOURCE_COMMIT
test ! -e "$RELEASE_ROOT"
STAGE=$(mktemp -d "$RELEASES/.stage.XXXXXX")
BACKUP=$(mktemp -d "$DEPLOY_STATE/.rollback.XXXXXX")
RESTORE_FROM=$BACKUP
SUCCESS=0
rollback_install() {
  test "$SUCCESS" -eq 0 || return 0
  restore_snapshot "$RESTORE_FROM" || true
}
cleanup_install() {
  rollback_install
  test -d "$STAGE" && rm -rf "$STAGE"
  test -d "$BACKUP" && rm -rf "$BACKUP"
}
trap 'cleanup_install' EXIT HUP INT TERM

snapshot_state "$BACKUP"
git -C "$SOURCE_REAL" archive "$SOURCE_COMMIT" services/opensandbox_gateway deploy/opensandbox | tar -x -C "$STAGE"
test -f "$STAGE/services/opensandbox_gateway/gateway.py"
test -z "$(find "$STAGE" -type l -print -quit)"
printf '%s\n' "$SOURCE_COMMIT" > "$STAGE/SOURCE_COMMIT"
printf '%s\n' "$SOURCE_REAL" > "$STAGE/SOURCE_ROOT"
printf '%s\n' "$AUTHORITY_REF" > "$STAGE/AUTHORITY_REF"
printf '%s\n' "$AUTHORITY_COMMIT" > "$STAGE/AUTHORITY_COMMIT"
install -d -o root -g opensandbox-gateway -m 0750 "$STAGE/config"
install -o root -g opensandbox-gateway -m 0640 "$CONFIG_DIR/gateway.env" "$STAGE/config/gateway.env"
install -o root -g opensandbox-gateway -m 0640 "$CONFIG_DIR/egress-policy.v1.json" "$STAGE/config/egress-policy.v1.json"
sed -i "s#/etc/opensandbox-gateway/egress-policy.v1.json#$RELEASE_ROOT/config/egress-policy.v1.json#g" "$STAGE/config/gateway.env"
sed "s#/opt/opensandbox-gateway/current#$RELEASE_ROOT#g;s#EnvironmentFile=/etc/opensandbox-gateway/gateway.env#EnvironmentFile=$RELEASE_ROOT/config/gateway.env#g" \
  "$STAGE/deploy/opensandbox/opensandbox-gateway.service" > "$STAGE/config/opensandbox-gateway.service"
sed "s#/opt/opensandbox-gateway/current#$RELEASE_ROOT#g" \
  "$STAGE/deploy/opensandbox/opensandbox-gateway-helper.service" > "$STAGE/config/opensandbox-gateway-helper.service"
chown -R root:root "$STAGE"
chown -R root:opensandbox-gateway "$STAGE/config"
find "$STAGE" -type d -exec chmod go-w {} +
find "$STAGE" -type f -exec chmod go-w {} +
write_manifest "$STAGE"
require_root_tree "$STAGE"
verify_manifest "$STAGE"
mv "$STAGE" "$RELEASE_ROOT"
STAGE=$RELEASE_ROOT
validate_release "$SOURCE_COMMIT" exact

install -o root -g root -m 0644 "$RELEASE_ROOT/config/opensandbox-gateway.service" "$SYSTEMD_DIR/opensandbox-gateway.service"
install -o root -g root -m 0644 "$RELEASE_ROOT/config/opensandbox-gateway-helper.service" "$SYSTEMD_DIR/opensandbox-gateway-helper.service"
chown -R root:opensandbox-gateway "$CONFIG_DIR"
chmod 0750 "$CONFIG_DIR" "$CONFIG_DIR/secrets" "$CONFIG_DIR/tls"
chmod 0640 "$CONFIG_DIR/gateway.env" "$CONFIG_DIR/egress-policy.v1.json" "$CONFIG_DIR/tls/fullchain.pem"
chmod 0440 "$CONFIG_DIR"/secrets/* "$CONFIG_DIR/tls/privkey.pem"
setfacl -m u:opensandbox-gateway:rwx,d:u:opensandbox-gateway:rwx "$WORKSPACE_ROOT"
systemctl daemon-reload
systemctl enable opensandbox-gateway-helper.service opensandbox-gateway.service
systemctl restart opensandbox-gateway-helper.service opensandbox-gateway.service
test "$(systemctl show opensandbox-gateway.service -p WorkingDirectory --value)" = "$RELEASE_ROOT"
test "$(systemctl show opensandbox-gateway-helper.service -p WorkingDirectory --value)" = "$RELEASE_ROOT"
validate_release "$SOURCE_COMMIT" exact
systemctl is-active --quiet opensandbox-gateway-helper.service
systemctl is-active --quiet opensandbox-gateway.service

SNAPSHOT_ID=$(basename "$BACKUP")
case "$SNAPSHOT_ID" in .rollback.[A-Za-z0-9]*) ;; *) exit 1 ;; esac
SNAPSHOT=$DEPLOY_STATE/snapshots/$SNAPSHOT_ID
mv "$BACKUP" "$SNAPSHOT"
BACKUP=$SNAPSHOT
RESTORE_FROM=$SNAPSHOT
ln -s "releases/$SOURCE_COMMIT" "$CURRENT_LINK.next"
mv -Tf "$CURRENT_LINK.next" "$CURRENT_LINK"
test "$(readlink -f "$CURRENT_LINK")" = "$RELEASE_ROOT"
AUTHORITY_TMP=$DEPLOY_STATE/.current-authority-sha.$$
printf '%s\n' "$AUTHORITY_COMMIT" > "$AUTHORITY_TMP"
chown root:root "$AUTHORITY_TMP"
chmod 0600 "$AUTHORITY_TMP"
mv -f "$AUTHORITY_TMP" "$AUTHORITY_SHA_STATE"
test "$(cat "$AUTHORITY_SHA_STATE")" = "$AUTHORITY_COMMIT"
POINTER_TMP=$DEPLOY_STATE/.previous-snapshot.$$
printf '%s\n' "$SNAPSHOT_ID" > "$POINTER_TMP"
chown root:root "$POINTER_TMP"
chmod 0600 "$POINTER_TMP"
mv -f "$POINTER_TMP" "$ROLLBACK_POINTER"
SUCCESS=1
BACKUP=
STAGE=
trap - EXIT HUP INT TERM
}

install_main "$@"
