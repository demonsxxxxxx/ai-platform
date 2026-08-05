#!/bin/sh
set -eu

AUTHORITY_REF=${OPENSANDBOX_GATEWAY_AUTHORITY_REF:-origin/main}
EXPECTED_AUTHORITY_SHA=${OPENSANDBOX_GATEWAY_EXPECTED_AUTHORITY_SHA:-}
AUTHORITY_EVIDENCE_ID=${OPENSANDBOX_GATEWAY_AUTHORITY_EVIDENCE_ID:-}
SERVICE_UID=${OPENSANDBOX_GATEWAY_SERVICE_UID:-}
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
SERVICE_USER=opensandbox-gateway
SERVICE_GROUP=opensandbox-gateway
SERVICE_HOME=/nonexistent
SERVICE_SHELL=/usr/sbin/nologin

is_commit() {
  test "${#1}" -eq 40 || return 1
  case "$1" in *[!0-9a-f]*) return 1 ;; esac
}

is_service_uid() {
  case "$1" in
    ""|0|0[0-9]*|*[!0-9]*) return 1 ;;
  esac
  uid_length=${#1}
  test "$uid_length" -le 10 || return 1
  test "$uid_length" -lt 10 && return 0
  test "$1" = 4294967294 || test "$1" \< 4294967294
}

gateway_user_entry() {
  printf '%s:x:%s:%s::%s:%s' "$SERVICE_USER" "$1" "$1" "$SERVICE_HOME" "$SERVICE_SHELL"
}

gateway_group_entry() {
  printf '%s:x:%s:' "$SERVICE_GROUP" "$1"
}

require_account_lookup_absent() (
  database=$1
  key=$2
  if getent "$database" "$key" >/dev/null 2>&1; then
    return 1
  else
    test "$?" -eq 2
  fi
)

require_gateway_config_uid() {
  gateway_env=$1
  gateway_uid=$2
  is_service_uid "$gateway_uid" || return 1
  expected_uid_line=OPENSANDBOX_GATEWAY_ALLOWED_UID=$gateway_uid
  uid_key_count=$(awk 'index($0, "OPENSANDBOX_GATEWAY_ALLOWED_UID") { count += 1 } END { print count + 0 }' "$gateway_env") || return 1
  uid_exact_count=$(awk -v expected="$expected_uid_line" '$0 == expected { count += 1 } END { print count + 0 }' "$gateway_env") || return 1
  test "$uid_key_count" -eq 1 && test "$uid_exact_count" -eq 1
}

preflight_gateway_account_contract() (
  gateway_uid=$1
  is_service_uid "$gateway_uid" || return 1
  expected_user=$(gateway_user_entry "$gateway_uid")
  expected_group=$(gateway_group_entry "$gateway_uid")

  if actual_group=$(getent group "$SERVICE_GROUP"); then
    test "$actual_group" = "$expected_group" || return 1
    test "$(getent group "$gateway_uid")" = "$expected_group" || return 1
    group_present=1
  else
    lookup_status=$?
    test "$lookup_status" -eq 2 || return 1
    group_present=0
    if getent group "$gateway_uid" >/dev/null 2>&1; then
      return 1
    else
      test "$?" -eq 2 || return 1
    fi
  fi

  if actual_user=$(getent passwd "$SERVICE_USER"); then
    test "$group_present" -eq 1 || return 1
    test "$actual_user" = "$expected_user" || return 1
    test "$(getent passwd "$gateway_uid")" = "$expected_user" || return 1
  else
    lookup_status=$?
    test "$lookup_status" -eq 2 || return 1
    if getent passwd "$gateway_uid" >/dev/null 2>&1; then
      return 1
    else
      test "$?" -eq 2 || return 1
    fi
  fi

  passwd_entries=$(getent passwd) || return 1
  unexpected_primary_gid=$(printf '%s\n' "$passwd_entries" | awk -F: -v gid="$gateway_uid" -v user="$SERVICE_USER" \
    '$4 == gid && $1 != user { print; exit }') || return 1
  test -z "$unexpected_primary_gid"
)

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

require_root_owned_regular() {
  path=$1
  mode=$2
  test -f "$path" && test ! -L "$path" || return 1
  test "$(stat -c %u "$path")" -eq 0 || return 1
  case "$(stat -c %G "$path")" in root|opensandbox-gateway) ;; *) return 1 ;; esac
  test "$(stat -c %a "$path")" = "$mode"
}

require_root_owned_directory() {
  path=$1
  mode=$2
  test -d "$path" && test ! -L "$path" || return 1
  test "$(stat -c %u "$path")" -eq 0 || return 1
  case "$(stat -c %G "$path")" in root|opensandbox-gateway) ;; *) return 1 ;; esac
  test "$(stat -c %a "$path")" = "$mode"
}

