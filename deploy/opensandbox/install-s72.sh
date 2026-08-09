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
TRANSACTION_RECORDS=$DEPLOY_STATE/transactions
SNAPSHOTS=$DEPLOY_STATE/snapshots
LOCK_FILE=/run/lock/opensandbox-gateway-s72-install.lock

S72_ATOMIC_RECOVERY_HELPER_SHA256=ec76affc74a7b88bf3a66934bdb9eb4a63d9d8f08aac5cddc06f4f2ba76770e1

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
  s72_atomic_record_authority_state \
  s72_atomic_publish_transaction_record \
  s72_atomic_load_active_transaction \
  s72_atomic_publish_snapshot \
  s72_atomic_verify_snapshot_seal \
  s72_atomic_restore_snapshot \
  s72_atomic_require_exact_lifecycle; do
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
  require_gateway_config_contract_at "$CONFIG_DIR"
}

require_gateway_config_contract_at() {
  contract_root=$1
  require_root_owned_directory "$contract_root" 750 || return 1
  require_root_owned_directory "$contract_root/secrets" 750 || return 1
  require_root_owned_directory "$contract_root/tls" 750 || return 1
  test -z "$(find "$contract_root" -mindepth 1 -maxdepth 1 \
    ! -name gateway.env ! -name egress-policy.v1.json ! -name secrets ! -name tls -print -quit)" || return 1
  test -z "$(find "$contract_root/secrets" -mindepth 1 -maxdepth 1 \
    ! -name lifecycle-api-key ! -name capability-token ! -name record-signing-key -print -quit)" || return 1
  test -z "$(find "$contract_root/tls" -mindepth 1 -maxdepth 1 \
    ! -name fullchain.pem ! -name privkey.pem ! -name upstream-ca.pem -print -quit)" || return 1
  require_root_owned_regular "$contract_root/gateway.env" 640 || return 1
  require_root_owned_regular "$contract_root/egress-policy.v1.json" 640 || return 1
  require_root_owned_regular "$contract_root/tls/fullchain.pem" 640 || return 1
  require_root_owned_regular "$contract_root/tls/upstream-ca.pem" 640 || return 1
  require_root_owned_regular "$contract_root/tls/privkey.pem" 440 || return 1
  for secret in lifecycle-api-key capability-token record-signing-key; do
    require_root_owned_regular "$contract_root/secrets/$secret" 440 || return 1
  done
  test "$(grep -Fxc 'OPENSANDBOX_GATEWAY_UPSTREAM_CA_FILE=/etc/opensandbox-gateway/tls/upstream-ca.pem' "$contract_root/gateway.env")" -eq 1
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
  s72_atomic_write_manifest "$@"
}

verify_manifest() {
  s72_atomic_verify_manifest "$@"
}

capture_config_metadata() (
  tree=$1
  require_gateway_config_contract_at "$tree" || return 1
  cd "$tree" || return 1
  find . -mindepth 1 -print | LC_ALL=C sort | while IFS= read -r relative; do
    test -f "$relative" && test ! -L "$relative" && kind=f || {
      test -d "$relative" && test ! -L "$relative" && kind=d || exit 1
    }
    digest=-
    test "$kind" = d || digest=$(sha256sum "$relative" | awk '{ print $1 }') || exit 1
    printf '%s\t%s\t%s\t%s\n' "$relative" "$kind" \
      "$(stat -c %u:%g:%a:%s:%Y:%Z "$relative")" "$digest" || exit 1
  done
)

write_config_metadata() {
  tree=$1
  output=$2
  contents=$(capture_config_metadata "$tree") || return 1
  s72_atomic_publish_new_file "$output" 0400 "$contents"
}

