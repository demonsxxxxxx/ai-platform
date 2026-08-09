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

S72_ATOMIC_RECOVERY_HELPER_SHA256=1154d476f349298212dc63312a48f2da21e1318750749b61f569bd03761fbb49

s72_loader_reject() {
  printf '%s\n' 'OpenSandbox s72 loader authority rejected' >&2
  exit 126
}

s72_loader_require_canonical_regular() {
  s72_loader_path=$1
  case "$s72_loader_path" in /*) ;; *) return 1 ;; esac
  test -f "$s72_loader_path" && test ! -L "$s72_loader_path" || return 1
  s72_loader_canonical=$(/usr/bin/readlink -f -- "$s72_loader_path") || return 1
  test "$s72_loader_canonical" = "$s72_loader_path"
}

s72_loader_identity() {
  /usr/bin/stat -c '%d:%i:%f:%u:%g:%a:%s:%Y:%Z' -- "$1"
}

s72_loader_capture_helper_content() {
  s72_loader_capture_path=$1
  s72_loader_capture_identity=$2
  s72_loader_capture_digest=$3
  exec 8<"$s72_loader_capture_path" || return 1
  s72_loader_descriptor_identity=$(
    /usr/bin/stat -Lc '%d:%i:%f:%u:%g:%a:%s:%Y:%Z' -- /dev/fd/8
  ) || {
    exec 8<&-
    return 1
  }
  if test "$s72_loader_descriptor_identity" != "$s72_loader_capture_identity"; then
    exec 8<&-
    return 1
  fi
  s72_loader_helper_content=$(
    /usr/bin/cat <&8 || exit $?
    printf '%s' __S72_ATOMIC_RECOVERY_HELPER_EOF__
  ) || {
    exec 8<&-
    return 1
  }
  exec 8<&-
  s72_loader_helper_content=${s72_loader_helper_content%__S72_ATOMIC_RECOVERY_HELPER_EOF__}
  s72_loader_content_digest=$(printf '%s' "$s72_loader_helper_content" | /usr/bin/sha256sum) || return 1
  s72_loader_content_digest=${s72_loader_content_digest%% *}
  test "$s72_loader_content_digest" = "$s72_loader_capture_digest"
}

s72_loader_require_root_nonwritable() {
  s72_loader_node=$1
  test ! -L "$s72_loader_node" || return 1
  test "$(/usr/bin/stat -c %u -- "$s72_loader_node")" -eq 0 || return 1
  s72_loader_mode=$(/usr/bin/stat -c %a -- "$s72_loader_node") || return 1
  case "$s72_loader_mode" in ""|*[!0-7]*) return 1 ;; esac
  test $((0$s72_loader_mode & 0022)) -eq 0
}

s72_loader_require_privileged_chain() {
  s72_loader_entry=$1
  s72_loader_helper=$2
  s72_loader_require_root_nonwritable "$s72_loader_entry" || return 1
  s72_loader_require_root_nonwritable "$s72_loader_helper" || return 1
  s72_loader_node=${s72_loader_helper%/*}
  while :; do
    test -d "$s72_loader_node" && test ! -L "$s72_loader_node" || return 1
    s72_loader_require_root_nonwritable "$s72_loader_node" || return 1
    test "$s72_loader_node" = / && break
    s72_loader_node=${s72_loader_node%/*}
    test -n "$s72_loader_node" || s72_loader_node=/
  done
}

s72_loader_script_is_exported() {
  /usr/bin/env | /usr/bin/grep -q '^SCRIPT='
}

s72_loader_require_test_checkout_entry() {
  s72_loader_test_entry=$1
  s72_loader_test_relative=$2
  if test -x /usr/bin/git; then
    s72_loader_git=/usr/bin/git
  elif test -x /mingw64/bin/git.exe; then
    s72_loader_git=/mingw64/bin/git.exe
  else
    return 1
  fi
  s72_loader_repo=$($s72_loader_git -C "${s72_loader_test_entry%/*}" rev-parse --show-toplevel) || return 1
  if test -x /usr/bin/cygpath.exe; then
    s72_loader_repo=$(/usr/bin/cygpath.exe -u "$s72_loader_repo") || return 1
  fi
  s72_loader_repo=$(/usr/bin/readlink -f -- "$s72_loader_repo") || return 1
  test "$s72_loader_test_entry" = "$s72_loader_repo/$s72_loader_test_relative" || return 1
  $s72_loader_git -C "$s72_loader_repo" ls-files --error-unmatch -- "$s72_loader_test_relative" >/dev/null 2>&1
}

s72_loader_mode=production
case "$0" in
  /*)
    test "${SCRIPT+x}" != x || s72_loader_reject
    s72_loader_entrypoint=$0
    ;;
  gateway-contract|s72-atomic-contract)
    test "${SCRIPT+x}" = x || s72_loader_reject
    s72_loader_script_is_exported && s72_loader_reject
    s72_loader_mode=test-source-eval
    s72_loader_entrypoint=$SCRIPT
    ;;
  *) s72_loader_reject ;;
esac

s72_loader_require_canonical_regular "$s72_loader_entrypoint" || s72_loader_reject
test "${s72_loader_entrypoint##*/}" = install-s72.sh || s72_loader_reject
if test "$s72_loader_mode" = test-source-eval; then
  s72_loader_require_test_checkout_entry \
    "$s72_loader_entrypoint" deploy/opensandbox/install-s72.sh || s72_loader_reject