require_gateway_config_contract() {
  gateway_uid=$1
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
  require_gateway_config_uid "$CONFIG_DIR/gateway.env" "$gateway_uid"
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

preflight_gateway_account_snapshot() (
  snapshot=$1
  test -f "$snapshot/gateway-service-uid" && test ! -L "$snapshot/gateway-service-uid" || return 1
  gateway_uid=$(cat "$snapshot/gateway-service-uid") || return 1
  is_service_uid "$gateway_uid" || return 1
  test "$(grep -Fxc "$gateway_uid" "$snapshot/gateway-service-uid")" -eq 1 || return 1
  expected_user=$(gateway_user_entry "$gateway_uid")
  expected_group=$(gateway_group_entry "$gateway_uid")

  require_marker_pair "$snapshot/gateway-user.present" "$snapshot/gateway-user.absent" || return 1
  if test -f "$snapshot/gateway-user.present"; then
    test -f "$snapshot/gateway-user.entry" && test ! -e "$snapshot/gateway-user.created" || return 1
    test "$(grep -Fxc "$expected_user" "$snapshot/gateway-user.entry")" -eq 1 || return 1
  else
    test ! -e "$snapshot/gateway-user.entry" || return 1
    test ! -e "$snapshot/gateway-user.created" || \
      test "$(grep -Fxc "$expected_user" "$snapshot/gateway-user.created")" -eq 1 || return 1
  fi

  require_marker_pair "$snapshot/gateway-group.present" "$snapshot/gateway-group.absent" || return 1
  if test -f "$snapshot/gateway-group.present"; then
    test -f "$snapshot/gateway-group.entry" && test ! -e "$snapshot/gateway-group.created" || return 1
    test "$(grep -Fxc "$expected_group" "$snapshot/gateway-group.entry")" -eq 1 || return 1
  else
    test ! -e "$snapshot/gateway-group.entry" || return 1
    test ! -e "$snapshot/gateway-group.created" || \
      test "$(grep -Fxc "$expected_group" "$snapshot/gateway-group.created")" -eq 1 || return 1
  fi
)

snapshot_gateway_account_state() (
  snapshot=$1
  gateway_uid=$2
  preflight_gateway_account_contract "$gateway_uid" || return 1
  printf '%s\n' "$gateway_uid" > "$snapshot/gateway-service-uid"
  if actual_user=$(getent passwd "$SERVICE_USER"); then
    printf '%s\n' "$actual_user" > "$snapshot/gateway-user.entry"
    : > "$snapshot/gateway-user.present"
  else
    test "$?" -eq 2 || return 1
    : > "$snapshot/gateway-user.absent"
  fi
  if actual_group=$(getent group "$SERVICE_GROUP"); then
    printf '%s\n' "$actual_group" > "$snapshot/gateway-group.entry"
    : > "$snapshot/gateway-group.present"
  else
    test "$?" -eq 2 || return 1
    : > "$snapshot/gateway-group.absent"
  fi
  preflight_gateway_account_snapshot "$snapshot"
)

ensure_gateway_account() {
  account_snapshot=$1
  account_uid=$2
  preflight_gateway_account_snapshot "$account_snapshot" || return 1
  test "$(cat "$account_snapshot/gateway-service-uid")" = "$account_uid" || return 1
  account_expected_user=$(gateway_user_entry "$account_uid")
  account_expected_group=$(gateway_group_entry "$account_uid")

  if test -f "$account_snapshot/gateway-group.absent"; then
    groupadd --system --gid "$account_uid" "$SERVICE_GROUP" || return 1
    account_actual_group=$(getent group "$SERVICE_GROUP") || return 1
    test "$account_actual_group" = "$account_expected_group" || return 1
    test "$(getent group "$account_uid")" = "$account_expected_group" || return 1
    printf '%s\n' "$account_actual_group" > "$account_snapshot/gateway-group.created"
    GATEWAY_GROUP_CREATED=1
  fi
  if test -f "$account_snapshot/gateway-user.absent"; then
    useradd --system --uid "$account_uid" --gid "$SERVICE_GROUP" --home-dir "$SERVICE_HOME" \
      --shell "$SERVICE_SHELL" --no-create-home --comment "" "$SERVICE_USER" || return 1
    account_actual_user=$(getent passwd "$SERVICE_USER") || return 1
    test "$account_actual_user" = "$account_expected_user" || return 1
    test "$(getent passwd "$account_uid")" = "$account_expected_user" || return 1
    printf '%s\n' "$account_actual_user" > "$account_snapshot/gateway-user.created"
    GATEWAY_USER_CREATED=1
  fi
  preflight_gateway_account_snapshot "$account_snapshot" || return 1
  preflight_gateway_account_contract "$account_uid"
}

preflight_gateway_account_restore() (
  snapshot=$1
  preflight_gateway_account_snapshot "$snapshot" || return 1
  gateway_uid=$(cat "$snapshot/gateway-service-uid") || return 1
  preflight_gateway_account_contract "$gateway_uid" || return 1

  if test -f "$snapshot/gateway-user.present"; then
    test "$(getent passwd "$SERVICE_USER")" = "$(cat "$snapshot/gateway-user.entry")" || return 1
  elif test -f "$snapshot/gateway-user.created"; then
    test "$(getent passwd "$SERVICE_USER")" = "$(cat "$snapshot/gateway-user.created")" || return 1
  else
    require_account_lookup_absent passwd "$SERVICE_USER" || return 1
    require_account_lookup_absent passwd "$gateway_uid" || return 1
  fi

  if test -f "$snapshot/gateway-group.present"; then
    test "$(getent group "$SERVICE_GROUP")" = "$(cat "$snapshot/gateway-group.entry")" || return 1
  elif test -f "$snapshot/gateway-group.created"; then
    test "$(getent group "$SERVICE_GROUP")" = "$(cat "$snapshot/gateway-group.created")" || return 1
  else
    require_account_lookup_absent group "$SERVICE_GROUP" || return 1
    require_account_lookup_absent group "$gateway_uid" || return 1
  fi
)

verify_gateway_account_restored() (
  snapshot=$1
  preflight_gateway_account_snapshot "$snapshot" || return 1
  gateway_uid=$(cat "$snapshot/gateway-service-uid") || return 1

  if test -f "$snapshot/gateway-user.present"; then
    test "$(getent passwd "$SERVICE_USER")" = "$(cat "$snapshot/gateway-user.entry")" || return 1
    test "$(getent passwd "$gateway_uid")" = "$(cat "$snapshot/gateway-user.entry")" || return 1
  else
    require_account_lookup_absent passwd "$SERVICE_USER" || return 1
    require_account_lookup_absent passwd "$gateway_uid" || return 1
  fi
  if test -f "$snapshot/gateway-group.present"; then
    test "$(getent group "$SERVICE_GROUP")" = "$(cat "$snapshot/gateway-group.entry")" || return 1
    test "$(getent group "$gateway_uid")" = "$(cat "$snapshot/gateway-group.entry")" || return 1
  else
    require_account_lookup_absent group "$SERVICE_GROUP" || return 1
    require_account_lookup_absent group "$gateway_uid" || return 1
  fi
)

restore_gateway_account_state() (
  snapshot=$1
  preflight_gateway_account_restore "$snapshot" || return 1
  if test -f "$snapshot/gateway-user.absent" && test -f "$snapshot/gateway-user.created"; then
    userdel "$SERVICE_USER" || return 1
  fi
  if test -f "$snapshot/gateway-group.absent" && test -f "$snapshot/gateway-group.created"; then
    groupdel "$SERVICE_GROUP" || return 1
  fi
  verify_gateway_account_restored "$snapshot"
)

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
  preflight_gateway_account_snapshot "$snapshot" || return 1
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
  gateway_uid=$2
  preflight_live_state
  install -d -o root -g root -m 0700 "$snapshot"
  snapshot_gateway_account_state "$snapshot" "$gateway_uid"
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
  preflight_gateway_account_restore "$snapshot" || return 1
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
  restore_gateway_account_state "$snapshot" || return 1
}