verify_config_metadata() {
  tree=$1
  metadata=$2
  s72_atomic_require_root_owned_regular "$metadata" 400 || return 1
  test "$(cat "$metadata")" = "$(capture_config_metadata "$tree")"
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
    require_gateway_config_contract || return 1
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
  if test -n "$current_commit"; then
    cmp -s "$SYSTEMD_DIR/opensandbox-gateway.service" \
      "$RELEASES/$current_commit/config/opensandbox-gateway.service" || return 1
    cmp -s "$SYSTEMD_DIR/opensandbox-gateway-helper.service" \
      "$RELEASES/$current_commit/config/opensandbox-gateway-helper.service" || return 1
  else
    test ! -e "$SYSTEMD_DIR/opensandbox-gateway.service" || return 1
    test ! -e "$SYSTEMD_DIR/opensandbox-gateway-helper.service" || return 1
  fi
  if test -e "$ROLLBACK_POINTER" || test -L "$ROLLBACK_POINTER"; then
    s72_atomic_require_root_owned_regular "$ROLLBACK_POINTER" 600 || return 1
    rollback_id=$(cat "$ROLLBACK_POINTER") || return 1
    s72_atomic_is_snapshot_id "$rollback_id" || return 1
    s72_atomic_preflight_snapshot "$DEPLOY_STATE/snapshots/$rollback_id" || return 1
    s72_atomic_verify_snapshot_seal "$DEPLOY_STATE/snapshots/$rollback_id" || return 1
  fi
  s72_atomic_require_exact_lifecycle
}

live_file_matches_snapshot() {
  live=$1
  snapshot=$2
  payload=$3
  present_marker=$4
  mode=$5
  if test -f "$snapshot/$present_marker"; then
    s72_atomic_file_matches "$live" "$snapshot/$payload" "$mode"
  else
    test ! -e "$live" && test ! -L "$live"
  fi
}

live_current_matches_snapshot() {
  snapshot=$1
  if test -f "$snapshot/current"; then
    test -L "$CURRENT_LINK" && test "$(readlink "$CURRENT_LINK")" = "$(cat "$snapshot/current")"
  else
    test ! -e "$CURRENT_LINK" && test ! -L "$CURRENT_LINK"
  fi
}

preflight_recoverable_live() {
  recovery_id=$1
  apply_id=$2
  recovery=$SNAPSHOTS/$recovery_id
  s72_atomic_preflight_snapshot "$recovery" || return 1
  s72_atomic_verify_snapshot_seal "$recovery" || return 1
  apply=
  if test "$apply_id" != none; then
    apply=$SNAPSHOTS/$apply_id
    s72_atomic_preflight_snapshot "$apply" || return 1
    s72_atomic_verify_snapshot_seal "$apply" || return 1
  fi
  for unit in opensandbox-gateway.service opensandbox-gateway-helper.service; do
    live_file_matches_snapshot "$SYSTEMD_DIR/$unit" "$recovery" "$unit" "$unit.present" 644 \
      || { test -n "$apply" && live_file_matches_snapshot "$SYSTEMD_DIR/$unit" "$apply" \
        "$unit" "$unit.present" 644; } || return 1
  done
  if test -f "$recovery/config.present"; then
    s72_atomic_directory_matches "$CONFIG_DIR" "$recovery/etc-opensandbox-gateway" \
      || { test -n "$apply" && test -f "$apply/config.present" \
        && s72_atomic_directory_matches "$CONFIG_DIR" "$apply/etc-opensandbox-gateway"; } || return 1
  else
    { test ! -e "$CONFIG_DIR" && test ! -L "$CONFIG_DIR"; } \
      || { test -n "$apply" && test -f "$apply/config.present" \
        && s72_atomic_directory_matches "$CONFIG_DIR" "$apply/etc-opensandbox-gateway"; } || return 1
  fi
  live_current_matches_snapshot "$recovery" \
    || { test -n "$apply" && live_current_matches_snapshot "$apply"; } || return 1
  s72_atomic_require_exact_lifecycle
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
  test -d "$snapshot" && test ! -L "$snapshot" || return 1
  test -f "$snapshot/transaction-owner" && test ! -L "$snapshot/transaction-owner" || return 1
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
    require_gateway_config_contract
    cp -a "$CONFIG_DIR" "$snapshot/etc-opensandbox-gateway"
    : > "$snapshot/config.present"
    write_config_metadata "$snapshot/etc-opensandbox-gateway" "$snapshot/config.metadata"
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
  printf '%s\n' "$EXPECTED_AUTHORITY_SHA" > "$snapshot/captured-authority-sha"
  printf '%s\n' "$AUTHORITY_EVIDENCE_ID" > "$snapshot/captured-authority-evidence"
  if test -e "$ROLLBACK_POINTER" || test -L "$ROLLBACK_POINTER"; then
    s72_atomic_require_root_owned_regular "$ROLLBACK_POINTER" 600
    rollback_id=$(cat "$ROLLBACK_POINTER")
    s72_atomic_is_snapshot_id "$rollback_id"
    printf '%s\n' "$rollback_id" > "$snapshot/rollback-pointer"
  else
    : > "$snapshot/rollback-pointer.absent"
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
  s72_atomic_write_lifecycle_authority "$snapshot/lifecycle.authority"
  chown -R root:root "$snapshot"
  find "$snapshot" -maxdepth 1 -type f \( -name '*.present' -o -name '*.absent' \
    -o -name '*.active' -o -name '*.inactive' -o -name '*.enabled' -o -name '*.disabled' \) \
    -exec chmod 0600 {} +
  chmod 0600 "$snapshot/workspaces.acl"
  chmod 0400 "$snapshot/captured-authority-sha" "$snapshot/captured-authority-evidence"
  test ! -e "$snapshot/authority-sha" || chmod 0400 "$snapshot/authority-sha" "$snapshot/authority-evidence"
  test ! -e "$snapshot/current" || chmod 0400 "$snapshot/current"
  test ! -e "$snapshot/rollback-pointer" || chmod 0400 "$snapshot/rollback-pointer"
  for unit in opensandbox-gateway.service opensandbox-gateway-helper.service; do
    test ! -e "$snapshot/$unit" || chmod 0644 "$snapshot/$unit"
  done
  write_manifest "$snapshot"
  require_root_tree "$snapshot"
  verify_manifest "$snapshot"
  preflight_snapshot "$snapshot"
}

