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
SERVICE_USER=opensandbox-gateway
SERVICE_GROUP=opensandbox-gateway
SERVICE_HOME=/nonexistent
SERVICE_SHELL=/usr/sbin/nologin
SNAPSHOT_FORMAT=opensandbox-gateway-snapshot-v2

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
    require_account_lookup_absent group "$gateway_uid" || return 1
  fi

  if actual_user=$(getent passwd "$SERVICE_USER"); then
    test "$group_present" -eq 1 || return 1
    test "$actual_user" = "$expected_user" || return 1
    test "$(getent passwd "$gateway_uid")" = "$expected_user" || return 1
  else
    lookup_status=$?
    test "$lookup_status" -eq 2 || return 1
    require_account_lookup_absent passwd "$gateway_uid" || return 1
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

require_gateway_config_uid() {
  gateway_env=$1
  gateway_uid=$2
  is_service_uid "$gateway_uid" || return 1
  expected_uid_line=OPENSANDBOX_GATEWAY_ALLOWED_UID=$gateway_uid
  uid_key_count=$(awk 'index($0, "OPENSANDBOX_GATEWAY_ALLOWED_UID") { count += 1 } END { print count + 0 }' "$gateway_env") || return 1
  uid_exact_count=$(awk -v expected="$expected_uid_line" '$0 == expected { count += 1 } END { print count + 0 }' "$gateway_env") || return 1
  test "$uid_key_count" -eq 1 && test "$uid_exact_count" -eq 1
}

require_gateway_config_contract() {
  gateway_uid=$1
  require_root_tree "$CONFIG_DIR" || return 1
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

require_gateway_runtime_config_readability() {
  gateway_uid=$1
  preflight_gateway_account_contract "$gateway_uid" || return 1
  require_gateway_config_contract "$gateway_uid" || return 1
  for path in "$CONFIG_DIR" "$CONFIG_DIR/secrets" "$CONFIG_DIR/tls" \
    "$CONFIG_DIR/gateway.env" "$CONFIG_DIR/egress-policy.v1.json" \
    "$CONFIG_DIR/tls/fullchain.pem" "$CONFIG_DIR/tls/upstream-ca.pem" \
    "$CONFIG_DIR/tls/privkey.pem" "$CONFIG_DIR/secrets/lifecycle-api-key" \
    "$CONFIG_DIR/secrets/capability-token" "$CONFIG_DIR/secrets/record-signing-key"; do
    test "$(stat -c %g "$path")" = "$gateway_uid" || return 1
  done
}

verify_manifest() (
  target=$1
  manifest=$target/MANIFEST.sha256
  test -f "$manifest" && test ! -L "$manifest" || return 1
  listed=$(mktemp "${TMPDIR:-/tmp}/opensandbox-manifest-listed.XXXXXX") || return 1
  actual=$(mktemp "${TMPDIR:-/tmp}/opensandbox-manifest-actual.XXXXXX") || {
    rm -f "$listed"
    return 1
  }
  trap 'rm -f "$listed" "$actual"' EXIT HUP INT TERM
  awk '
    length($0) < 68 || substr($0, 65, 2) != "  " { exit 1 }
    length(substr($0, 1, 64)) != 64 || substr($0, 1, 64) !~ /^[0-9a-f]+$/ { exit 1 }
    substr($0, 67) !~ /^\.\/[A-Za-z0-9._\/-]+$/ { exit 1 }
    substr($0, 69) ~ /(^|\/)\.\.?(\/|$)/ || substr($0, 69) ~ /\/\// { exit 1 }
    { print substr($0, 67) }
  ' "$manifest" > "$listed" || return 1
  LC_ALL=C sort "$listed" -o "$listed" || return 1
  awk 'seen[$0]++ { exit 1 }' "$listed" || return 1
  (cd "$target" && find . -type f ! -path ./MANIFEST.sha256 -print) > "$actual" || return 1
  LC_ALL=C sort "$actual" -o "$actual" || return 1
  cmp -s "$listed" "$actual" || return 1
  (cd "$target" && sha256sum -c MANIFEST.sha256 >/dev/null)
)

is_safe_snapshot_path() {
  case "$1" in
    .) return 0 ;;
    ./*) ;;
    *) return 1 ;;
  esac
  case "$1" in
    *[!A-Za-z0-9._/-]*|*//*|*/../*|*/..|*/./*|*/.) return 1 ;;
  esac
}

