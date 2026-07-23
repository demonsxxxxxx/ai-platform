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
RUNTIME_STATE=/var/lib/opensandbox-gateway

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

require_exact_authority_head() {
  source_root=$1
  authority_ref=$2
  expected_authority=$3
  is_commit "$expected_authority" || return 1
  git -C "$source_root" show-ref --verify --quiet "refs/remotes/$authority_ref" || return 1
  head_commit=$(git -C "$source_root" rev-parse --verify 'HEAD^{commit}') || return 1
  authority_commit=$(git -C "$source_root" rev-parse --verify "refs/remotes/$authority_ref^{commit}") || return 1
  is_commit "$head_commit" || return 1
  is_commit "$authority_commit" || return 1
  test "$head_commit" = "$expected_authority" || return 1
  test "$authority_commit" = "$expected_authority" || return 1
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
  test "$(readlink -f "$source_root")" = "$source_root" || return 1
  require_root_tree "$source_root" || return 1
  git -C "$source_root" show-ref --verify --quiet "refs/remotes/$authority_ref" || return 1
  current_authority=$(git -C "$source_root" rev-parse --verify "refs/remotes/$authority_ref^{commit}") || return 1
  is_commit "$current_authority" || return 1
  case "$mode" in
    exact)
      test "$commit" = "$current_authority" || return 1
      test "$commit" = "$EXPECTED_AUTHORITY_SHA" || return 1
      test "$authority_evidence" = "$AUTHORITY_EVIDENCE_ID" || return 1
      ;;
    rollback) git -C "$source_root" merge-base --is-ancestor "$commit" "$current_authority" ;;
    *) return 1 ;;
  esac
}

require_marker_pair() {
  if test -f "$1"; then
    test ! -e "$2"
  else
    test -f "$2"
  fi
}