restore_snapshot_payload() {
  snapshot=$1
  transaction_id=$2
  scope=${S72_RESTORE_SCOPE:-apply}
  unit_workspace=$(s72_atomic_prepare_workspace "$SYSTEMD_DIR" units-$scope "$transaction_id") || return 1
  for unit in opensandbox-gateway.service opensandbox-gateway-helper.service; do
    source=absent
    test ! -f "$snapshot/$unit.present" || source=$snapshot/$unit
    s72_atomic_apply_file "$SYSTEMD_DIR/$unit" "$source" "$unit_workspace" "$unit" || return 1
  done
  s72_atomic_advance_transaction "$TRANSACTION_RECORDS" "$transaction_id" units-applied || return 1

  config_workspace=$(s72_atomic_prepare_workspace "${CONFIG_DIR%/*}" config-$scope "$transaction_id") || return 1
  config_source=absent
  test ! -f "$snapshot/config.present" || config_source=$snapshot/etc-opensandbox-gateway
  s72_atomic_apply_directory "$CONFIG_DIR" "$config_source" "$config_workspace" config || return 1
  test "$config_source" = absent || verify_config_metadata "$CONFIG_DIR" "$snapshot/config.metadata" || return 1
  s72_atomic_advance_transaction "$TRANSACTION_RECORDS" "$transaction_id" config-applied || return 1

  setfacl --restore="$snapshot/workspaces.acl" || return 1
  s72_atomic_advance_transaction "$TRANSACTION_RECORDS" "$transaction_id" acl-applied || return 1

  state_workspace=$(s72_atomic_prepare_workspace "$DEPLOY_STATE" state-$scope "$transaction_id") || return 1
  authority_source=absent
  evidence_source=absent
  if test -f "$snapshot/authority-sha"; then
    authority_source=$snapshot/authority-sha
    evidence_source=$snapshot/authority-evidence
  fi
  s72_atomic_apply_file "$AUTHORITY_SHA_STATE" "$authority_source" "$state_workspace" authority-sha 600 || return 1
  s72_atomic_apply_file "$AUTHORITY_EVIDENCE_STATE" "$evidence_source" "$state_workspace" authority-evidence 600 || return 1
  s72_atomic_advance_transaction "$TRANSACTION_RECORDS" "$transaction_id" authority-applied || return 1

  s72_atomic_apply_current_link "$snapshot" "$transaction_id" "$scope" || return 1
  pointer_source=absent
  test ! -f "$snapshot/rollback-pointer" || pointer_source=$snapshot/rollback-pointer
  s72_atomic_apply_file "$ROLLBACK_POINTER" "$pointer_source" "$state_workspace" rollback-pointer 600 || return 1
  s72_atomic_advance_transaction "$TRANSACTION_RECORDS" "$transaction_id" pointer-applied || return 1
}

