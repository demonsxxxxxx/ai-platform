#!/bin/sh
set -eu

test "$(id -u)" -eq 0
systemctl disable --now opensandbox-gateway.service 2>/dev/null || true
rm -f /etc/systemd/system/opensandbox-gateway.service
systemctl daemon-reload
setfacl -x u:opensandbox-gateway /data/opensandbox/workspaces 2>/dev/null || true
setfacl -x d:u:opensandbox-gateway /data/opensandbox/workspaces 2>/dev/null || true
systemctl is-active --quiet opensandbox.service
ss -ltn | grep -q '127.0.0.1:8080'

# Deliberately retain /etc/opensandbox-gateway, /var/lib/opensandbox-gateway,
# and /opt/opensandbox-gateway for forensic recovery. ai-platform's Docker
# provider configuration is never modified by this deployment or rollback.