capture_config_metadata() (
  tree=$1
  require_root_tree "$tree" || return 1
  cd "$tree" || return 1
  paths=$(mktemp "${TMPDIR:-/tmp}/opensandbox-config-paths.XXXXXX") || return 1
  trap 'rm -f "$paths"' EXIT HUP INT TERM
  find . -mindepth 1 -print > "$paths" || return 1
  LC_ALL=C sort "$paths" -o "$paths" || return 1
  printf '.\td\t%s\n' "$(stat -c %u:%g:%a .)" || return 1
  while IFS= read -r relative; do
    is_safe_snapshot_path "$relative" || exit 1
    if test -d "$relative" && test ! -L "$relative"; then
      kind=d
    elif test -f "$relative" && test ! -L "$relative"; then
      kind=f
    else
      exit 1
    fi
    printf '%s\t%s\t%s\n' "$relative" "$kind" "$(stat -c %u:%g:%a "$relative")" || exit 1
  done < "$paths"
)

verify_config_metadata() {
  tree=$1
  metadata=$2
  test -f "$metadata" && test ! -L "$metadata" || return 1
  test "$(stat -c %u:%g:%a "$metadata")" = 0:0:400 || return 1
  actual_metadata=$(capture_config_metadata "$tree") || return 1
  test "$(cat "$metadata")" = "$actual_metadata"
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

snapshot_account_is_managed() {
  snapshot=$1
  if test -f "$snapshot/gateway-service-uid" && test ! -L "$snapshot/gateway-service-uid"; then
    return 0
  fi
  test -z "$(find "$snapshot" -maxdepth 1 -name 'gateway-*' -print -quit)" || return 1
  return 2
}

require_known_snapshot_files() (
  snapshot=$1
  test -z "$(find "$snapshot" -mindepth 1 -maxdepth 1 -type d ! -name etc-opensandbox-gateway -print -quit)" || return 1
  paths=$(mktemp "${TMPDIR:-/tmp}/opensandbox-snapshot-paths.XXXXXX") || return 1
  trap 'rm -f "$paths"' EXIT HUP INT TERM
  find "$snapshot" -maxdepth 1 -type f -print > "$paths" || return 1
  while IFS= read -r path; do
    name=${path##*/}
    case "$name" in
      MANIFEST.sha256|snapshot-format|rollback-from|workspaces.acl|config.present|config.absent|config.metadata|\
      authority-sha|authority-sha.absent|authority-evidence|authority-evidence.absent|current|current.absent|\
      gateway-service-uid|gateway-user.present|gateway-user.absent|gateway-user.entry|gateway-user.created|\
      gateway-group.present|gateway-group.absent|gateway-group.entry|gateway-group.created|\
      opensandbox-gateway.service|opensandbox-gateway.service.present|opensandbox-gateway.service.absent|\
      opensandbox-gateway.service.active|opensandbox-gateway.service.inactive|opensandbox-gateway.service.enabled|\
      opensandbox-gateway.service.disabled|opensandbox-gateway-helper.service|\
      opensandbox-gateway-helper.service.present|opensandbox-gateway-helper.service.absent|\
      opensandbox-gateway-helper.service.active|opensandbox-gateway-helper.service.inactive|\
      opensandbox-gateway-helper.service.enabled|opensandbox-gateway-helper.service.disabled) ;;
      *) exit 1 ;;
    esac
  done < "$paths"
)