fi
S72_LIB_DIR=${s72_loader_entrypoint%/*}/lib
s72_loader_helper=$S72_LIB_DIR/s72-atomic-recovery-authority.sh
s72_loader_require_canonical_regular "$s72_loader_helper" || s72_loader_reject
if test "$s72_loader_mode" = production; then
  s72_loader_require_privileged_chain "$s72_loader_entrypoint" "$s72_loader_helper" || s72_loader_reject
fi
s72_loader_entry_identity=$(s72_loader_identity "$s72_loader_entrypoint") || s72_loader_reject
s72_loader_helper_identity=$(s72_loader_identity "$s72_loader_helper") || s72_loader_reject
s72_loader_helper_digest=$(/usr/bin/sha256sum -- "$s72_loader_helper") || s72_loader_reject
s72_loader_helper_digest=${s72_loader_helper_digest%% *}
test "$s72_loader_helper_digest" = "$S72_ATOMIC_RECOVERY_HELPER_SHA256" || s72_loader_reject
s72_loader_capture_helper_content \
  "$s72_loader_helper" "$s72_loader_helper_identity" "$S72_ATOMIC_RECOVERY_HELPER_SHA256" || s72_loader_reject
test "$(s72_loader_identity "$s72_loader_helper")" = "$s72_loader_helper_identity" || s72_loader_reject
eval "$s72_loader_helper_content"
unset s72_loader_helper_content
test "$(s72_loader_identity "$s72_loader_entrypoint")" = "$s72_loader_entry_identity" || s72_loader_reject
test "$(s72_loader_identity "$s72_loader_helper")" = "$s72_loader_helper_identity" || s72_loader_reject
s72_loader_helper_digest=$(/usr/bin/sha256sum -- "$s72_loader_helper") || s72_loader_reject
s72_loader_helper_digest=${s72_loader_helper_digest%% *}
test "$s72_loader_helper_digest" = "$S72_ATOMIC_RECOVERY_HELPER_SHA256" || s72_loader_reject
test "${S72_ATOMIC_RECOVERY_AUTHORITY_SCHEMA:-}" = s72-atomic-recovery-authority-v1 || s72_loader_reject
for s72_loader_symbol in \
  s72_atomic_is_commit \
  s72_atomic_is_authority_evidence_id \
  s72_atomic_require_root_tree \
  s72_atomic_require_root_owned_regular \
  s72_atomic_require_root_owned_directory \
  s72_atomic_verify_manifest \
  s72_atomic_require_marker_pair \
  s72_atomic_preflight_snapshot \
  s72_atomic_record_authority_state; do
  command -v "$s72_loader_symbol" >/dev/null 2>&1 || s72_loader_reject
done

is_commit() {
  s72_atomic_is_commit "$@"
}

is_authority_evidence_id() {
  s72_atomic_is_authority_evidence_id "$@"
}

require_root_tree() {
  s72_atomic_require_root_tree "$@"
}

require_root_owned_regular() {
  s72_atomic_require_root_owned_regular "$@"
}

require_root_owned_directory() {
  s72_atomic_require_root_owned_directory "$@"
}

require_gateway_config_contract() {
  require_root_owned_directory "$CONFIG_DIR" 750 || return 1
  require_root_owned_directory "$CONFIG_DIR/secrets" 750 || return 1
  require_root_owned_directory "$CONFIG_DIR/tls" 750 || return 1
  test -z "$(find "$CONFIG_DIR/secrets" -mindepth 1 -maxdepth 1 \
    ! -name lifecycle-api-key ! -name capability-token ! -name record-signing-key -print -quit)" || return 1
  test -z "$(find "$CONFIG_DIR/tls" -mindepth 1 -maxdepth 1 \
    ! -name fullchain.pem ! -name privkey.pem ! -name upstream-ca.pem -print -quit)" || return 1
  require_root_owned_regular "$CONFIG_DIR/gateway.env" 640 || return 1
  require_root_owned_regular "$CONFIG_DIR/egress-policy.v1.json" 640 || return 1
  require_root_owned_regular "$CONFIG_DIR/tls/fullchain.pem" 640 || return 1
  require_root_owned_regular "$CONFIG_DIR/tls/upstream-ca.pem" 640 || return 1
  require_root_owned_regular "$CONFIG_DIR/tls/privkey.pem" 440 || return 1
  for secret in lifecycle-api-key capability-token record-signing-key; do
    require_root_owned_regular "$CONFIG_DIR/secrets/$secret" 440 || return 1
  done
  test "$(grep -Fxc 'OPENSANDBOX_GATEWAY_UPSTREAM_CA_FILE=/etc/opensandbox-gateway/tls/upstream-ca.pem' "$CONFIG_DIR/gateway.env")" -eq 1
}

normalize_runtime_config_permissions() {
  test -d "$CONFIG_DIR" && test ! -L "$CONFIG_DIR" || return 1
  chown -R root:opensandbox-gateway "$CONFIG_DIR" || return 1
  chmod 0750 "$CONFIG_DIR" || return 1
  for directory in "$CONFIG_DIR/secrets" "$CONFIG_DIR/tls"; do
    test ! -e "$directory" || chmod 0750 "$directory" || return 1
  done
  for file in "$CONFIG_DIR/gateway.env" "$CONFIG_DIR/egress-policy.v1.json" \
    "$CONFIG_DIR/tls/fullchain.pem" "$CONFIG_DIR/tls/upstream-ca.pem"; do
    test ! -e "$file" || chmod 0640 "$file" || return 1
  done
  for file in "$CONFIG_DIR"/secrets/* "$CONFIG_DIR/tls/privkey.pem"; do
    test ! -e "$file" || chmod 0440 "$file" || return 1
  done
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
  s72_atomic_verify_manifest "$@"
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
  s72_atomic_require_marker_pair "$@"
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
  s72_atomic_preflight_snapshot "$@"
}

record_authority_state() {
  s72_atomic_record_authority_state "$@"
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
    normalize_runtime_config_permissions || return 1
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
require_root_tree "$CONFIG_DIR"
require_gateway_config_contract
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
normalize_runtime_config_permissions
require_gateway_config_contract
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
