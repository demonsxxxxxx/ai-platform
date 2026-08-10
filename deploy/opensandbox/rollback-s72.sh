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
TRANSACTION_RECORDS=$DEPLOY_STATE/transactions
SNAPSHOTS=$DEPLOY_STATE/snapshots
LOCK_FILE=/run/lock/opensandbox-gateway-s72-install.lock

S72_ATOMIC_RECOVERY_HELPER_SHA256=96915ad40a8847b3164faaf4c393c0653e619ada4fab5c02645e920d6c722654

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
test "${s72_loader_entrypoint##*/}" = rollback-s72.sh || s72_loader_reject
if test "$s72_loader_mode" = test-source-eval; then
  s72_loader_require_test_checkout_entry \
    "$s72_loader_entrypoint" deploy/opensandbox/rollback-s72.sh || s72_loader_reject
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
  s72_atomic_is_service_uid \
  s72_atomic_directory_identity \
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

require_gateway_config_contract_at() {
  contract_root=$1
  s72_atomic_require_root_owned_directory "$contract_root" 750 || return 1
  s72_atomic_require_root_owned_directory "$contract_root/secrets" 750 || return 1
  s72_atomic_require_root_owned_directory "$contract_root/tls" 750 || return 1
  test -z "$(find "$contract_root" -mindepth 1 -maxdepth 1 \
    ! -name gateway.env ! -name egress-policy.v1.json ! -name secrets ! -name tls -print -quit)" || return 1
  test -z "$(find "$contract_root/secrets" -mindepth 1 -maxdepth 1 \
    ! -name lifecycle-api-key ! -name capability-token ! -name record-signing-key -print -quit)" || return 1
  test -z "$(find "$contract_root/tls" -mindepth 1 -maxdepth 1 \
    ! -name fullchain.pem ! -name privkey.pem ! -name upstream-ca.pem -print -quit)" || return 1
  s72_atomic_require_root_owned_regular "$contract_root/gateway.env" 640 || return 1
  s72_atomic_require_root_owned_regular "$contract_root/egress-policy.v1.json" 640 || return 1
  s72_atomic_require_root_owned_regular "$contract_root/tls/fullchain.pem" 640 || return 1
  s72_atomic_require_root_owned_regular "$contract_root/tls/upstream-ca.pem" 640 || return 1
  s72_atomic_require_root_owned_regular "$contract_root/tls/privkey.pem" 440 || return 1
  for secret in lifecycle-api-key capability-token record-signing-key; do
    s72_atomic_require_root_owned_regular "$contract_root/secrets/$secret" 440 || return 1
  done
  test "$(grep -Fxc 'OPENSANDBOX_GATEWAY_UPSTREAM_CA_FILE=/etc/opensandbox-gateway/tls/upstream-ca.pem' "$contract_root/gateway.env")" -eq 1
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

verify_config_metadata() {
  tree=$1
  metadata=$2
  s72_atomic_require_root_owned_regular "$metadata" 400 || return 1
  test "$(cat "$metadata")" = "$(capture_config_metadata "$tree")"
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
  case "${1:-}" in
    "") test "$#" -eq 0; action=--rollback ;;
    --recover) test "$#" -eq 1; action=--recover ;;
    *) return 1 ;;
  esac
  installer=${s72_loader_entrypoint%/*}/install-s72.sh
  s72_loader_require_canonical_regular "$installer" || return 1
  s72_loader_require_privileged_chain "$installer" "$s72_loader_helper" || return 1
  installer_identity=$(s72_loader_identity "$installer") || return 1
  if test "$s72_loader_mode" = test-source-eval; then
    command -v s72_test_exec_installer >/dev/null 2>&1 || return 1
    s72_test_exec_installer "$installer" "$action" || return 1
  else
    test "$(s72_loader_identity "$installer")" = "$installer_identity" || return 1
    exec "$installer" "$action"
  fi
  test "$(s72_loader_identity "$installer")" = "$installer_identity"
}

rollback_main "$@"

# Docker provider configuration is never modified by deployment or rollback.