preflight_live_state() {
  test -d "$WORKSPACE_ROOT" && test ! -L "$WORKSPACE_ROOT" || return 1
  if test -e "$DEPLOY_STATE"; then
    test -d "$DEPLOY_STATE" && test ! -L "$DEPLOY_STATE" || return 1
    test "$(stat -c %u:%g:%a "$DEPLOY_STATE")" = 0:0:700 || return 1
  fi
  for unit in opensandbox-gateway.service opensandbox-gateway-helper.service; do
    if test -e "$SYSTEMD_DIR/$unit"; then
      test -f "$SYSTEMD_DIR/$unit" && test ! -L "$SYSTEMD_DIR/$unit" || return 1
      test "$(stat -c %u "$SYSTEMD_DIR/$unit")" -eq 0 || return 1
    fi
  done
  if test -e "$CONFIG_DIR"; then
    require_root_tree "$CONFIG_DIR" || return 1
  fi
  current_commit=
  if test -L "$CURRENT_LINK"; then
    current=$(readlink "$CURRENT_LINK")
    case "$current" in
      releases/*) current_commit=${current#releases/}; validate_release "$current_commit" rollback || return 1 ;;
      *) return 1 ;;
    esac
  elif test -e "$CURRENT_LINK"; then
    return 1
  fi
  if test -e "$AUTHORITY_SHA_STATE" || test -e "$AUTHORITY_EVIDENCE_STATE"; then
    test -f "$AUTHORITY_SHA_STATE" && test ! -L "$AUTHORITY_SHA_STATE" || return 1
    test -f "$AUTHORITY_EVIDENCE_STATE" && test ! -L "$AUTHORITY_EVIDENCE_STATE" || return 1
    test "$(stat -c %u:%g:%a "$AUTHORITY_SHA_STATE")" = 0:0:600 || return 1
    test "$(stat -c %u:%g:%a "$AUTHORITY_EVIDENCE_STATE")" = 0:0:600 || return 1
    authority_sha=$(cat "$AUTHORITY_SHA_STATE")
    authority_evidence=$(cat "$AUTHORITY_EVIDENCE_STATE")
    is_commit "$authority_sha" || return 1
    is_authority_evidence_id "$authority_evidence" || return 1
    test -n "$current_commit" && test "$authority_sha" = "$current_commit" || return 1
  else
    test -z "$current_commit" || return 1
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
    old_target=$(cat "$snapshot/current")
    case "$old_target" in releases/*) old_commit=${old_target#releases/} ;; *) return 1 ;; esac
    validate_release "$old_commit" rollback || return 1
    test -f "$snapshot/authority-sha" && test "$(cat "$snapshot/authority-sha")" = "$old_commit" || return 1
    test -f "$snapshot/authority-evidence" || return 1
  else
    test -f "$snapshot/authority-sha.absent" && test -f "$snapshot/authority-evidence.absent" || return 1
  fi
  if test -f "$snapshot/authority-sha"; then
    is_commit "$(cat "$snapshot/authority-sha")" || return 1
    is_authority_evidence_id "$(cat "$snapshot/authority-evidence")" || return 1
  fi
}

record_authority_state() {
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

snapshot_state() {
  snapshot=$1
  preflight_live_state
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
  if test -e "$AUTHORITY_SHA_STATE" || test -e "$AUTHORITY_EVIDENCE_STATE"; then
    test -f "$AUTHORITY_SHA_STATE" && test ! -L "$AUTHORITY_SHA_STATE"
    test -f "$AUTHORITY_EVIDENCE_STATE" && test ! -L "$AUTHORITY_EVIDENCE_STATE"
    test "$(stat -c %u:%g:%a "$AUTHORITY_SHA_STATE")" = 0:0:600
    test "$(stat -c %u:%g:%a "$AUTHORITY_EVIDENCE_STATE")" = 0:0:600
    authority_sha=$(cat "$AUTHORITY_SHA_STATE")
    authority_evidence=$(cat "$AUTHORITY_EVIDENCE_STATE")
    is_commit "$authority_sha"
    is_authority_evidence_id "$authority_evidence"
    printf '%s\n' "$authority_sha" > "$snapshot/authority-sha"
    printf '%s\n' "$authority_evidence" > "$snapshot/authority-evidence"
  else
    : > "$snapshot/authority-sha.absent"
    : > "$snapshot/authority-evidence.absent"
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
  preflight_snapshot "$snapshot"
}

restore_snapshot() {
  snapshot=$1
  preflight_snapshot "$snapshot" || return 1
  for unit in opensandbox-gateway.service opensandbox-gateway-helper.service; do
    if test -f "$snapshot/$unit.present"; then
      install -o root -g root -m 0644 "$snapshot/$unit" "$SYSTEMD_DIR/$unit" || return 1
    else
      rm -f "$SYSTEMD_DIR/$unit" || return 1
    fi
  done
  if test -f "$snapshot/config.present"; then
    rm -rf "$CONFIG_DIR" || return 1
    cp -a "$snapshot/etc-opensandbox-gateway" "$CONFIG_DIR" || return 1
  else
    rm -rf "$CONFIG_DIR" || return 1
  fi
  setfacl --restore="$snapshot/workspaces.acl" || return 1
  if test -f "$snapshot/authority-sha"; then
    authority_sha=$(cat "$snapshot/authority-sha")
    is_commit "$authority_sha"
    install -o root -g root -m 0600 "$snapshot/authority-sha" "$AUTHORITY_SHA_STATE" || return 1
    install -o root -g root -m 0600 "$snapshot/authority-evidence" "$AUTHORITY_EVIDENCE_STATE" || return 1
  elif test -f "$snapshot/authority-sha.absent"; then
    rm -f "$AUTHORITY_SHA_STATE" "$AUTHORITY_EVIDENCE_STATE" || return 1
  else
    return 1
  fi
  old_target=
  if test -f "$snapshot/current"; then
    old_target=$(cat "$snapshot/current")
    old_commit=${old_target#releases/}
    validate_release "$old_commit"
  fi
  systemctl daemon-reload || return 1
  for unit in opensandbox-gateway-helper.service opensandbox-gateway.service; do
    if test -f "$snapshot/$unit.enabled"; then
      systemctl enable "$unit" >/dev/null 2>&1 || return 1
    else
      systemctl disable "$unit" >/dev/null 2>&1 || true
    fi
    if test -f "$snapshot/$unit.active"; then
      systemctl restart "$unit" || return 1
    else
      systemctl stop "$unit" >/dev/null 2>&1 || true
    fi
  done
  if test -n "$old_target"; then
    ln -s "$old_target" "$CURRENT_LINK.restore" || return 1
    mv -Tf "$CURRENT_LINK.restore" "$CURRENT_LINK" || return 1
    test "$(readlink -f "$CURRENT_LINK")" = "$RELEASES/$old_commit"
  else
    rm -f "$CURRENT_LINK" || return 1
  fi
}

cleanup_install() {
  status=$?
  trap - EXIT HUP INT TERM
  if test "$SUCCESS" -eq 0; then
    set +e
    restore_snapshot "$RESTORE_FROM"
    restore_status=$?
    set -e
    if test "$restore_status" -ne 0; then
      printf '%s\n' "OpenSandbox gateway restore failed; preserved recovery snapshot: $RESTORE_FROM" >&2
      exit 125
    fi
  fi
  test -d "$STAGE" && rm -rf "$STAGE"
  test -d "$BACKUP" && rm -rf "$BACKUP"
  exit "$status"
}

install_main() {
SOURCE_ROOT=${1:?usage: install-s72.sh /path/to/root-owned-clean-ai-platform-clone}
test "$(id -u)" -eq 0
case "$AUTHORITY_REF" in ""|*[!A-Za-z0-9._/-]*|*..*) exit 1 ;; esac
is_commit "$EXPECTED_AUTHORITY_SHA"
is_authority_evidence_id "$AUTHORITY_EVIDENCE_ID"
SOURCE_REAL=$(readlink -f "$SOURCE_ROOT")
test "$SOURCE_REAL" = "$(cd "$SOURCE_ROOT" && pwd -P)"
require_root_tree "$SOURCE_REAL"
test "$(git -C "$SOURCE_REAL" rev-parse --show-toplevel)" = "$SOURCE_REAL"
SOURCE_COMMIT=$(git -C "$SOURCE_REAL" rev-parse --verify 'HEAD^{commit}')
is_commit "$SOURCE_COMMIT"
git -C "$SOURCE_REAL" diff-index --quiet HEAD --
test -z "$(git -C "$SOURCE_REAL" ls-files --others --exclude-standard)"
AUTHORITY_COMMIT=$(require_exact_authority_head "$SOURCE_REAL" "$AUTHORITY_REF" "$EXPECTED_AUTHORITY_SHA")
test "$SOURCE_COMMIT" = "$AUTHORITY_COMMIT"
test -f "$CONFIG_DIR/gateway.env"
test -f "$CONFIG_DIR/egress-policy.v1.json"
test -f "$CONFIG_DIR/tls/fullchain.pem"
test -f "$CONFIG_DIR/tls/privkey.pem"
require_root_tree "$CONFIG_DIR"
test "$(systemctl show opensandbox.service -p ActiveState --value)" = active
test "$(systemctl show opensandbox.service -p FragmentPath --value)" = /etc/systemd/system/opensandbox.service
ss -ltn | grep -q '127.0.0.1:8080'
preflight_live_state

install -d -o root -g root -m 0755 /opt/opensandbox-gateway "$RELEASES"
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
trap 'cleanup_install' EXIT HUP INT TERM

snapshot_state "$BACKUP"
getent group opensandbox-gateway >/dev/null 2>&1 || groupadd --system opensandbox-gateway
id opensandbox-gateway >/dev/null 2>&1 || useradd --system --gid opensandbox-gateway --home-dir /nonexistent --shell /usr/sbin/nologin opensandbox-gateway
install -d -o opensandbox-gateway -g opensandbox-gateway -m 0700 "$RUNTIME_STATE"
git -C "$SOURCE_REAL" archive "$SOURCE_COMMIT" services/opensandbox_gateway deploy/opensandbox | tar -x -C "$STAGE"
test -f "$STAGE/services/opensandbox_gateway/gateway.py"
test -z "$(find "$STAGE" -type l -print -quit)"
printf '%s\n' "$SOURCE_COMMIT" > "$STAGE/SOURCE_COMMIT"
printf '%s\n' "$SOURCE_REAL" > "$STAGE/SOURCE_ROOT"
printf '%s\n' "$AUTHORITY_REF" > "$STAGE/AUTHORITY_REF"
printf '%s\n' "$AUTHORITY_COMMIT" > "$STAGE/AUTHORITY_COMMIT"
printf '%s\n' "$AUTHORITY_EVIDENCE_ID" > "$STAGE/AUTHORITY_EVIDENCE_ID"
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
record_authority_state "$AUTHORITY_COMMIT" "$AUTHORITY_EVIDENCE_ID"
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