restore_snapshot_runtime() {
  snapshot=$1
  systemctl daemon-reload || return 1
  for unit in opensandbox-gateway-helper.service opensandbox-gateway.service; do
    if test -f "$snapshot/$unit.enabled"; then
      systemctl enable "$unit" >/dev/null 2>&1 || return 1
    else
      systemctl disable "$unit" >/dev/null 2>&1 || true
    fi
    if test -f "$snapshot/$unit.active"; then
      systemctl restart "$unit" || return 1
      test "$(systemctl show "$unit" -p ActiveState --value)" = active || return 1
    else
      systemctl stop "$unit" >/dev/null 2>&1 || true
      test "$(systemctl show "$unit" -p ActiveState --value)" != active || return 1
    fi
  done
}

restore_snapshot() {
  s72_atomic_restore_snapshot "$1" "$2" "$TRANSACTION_RECORDS"
}

acquire_install_lock() {
  test -d /run/lock && test ! -L /run/lock || return 1
  test "$(stat -c %u /run/lock)" -eq 0 || return 1
  lock_parent_mode=$(stat -c %a /run/lock) || return 1
  test $((0$lock_parent_mode & 0022)) -eq 0 || return 1
  s72_atomic_require_root_owned_regular "$LOCK_FILE" 600 || return 1
  lock_identity=$(s72_atomic_node_identity "$LOCK_FILE") || return 1
  exec 9<>"$LOCK_FILE" || return 1
  test "$(stat -Lc '%d:%i:%F:%u:%g:%a:%s:%Y:%Z' /proc/$$/fd/9)" = "$lock_identity" || return 1
  flock -n 9 || return 1
  s72_atomic_require_identity "$LOCK_FILE" "$lock_identity"
}

initialize_deploy_state() {
  install -d -o root -g root -m 0755 /opt/opensandbox-gateway "$RELEASES" || return 1
  if test -e "$DEPLOY_STATE" || test -L "$DEPLOY_STATE"; then
    s72_atomic_require_root_owned_directory "$DEPLOY_STATE" 700 || return 1
  else
    install -d -o root -g root -m 0700 "$DEPLOY_STATE" || return 1
    s72_atomic_fsync_path "${DEPLOY_STATE%/*}" || return 1
  fi
  require_deploy_state_inventory bootstrap || return 1
  for directory in "$SNAPSHOTS" "$TRANSACTION_RECORDS"; do
    if test -e "$directory" || test -L "$directory"; then
      s72_atomic_require_root_owned_directory "$directory" 700 || return 1
    else
      install -d -o root -g root -m 0700 "$directory" || return 1
      s72_atomic_fsync_path "$DEPLOY_STATE" || return 1
    fi
  done
  require_deploy_state_inventory strict
}

require_deploy_state_inventory() {
  mode=${1:-strict}
  s72_atomic_require_root_owned_directory "$DEPLOY_STATE" 700 || return 1
  test -z "$(find "$DEPLOY_STATE" -mindepth 1 ! -type f ! -type d -print -quit)" || return 1
  for path in "$DEPLOY_STATE"/* "$DEPLOY_STATE"/.s72-*; do
    test -e "$path" || continue
    name=${path##*/}
    case "$name" in
      snapshots|transactions) test -d "$path" && test ! -L "$path" || return 1 ;;
      current-authority-sha|current-authority-evidence|previous-snapshot)
        s72_atomic_require_root_owned_regular "$path" 600 || return 1
        ;;
      .s72-state-apply-*|.s72-state-recovery-*|.s72-transaction-*)
        transaction=$(sed -n '2s/^transaction=//p' "$path/transaction-owner" 2>/dev/null) || return 1
        s72_atomic_require_transaction_owner "$path" || return 1
        s72_atomic_load_transaction "$TRANSACTION_RECORDS" "$transaction" || return 1
        test "$S72_TX_PHASE" != cleaned || return 1
        ;;
      *) return 1 ;;
    esac
  done
  if test -e "$SNAPSHOTS"; then
    for snapshot in "$SNAPSHOTS"/* "$SNAPSHOTS"/.snapshot-stage-*; do
      test -e "$snapshot" || continue
      name=${snapshot##*/}
      case "$name" in
        .rollback.*)
          s72_atomic_is_snapshot_id "$name" || return 1
          s72_atomic_preflight_snapshot "$snapshot" || return 1
          s72_atomic_verify_snapshot_seal "$snapshot" || return 1
          ;;
        .snapshot-stage-*)
          transaction=${name#.snapshot-stage-}
          s72_atomic_is_transaction_id "$transaction" || return 1
          s72_atomic_require_transaction_owner "$snapshot" || return 1
          test "$(sed -n '2s/^transaction=//p' "$snapshot/transaction-owner")" = "$transaction" || return 1
          s72_atomic_load_transaction "$TRANSACTION_RECORDS" "$transaction" || return 1
          test "$S72_TX_PHASE" != cleaned || return 1
          ;;
        *) return 1 ;;
      esac
    done
  else
    test "$mode" = bootstrap || return 1
  fi
  if test -e "$TRANSACTION_RECORDS"; then
    s72_atomic_require_transaction_inventory "$TRANSACTION_RECORDS" || return 1
  else
    test "$mode" = bootstrap || return 1
  fi
}

