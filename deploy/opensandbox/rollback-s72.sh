#!/bin/sh
set -eu

case "${0##*/}" in
  rollback-s72.sh) SELF_PATH=$0; CANONICAL_WRAPPER=1 ;;
  *)
    case "${SCRIPT:-}" in
      */deploy/opensandbox/rollback-s72.sh) SELF_PATH=$SCRIPT ;;
      *) exit 1 ;;
    esac
    CANONICAL_WRAPPER=0
    ;;
esac

test -f "$SELF_PATH" && test ! -L "$SELF_PATH"
SELF_REAL=$(readlink -f "$SELF_PATH")
SCRIPT_DIR=$(dirname "$SELF_REAL")
test "$SELF_REAL" = "$SCRIPT_DIR/rollback-s72.sh"
INSTALLER_ENGINE=$SCRIPT_DIR/install-s72.sh
test -f "$INSTALLER_ENGINE" && test ! -L "$INSTALLER_ENGINE"
if test "$(id -u)" -eq 0; then
  test "$(stat -c %u "$SELF_REAL")" -eq 0
  test "$(stat -c %u "$INSTALLER_ENGINE")" -eq 0
fi

if test "$CANONICAL_WRAPPER" -eq 1; then
  OPENSANDBOX_GATEWAY_ACTION=rollback
  export OPENSANDBOX_GATEWAY_ACTION
  exec "$INSTALLER_ENGINE" "$@"
fi

OPENSANDBOX_GATEWAY_INSTALL_LIBRARY_ONLY=1
export OPENSANDBOX_GATEWAY_INSTALL_LIBRARY_ONLY
. "$INSTALLER_ENGINE"
unset OPENSANDBOX_GATEWAY_INSTALL_LIBRARY_ONLY

rollback_main "$@"

# Docker provider configuration is never modified by deployment or rollback.
