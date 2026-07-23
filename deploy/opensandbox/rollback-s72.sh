#!/bin/sh
set -eu

test "$(id -u)" -eq 0
PREVIOUS=$(cat /var/lib/opensandbox-gateway/previous-release 2>/dev/null || true)
case "$PREVIOUS" in
  releases/[0-9a-f][0-9a-f]*)
    test -f "/opt/opensandbox-gateway/$PREVIOUS/SOURCE_COMMIT"
    EXPECTED_COMMIT=$(cat "/opt/opensandbox-gateway/$PREVIOUS/SOURCE_COMMIT")
    test "$EXPECTED_COMMIT" = "${PREVIOUS#releases/}"
    ln -s "$PREVIOUS" /opt/opensandbox-gateway/current.next
    mv -Tf /opt/opensandbox-gateway/current.next /opt/opensandbox-gateway/current
    systemctl restart opensandbox-gateway-helper.service opensandbox-gateway.service
    test "$(cat /opt/opensandbox-gateway/current/SOURCE_COMMIT)" = "$EXPECTED_COMMIT"
    systemctl is-active --quiet opensandbox-gateway-helper.service
    systemctl is-active --quiet opensandbox-gateway.service
    ;;
  *)
    systemctl disable --now opensandbox-gateway.service opensandbox-gateway-helper.service 2>/dev/null || true
    ;;
esac
systemctl is-active --quiet opensandbox.service
ss -ltn | grep -q '127.0.0.1:8080'

# Versioned releases, policy, secrets, and SQLite state are retained for
# deterministic readback and forensic recovery. Docker provider configuration
# is never modified by this deployment or rollback.