new_transaction_id() {
  od -An -N16 -tx1 /dev/urandom | tr -d ' \n'
}

begin_transaction() {
  TRANSACTION_ID=$1
  operation=$2
  recovery_snapshot=$3
  apply_snapshot=$4
  from_commit=$5
  to_commit=$6
  evidence=$7
  s72_atomic_is_transaction_id "$TRANSACTION_ID" || return 1
  s72_atomic_load_active_transaction "$TRANSACTION_RECORDS" >/dev/null 2>&1 && return 1
  test "$?" -eq 2 || return 1
  s72_atomic_publish_transaction_record "$TRANSACTION_RECORDS" "$TRANSACTION_ID" 000000 \
    "$operation" reserved "$recovery_snapshot" "$apply_snapshot" "$from_commit" "$to_commit" \
    "$evidence" none none >/dev/null || return 1
  transaction_workspace=$(s72_atomic_prepare_workspace "$DEPLOY_STATE" transaction "$TRANSACTION_ID") || return 1
  s72_atomic_bind_transaction_stage "$TRANSACTION_RECORDS" "$TRANSACTION_ID" \
    "$(s72_atomic_node_identity "$transaction_workspace")" || return 1
}

create_recovery_snapshot() {
  transaction_id=$1
  snapshot_id=$2
  stage=$(s72_atomic_create_snapshot_stage "$SNAPSHOTS" "$transaction_id") || return 1
  snapshot_state "$stage" || return 1
  published=$(s72_atomic_publish_snapshot "$stage" "$SNAPSHOTS" "$snapshot_id") || return 1
  test "$published" = "$SNAPSHOTS/$snapshot_id" || return 1
  s72_atomic_advance_transaction "$TRANSACTION_RECORDS" "$transaction_id" snapshot-published
}

cleanup_transaction_workspaces() {
  transaction_id=$1
  for workspace in \
    "$SNAPSHOTS/.snapshot-stage-$transaction_id" \
    "$RELEASES/.s72-release-$transaction_id" \
    "$SYSTEMD_DIR/.s72-units-apply-$transaction_id" \
    "$SYSTEMD_DIR/.s72-units-recovery-$transaction_id" \
    "${CONFIG_DIR%/*}/.s72-config-apply-$transaction_id" \
    "${CONFIG_DIR%/*}/.s72-config-recovery-$transaction_id" \
    "$DEPLOY_STATE/.s72-state-apply-$transaction_id" \
    "$DEPLOY_STATE/.s72-state-recovery-$transaction_id" \
    "${CURRENT_LINK%/*}/.s72-current-apply-$transaction_id" \
    "${CURRENT_LINK%/*}/.s72-current-recovery-$transaction_id" \
    "$DEPLOY_STATE/.s72-transaction-$transaction_id"; do
    test ! -e "$workspace" && test ! -L "$workspace" && continue
    test -d "$workspace" && test ! -L "$workspace" || return 1
    s72_atomic_remove_owned_stage "$workspace" "$transaction_id" || return 1
    s72_atomic_fsync_path "${workspace%/*}" || return 1
  done
}

