#!/bin/sh
set -eu

test "$(id -u)" -eq 0
PREVIOUS=$(cat /var/lib/opensandbox-gateway/previous-release 2>/dev/null || true)
case "$PREVIOUS" in
  releases/[0-9a-f][0-9a-f]*)
    RELEASE_ROOT=/opt/opensandbox-gateway/$PREVIOUS
    test -f "$RELEASE_ROOT/SOURCE_COMMIT"
    EXPECTED_COMMIT=$(cat "$RELEASE_ROOT/SOURCE_COMMIT")
    test "$EXPECTED_COMMIT" = "${PREVIOUS#releases/}"
    test -f "$RELEASE_ROOT/config/opensandbox-gateway.service"
    test -f "$RELEASE_ROOT/config/opensandbox-gateway-helper.service"
    test -f "$RELEASE_ROOT/config/gateway.env"
    test -f "$RELEASE_ROOT/config/egress-policy.v1.json"
    test -f "$RELEASE_ROOT/config/workspaces.acl"
    install -o root -g root -m 0644 "$RELEASE_ROOT/config/opensandbox-gateway.service" /etc/systemd/system/opensandbox-gateway.service
    install -o root -g root -m 0644 "$RELEASE_ROOT/config/opensandbox-gateway-helper.service" /etc/systemd/system/opensandbox-gateway-helper.service
    setfacl --restore="$RELEASE_ROOT/config/workspaces.acl"
    systemctl daemon-reload
    systemctl restart opensandbox-gateway-helper.service opensandbox-gateway.service
    test "$(systemctl show opensandbox-gateway.service -p WorkingDirectory --value)" = "$RELEASE_ROOT"
    test "$(systemctl show opensandbox-gateway-helper.service -p WorkingDirectory --value)" = "$RELEASE_ROOT"
    test "$(cat "$RELEASE_ROOT/SOURCE_COMMIT")" = "$EXPECTED_COMMIT"
    systemctl is-active --quiet opensandbox-gateway-helper.service
    systemctl is-active --quiet opensandbox-gateway.service
    ln -s "$PREVIOUS" /opt/opensandbox-gateway/current.next
    mv -Tf /opt/opensandbox-gateway/current.next /opt/opensandbox-gateway/current
    test "$(cat /opt/opensandbox-gateway/current/SOURCE_COMMIT)" = "$EXPECTED_COMMIT"
    ;;
  *)
    systemctl disable --now opensandbox-gateway.service opensandbox-gateway-helper.service 2>/dev/null || true
    ;;
esac
systemctl is-active --quiet opensandbox.service
ss -ltn | grep -q '127.0.0.1:8080'

# Docker provider configuration is never modified by deployment or rollback.