cleanup_install() {
  status=$?
  trap - EXIT HUP INT TERM
  if test "$SUCCESS" -eq 0; then
    set +e
    if test "${ACCOUNT_SNAPSHOT:-}" = "$RESTORE_FROM" && \
      { test "${GATEWAY_USER_CREATED:-0}" -eq 1 || test "${GATEWAY_GROUP_CREATED:-0}" -eq 1; }; then
      chown -R root:root "$RESTORE_FROM"
      write_manifest "$RESTORE_FROM"
    fi
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
is_service_uid "$SERVICE_UID"
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
require_gateway_config_contract "$SERVICE_UID"
test "$(systemctl show opensandbox.service -p ActiveState --value)" = active
test "$(systemctl show opensandbox.service -p FragmentPath --value)" = /etc/systemd/system/opensandbox.service
ss -ltn | grep -q '127.0.0.1:8080'
preflight_live_state
preflight_gateway_account_contract "$SERVICE_UID"

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
ACCOUNT_SNAPSHOT=$BACKUP
GATEWAY_USER_CREATED=0
GATEWAY_GROUP_CREATED=0
SUCCESS=0
trap 'cleanup_install' EXIT HUP INT TERM

snapshot_state "$BACKUP" "$SERVICE_UID"
ensure_gateway_account "$BACKUP" "$SERVICE_UID"
chown -R root:root "$BACKUP"
write_manifest "$BACKUP"
preflight_snapshot "$BACKUP"
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
require_gateway_config_contract "$SERVICE_UID"
preflight_gateway_account_contract "$SERVICE_UID"
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