finish_transaction_cleanup() {
  transaction_id=$1
  s72_atomic_load_transaction "$TRANSACTION_RECORDS" "$transaction_id" || return 1
  test "$S72_TX_PHASE" = committed || return 1
  cleanup_transaction_workspaces "$transaction_id" || return 1
  s72_atomic_advance_transaction "$TRANSACTION_RECORDS" "$transaction_id" cleaned
}

recover_active_transaction() {
  if s72_atomic_load_active_transaction "$TRANSACTION_RECORDS"; then
    :
  else
    load_status=$?
    test "$load_status" -eq 2 && return 0
    return 1
  fi
  transaction_id=$S72_TX_ID
  recovery_snapshot_id=$S72_TX_RECOVERY_SNAPSHOT
  if test "$S72_TX_PHASE" = committed; then
    finish_transaction_cleanup "$transaction_id"
    return
  fi
  if test "$S72_TX_PHASE" = reserved && { test "$recovery_snapshot_id" = none \
    || test ! -d "$SNAPSHOTS/$recovery_snapshot_id"; }; then
    s72_atomic_advance_transaction "$TRANSACTION_RECORDS" "$transaction_id" committed || return 1
    finish_transaction_cleanup "$transaction_id" || return 1
    return 0
  fi
  s72_atomic_is_snapshot_id "$recovery_snapshot_id" || return 1
  recovery_snapshot=$SNAPSHOTS/$recovery_snapshot_id
  s72_atomic_preflight_snapshot "$recovery_snapshot" || return 1
  s72_atomic_verify_snapshot_seal "$recovery_snapshot" || return 1
  s72_atomic_advance_transaction "$TRANSACTION_RECORDS" "$transaction_id" recovering || return 1
  restore_snapshot "$recovery_snapshot" "$transaction_id" || return 1
  s72_atomic_advance_transaction "$TRANSACTION_RECORDS" "$transaction_id" committed || return 1
  finish_transaction_cleanup "$transaction_id"
}

build_desired_snapshot() {
  transaction_id=$1
  snapshot_id=$2
  recovery_snapshot_id=$3
  commit=$4
  release=$RELEASES/$commit
  stage=$(s72_atomic_create_snapshot_stage "$SNAPSHOTS" "$transaction_id") || return 1
  for unit in opensandbox-gateway.service opensandbox-gateway-helper.service; do
    install -o root -g root -m 0644 "$release/config/$unit" "$stage/$unit" || return 1
    : > "$stage/$unit.present"
    : > "$stage/$unit.active"
    : > "$stage/$unit.enabled"
  done
  cp -a "$CONFIG_DIR" "$stage/etc-opensandbox-gateway" || return 1
  : > "$stage/config.present"
  write_config_metadata "$stage/etc-opensandbox-gateway" "$stage/config.metadata" || return 1
  getfacl -p "$WORKSPACE_ROOT" > "$stage/workspaces.acl" || return 1
  printf '%s\n' "$commit" > "$stage/authority-sha"
  printf '%s\n' "$AUTHORITY_EVIDENCE_ID" > "$stage/authority-evidence"
  printf '%s\n' "$EXPECTED_AUTHORITY_SHA" > "$stage/captured-authority-sha"
  printf '%s\n' "$AUTHORITY_EVIDENCE_ID" > "$stage/captured-authority-evidence"
  printf '%s\n' "releases/$commit" > "$stage/current"
  printf '%s\n' "$recovery_snapshot_id" > "$stage/rollback-pointer"
  s72_atomic_write_lifecycle_authority "$stage/lifecycle.authority" || return 1
  chown -R root:root "$stage" || return 1
  find "$stage" -maxdepth 1 -type f \( -name '*.present' -o -name '*.absent' \
    -o -name '*.active' -o -name '*.inactive' -o -name '*.enabled' -o -name '*.disabled' \) \
    -exec chmod 0600 {} + || return 1
  chmod 0600 "$stage/workspaces.acl" || return 1
  chmod 0400 "$stage/authority-sha" "$stage/authority-evidence" \
    "$stage/captured-authority-sha" "$stage/captured-authority-evidence" \
    "$stage/current" "$stage/rollback-pointer" || return 1
  write_manifest "$stage" || return 1
  preflight_snapshot "$stage" || return 1
  published=$(s72_atomic_publish_snapshot "$stage" "$SNAPSHOTS" "$snapshot_id") || return 1
  test "$published" = "$SNAPSHOTS/$snapshot_id"
}