preflight_gateway_account_snapshot() (
  snapshot=$1
  if snapshot_account_is_managed "$snapshot"; then
    :
  else
    account_status=$?
    if test "$account_status" -eq 2; then return 0; fi
    return 1
  fi
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

preflight_gateway_account_restore() (
  snapshot=$1
  preflight_gateway_account_snapshot "$snapshot" || return 1
  if snapshot_account_is_managed "$snapshot"; then
    :
  else
    account_status=$?
    if test "$account_status" -eq 2; then return 0; fi
    return 1
  fi
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
  if snapshot_account_is_managed "$snapshot"; then
    :
  else
    account_status=$?
    if test "$account_status" -eq 2; then return 0; fi
    return 1
  fi
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

gateway_account_removal_required() {
  snapshot=$1
  test -f "$snapshot/gateway-user.created" || test -f "$snapshot/gateway-group.created"
}

stop_unit_and_require_inactive() {
  unit=$1
  systemctl stop "$unit" >/dev/null 2>&1 || :
  test "$(systemctl show "$unit" -p ActiveState --value)" = inactive
}

require_no_gateway_uid_processes() {
  gateway_uid=$1
  processes=$(ps -eo uid=,pid=) || return 1
  unexpected_process=$(printf '%s\n' "$processes" | awk -v uid="$gateway_uid" '$1 == uid { print; exit }') || return 1
  test -z "$unexpected_process"
}

require_safe_gateway_runtime_state() {
  gateway_uid=$1
  test ! -e "$SERVICE_HOME" && test ! -L "$SERVICE_HOME" || return 1
  if test -e "$RUNTIME_STATE" || test -L "$RUNTIME_STATE"; then
    test -d "$RUNTIME_STATE" && test ! -L "$RUNTIME_STATE" || return 1
    test "$(stat -c %u:%g:%a "$RUNTIME_STATE")" = "$gateway_uid:$gateway_uid:700" || return 1
    test -z "$(find "$RUNTIME_STATE" -xdev -type l -print -quit)" || return 1
    unsafe_runtime_path=$(find "$RUNTIME_STATE" -xdev \( ! -uid "$gateway_uid" -o ! -gid "$gateway_uid" \) -print -quit) || return 1
    test -z "$unsafe_runtime_path" || return 1
  fi
}

prepare_gateway_account_removal() (
  snapshot=$1
  gateway_uid=$(cat "$snapshot/gateway-service-uid") || return 1
  for unit in opensandbox-gateway.service opensandbox-gateway-helper.service; do
    stop_unit_and_require_inactive "$unit" || return 1
  done
  require_no_gateway_uid_processes "$gateway_uid" || return 1
  require_safe_gateway_runtime_state "$gateway_uid" || return 1
  preflight_gateway_account_restore "$snapshot" || return 1
  preflight_gateway_account_contract "$gateway_uid"
)

restore_gateway_account_state() (
  snapshot=$1
  preflight_gateway_account_restore "$snapshot" || return 1
  if snapshot_account_is_managed "$snapshot"; then
    :
  else
    account_status=$?
    if test "$account_status" -eq 2; then return 0; fi
    return 1
  fi
  if gateway_account_removal_required "$snapshot"; then
    prepare_gateway_account_removal "$snapshot" || return 1
  fi
  if test -f "$snapshot/gateway-user.absent" && test -f "$snapshot/gateway-user.created"; then
    require_no_gateway_uid_processes "$(cat "$snapshot/gateway-service-uid")" || return 1
    userdel "$SERVICE_USER" || return 1
  fi
  if test -f "$snapshot/gateway-group.absent" && test -f "$snapshot/gateway-group.created"; then
    gateway_uid=$(cat "$snapshot/gateway-service-uid") || return 1
    expected_group=$(gateway_group_entry "$gateway_uid")
    test "$(getent group "$SERVICE_GROUP")" = "$expected_group" || return 1
    passwd_entries=$(getent passwd) || return 1
    unexpected_primary_gid=$(printf '%s\n' "$passwd_entries" | awk -F: -v gid="$gateway_uid" '$4 == gid { print; exit }') || return 1
    test -z "$unexpected_primary_gid" || return 1
    require_no_gateway_uid_processes "$gateway_uid" || return 1
    groupdel "$SERVICE_GROUP" || return 1
  fi
  verify_gateway_account_restored "$snapshot"
)

preflight_snapshot() {
  snapshot=$1
  require_root_tree "$snapshot" || return 1
  verify_manifest "$snapshot" || return 1
  require_known_snapshot_files "$snapshot" || return 1
  managed_snapshot=0
  if snapshot_account_is_managed "$snapshot"; then
    managed_snapshot=1
    test -f "$snapshot/snapshot-format" && test ! -L "$snapshot/snapshot-format" || return 1
    test "$(grep -Fxc "$SNAPSHOT_FORMAT" "$snapshot/snapshot-format")" -eq 1 || return 1
    test -f "$snapshot/rollback-from" && test ! -L "$snapshot/rollback-from" || return 1
    rollback_from=$(cat "$snapshot/rollback-from") || return 1
    is_commit "$rollback_from" || return 1
    test "$(grep -Fxc "$rollback_from" "$snapshot/rollback-from")" -eq 1 || return 1
  else
    account_status=$?
    test "$account_status" -eq 2 || return 1
    test ! -e "$snapshot/snapshot-format" && test ! -e "$snapshot/rollback-from" || return 1
  fi
  for unit in opensandbox-gateway.service opensandbox-gateway-helper.service; do
    require_marker_pair "$snapshot/$unit.present" "$snapshot/$unit.absent" || return 1
    test ! -f "$snapshot/$unit.present" || test -f "$snapshot/$unit" || return 1
    require_marker_pair "$snapshot/$unit.active" "$snapshot/$unit.inactive" || return 1
    require_marker_pair "$snapshot/$unit.enabled" "$snapshot/$unit.disabled" || return 1
    if test "$managed_snapshot" -eq 1 && test -f "$snapshot/$unit.absent"; then
      test -f "$snapshot/$unit.inactive" && test -f "$snapshot/$unit.disabled" || return 1
    fi
  done
  require_marker_pair "$snapshot/config.present" "$snapshot/config.absent" || return 1
  if test -f "$snapshot/config.present"; then
    test -d "$snapshot/etc-opensandbox-gateway" && test ! -L "$snapshot/etc-opensandbox-gateway" || return 1
    if test "$managed_snapshot" -eq 1; then
      verify_config_metadata "$snapshot/etc-opensandbox-gateway" "$snapshot/config.metadata" || return 1
    else
      test ! -e "$snapshot/config.metadata" || return 1
    fi
  else
    test ! -e "$snapshot/etc-opensandbox-gateway" && test ! -e "$snapshot/config.metadata" || return 1
  fi
  test -f "$snapshot/workspaces.acl" || return 1
  preflight_gateway_account_snapshot "$snapshot" || return 1
  if test "$managed_snapshot" -eq 1 && test -f "$snapshot/gateway-user.absent"; then
    test -f "$snapshot/opensandbox-gateway.service.inactive" || return 1
    test -f "$snapshot/opensandbox-gateway-helper.service.inactive" || return 1
    test -f "$snapshot/opensandbox-gateway.service.disabled" || return 1
    test -f "$snapshot/opensandbox-gateway-helper.service.disabled" || return 1
  elif test "$managed_snapshot" -eq 0; then
    test -f "$snapshot/opensandbox-gateway.service.inactive" || return 1
    test -f "$snapshot/opensandbox-gateway-helper.service.inactive" || return 1
  fi
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

require_unit_not_enabled() {
  unit=$1
  if unit_file_state=$(systemctl is-enabled "$unit" 2>/dev/null); then
    return 1
  fi
  case "$unit_file_state" in disabled|not-found) return 0 ;; *) return 1 ;; esac
}

quiesce_gateway_units() {
  for unit in opensandbox-gateway.service opensandbox-gateway-helper.service; do
    stop_unit_and_require_inactive "$unit" || return 1
    systemctl disable "$unit" >/dev/null 2>&1 || :
    require_unit_not_enabled "$unit" || return 1
  done
}

verify_snapshot_unit_state() {
  snapshot=$1
  unit=$2
  if test -f "$snapshot/$unit.active"; then
    test "$(systemctl show "$unit" -p ActiveState --value)" = active || return 1
  else
    test "$(systemctl show "$unit" -p ActiveState --value)" = inactive || return 1
  fi
  if test -f "$snapshot/$unit.enabled"; then
    test "$(systemctl is-enabled "$unit" 2>/dev/null)" = enabled || return 1
  else
    require_unit_not_enabled "$unit" || return 1
  fi
}

apply_snapshot_unit_states() {
  snapshot=$1
  gateway_uid=$2
  for unit in opensandbox-gateway-helper.service opensandbox-gateway.service; do
    if test -f "$snapshot/$unit.enabled"; then
      systemctl enable "$unit" >/dev/null 2>&1 || return 1
    elif test -f "$snapshot/$unit.present"; then
      systemctl disable "$unit" >/dev/null 2>&1 || return 1
    fi
    if test -f "$snapshot/$unit.active"; then
      require_gateway_runtime_config_readability "$gateway_uid" || return 1
      systemctl restart "$unit" || return 1
    else
      stop_unit_and_require_inactive "$unit" || return 1
    fi
  done
  for unit in opensandbox-gateway-helper.service opensandbox-gateway.service; do
    verify_snapshot_unit_state "$snapshot" "$unit" || return 1
  done
}

require_snapshot_matches_current() {
  snapshot=$1
  current_commit=$2
  if snapshot_account_is_managed "$snapshot"; then
    test "$(cat "$snapshot/rollback-from")" = "$current_commit"
  else
    account_status=$?
    test "$account_status" -eq 2
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
preflight_gateway_account_restore "$SNAPSHOT"
MANAGED_SNAPSHOT=0
if snapshot_account_is_managed "$SNAPSHOT"; then
  MANAGED_SNAPSHOT=1
  GATEWAY_UID=$(cat "$SNAPSHOT/gateway-service-uid")
  require_snapshot_matches_current "$SNAPSHOT" "$CURRENT_COMMIT"
  quiesce_gateway_units
else
  test "$?" -eq 2
fi

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
  if test "$MANAGED_SNAPSHOT" -eq 1; then
    verify_config_metadata "$CONFIG_DIR" "$SNAPSHOT/config.metadata"
  fi
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
if test "$MANAGED_SNAPSHOT" -eq 1; then
  restore_gateway_account_state "$SNAPSHOT"
else
  for unit in opensandbox-gateway-helper.service opensandbox-gateway.service; do
    if test -f "$SNAPSHOT/$unit.enabled"; then
      systemctl enable "$unit" >/dev/null 2>&1
    else
      systemctl disable "$unit" >/dev/null 2>&1 || true
    fi
    systemctl stop "$unit" >/dev/null 2>&1 || true
  done
fi
if test -n "$PREVIOUS"; then
  ln -s "$PREVIOUS" "$CURRENT_LINK.next"
  mv -Tf "$CURRENT_LINK.next" "$CURRENT_LINK"
  test "$(readlink -f "$CURRENT_LINK")" = "$RELEASES/$previous_commit"
  record_authority_state "$previous_commit" "$AUTHORITY_EVIDENCE_ID"
else
  rm -f "$CURRENT_LINK"
fi
if test "$MANAGED_SNAPSHOT" -eq 1; then
  apply_snapshot_unit_states "$SNAPSHOT" "$GATEWAY_UID"
fi
systemctl is-active --quiet opensandbox.service
ss -ltn | grep -q '127.0.0.1:8080'
}

rollback_main "$@"

# Docker provider configuration is never modified by deployment or rollback.