rollback_action() {
  case "$AUTHORITY_REF" in ""|*[!A-Za-z0-9._/-]*|*..*) return 1 ;; esac
  is_commit "$EXPECTED_AUTHORITY_SHA" || return 1
  is_authority_evidence_id "$AUTHORITY_EVIDENCE_ID" || return 1
  acquire_install_lock || return 1
  initialize_deploy_state || return 1
  if s72_atomic_load_active_transaction "$TRANSACTION_RECORDS" >/dev/null 2>&1; then
    printf '%s\n' 'OpenSandbox gateway recovery is required before rollback; run --recover' >&2
    return 1
  else
    test "$?" -eq 2 || return 1
  fi
  s72_atomic_require_root_owned_regular "$ROLLBACK_POINTER" 600 || return 1
  test -L "$CURRENT_LINK" || return 1
  current_target=$(readlink "$CURRENT_LINK") || return 1
  case "$current_target" in releases/*) current_commit=${current_target#releases/} ;; *) return 1 ;; esac
  validate_release "$current_commit" rollback || return 1
  current_source=$(cat "$RELEASES/$current_commit/SOURCE_ROOT") || return 1
  current_ref=$(cat "$RELEASES/$current_commit/AUTHORITY_REF") || return 1
  test "$current_ref" = "$AUTHORITY_REF" || return 1
  current_authority=$(git -C "$current_source" rev-parse --verify "refs/remotes/$AUTHORITY_REF^{commit}") || return 1
  test "$current_authority" = "$EXPECTED_AUTHORITY_SHA" || return 1
  target_id=$(cat "$ROLLBACK_POINTER") || return 1
  s72_atomic_is_snapshot_id "$target_id" || return 1
  target_snapshot=$SNAPSHOTS/$target_id
  preflight_snapshot "$target_snapshot" || return 1
  s72_atomic_verify_snapshot_seal "$target_snapshot" || return 1
  target_evidence=$(cat "$target_snapshot/captured-authority-evidence") || return 1
  is_authority_evidence_id "$target_evidence" || return 1
  target_commit=none
  if test -f "$target_snapshot/current"; then
    target_value=$(cat "$target_snapshot/current") || return 1
    case "$target_value" in releases/*) target_commit=${target_value#releases/} ;; *) return 1 ;; esac
  fi
  test "$target_commit" = none || is_commit "$target_commit" || return 1

  TRANSACTION_ID=$(new_transaction_id) || return 1
  recovery_id=.rollback.$TRANSACTION_ID
  begin_transaction "$TRANSACTION_ID" rollback "$recovery_id" "$target_id" \
    "$current_commit" "$target_commit" "$target_evidence" || return 1
  SUCCESS=0
  trap 'cleanup_install' EXIT HUP INT TERM
  create_recovery_snapshot "$TRANSACTION_ID" "$recovery_id" || return 1
  restore_snapshot "$target_snapshot" "$TRANSACTION_ID" || return 1
  s72_atomic_advance_transaction "$TRANSACTION_RECORDS" "$TRANSACTION_ID" committed || return 1
  finish_transaction_cleanup "$TRANSACTION_ID" || return 1
  SUCCESS=1
  trap - EXIT HUP INT TERM
}

cleanup_install() {
  status=$?
  trap - EXIT HUP INT TERM
  if test "${SUCCESS:-0}" -eq 0 && test -n "${TRANSACTION_ID:-}"; then
    set +e
    recover_active_transaction
    restore_status=$?
    set -e
    if test "$restore_status" -ne 0; then
      printf '%s\n' 'OpenSandbox gateway recovery failed; sealed transaction retained for --recover' >&2
      exit 125
    fi
  fi
  exit "$status"
}

install_main() {
test "$(id -u)" -eq 0
case "${1:-}" in
  --recover)
    test "$#" -eq 1
    acquire_install_lock
    initialize_deploy_state
    recover_active_transaction
    return
    ;;
  --rollback)
    test "$#" -eq 1
    rollback_action
    return
    ;;
  --*) exit 1 ;;
esac
SOURCE_ROOT=${1:?usage: install-s72.sh /path/to/root-owned-clean-ai-platform-clone}
test "$#" -eq 1
case "$AUTHORITY_REF" in ""|*[!A-Za-z0-9._/-]*|*..*) exit 1 ;; esac
is_commit "$EXPECTED_AUTHORITY_SHA"
is_authority_evidence_id "$AUTHORITY_EVIDENCE_ID"
acquire_install_lock
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
s72_atomic_require_exact_lifecycle
preflight_live_state
initialize_deploy_state
if s72_atomic_load_active_transaction "$TRANSACTION_RECORDS" >/dev/null 2>&1; then
  printf '%s\n' 'OpenSandbox gateway recovery is required before install; run --recover' >&2
  exit 1
else
  test "$?" -eq 2 || exit 1
fi

RELEASE_ROOT=$RELEASES/$SOURCE_COMMIT
TRANSACTION_ID=$(new_transaction_id)
RECOVERY_SNAPSHOT_ID=.rollback.$TRANSACTION_ID
DESIRED_TOKEN=$(new_transaction_id)
DESIRED_SNAPSHOT_ID=.rollback.$DESIRED_TOKEN
FROM_COMMIT=${current_commit:-none}
begin_transaction "$TRANSACTION_ID" install "$RECOVERY_SNAPSHOT_ID" "$DESIRED_SNAPSHOT_ID" \
  "$FROM_COMMIT" "$SOURCE_COMMIT" "$AUTHORITY_EVIDENCE_ID"
SUCCESS=0
trap 'cleanup_install' EXIT HUP INT TERM

create_recovery_snapshot "$TRANSACTION_ID" "$RECOVERY_SNAPSHOT_ID"
getent group opensandbox-gateway >/dev/null 2>&1 || groupadd --system opensandbox-gateway
id opensandbox-gateway >/dev/null 2>&1 || useradd --system --gid opensandbox-gateway --home-dir /nonexistent --shell /usr/sbin/nologin opensandbox-gateway
install -d -o opensandbox-gateway -g opensandbox-gateway -m 0700 "$RUNTIME_STATE"
if test -e "$RELEASE_ROOT" || test -L "$RELEASE_ROOT"; then
  test -d "$RELEASE_ROOT" && test ! -L "$RELEASE_ROOT"
  validate_release "$SOURCE_COMMIT" exact
else
  STAGE=$(s72_atomic_prepare_workspace "$RELEASES" release "$TRANSACTION_ID")
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
	s72_atomic_fsync_tree "$STAGE"
  STAGE_IDENTITY=$(s72_atomic_node_identity "$STAGE")
  test "$(stat -c %d "$STAGE")" = "$(stat -c %d "$RELEASES")"
  mv -T -n "$STAGE" "$RELEASE_ROOT"
  test ! -e "$STAGE" && test ! -L "$STAGE"
  s72_atomic_require_identity "$RELEASE_ROOT" "$STAGE_IDENTITY"
  s72_atomic_fsync_path "$RELEASES"
validate_release "$SOURCE_COMMIT" exact
fi
s72_atomic_advance_transaction "$TRANSACTION_RECORDS" "$TRANSACTION_ID" release-published

require_gateway_config_contract
setfacl -m u:opensandbox-gateway:rwx,d:u:opensandbox-gateway:rwx "$WORKSPACE_ROOT"
build_desired_snapshot "$TRANSACTION_ID" "$DESIRED_SNAPSHOT_ID" "$RECOVERY_SNAPSHOT_ID" "$SOURCE_COMMIT"
restore_snapshot "$SNAPSHOTS/$DESIRED_SNAPSHOT_ID" "$TRANSACTION_ID"
test "$(systemctl show opensandbox-gateway.service -p WorkingDirectory --value)" = "$RELEASE_ROOT"
test "$(systemctl show opensandbox-gateway-helper.service -p WorkingDirectory --value)" = "$RELEASE_ROOT"
validate_release "$SOURCE_COMMIT" exact
systemctl is-active --quiet opensandbox-gateway-helper.service
systemctl is-active --quiet opensandbox-gateway.service
test "$(readlink -f "$CURRENT_LINK")" = "$RELEASE_ROOT"
s72_atomic_advance_transaction "$TRANSACTION_RECORDS" "$TRANSACTION_ID" committed
finish_transaction_cleanup "$TRANSACTION_ID"
SUCCESS=1
trap - EXIT HUP INT TERM
}

install_main "$@"
