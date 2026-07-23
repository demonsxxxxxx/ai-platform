#!/bin/sh
set -eu

SOURCE_ROOT=${1:?usage: install-s72.sh /path/to/ai-platform}
test "$(id -u)" -eq 0
test -f "$SOURCE_ROOT/services/opensandbox_gateway/gateway.py"
test -f /etc/opensandbox-gateway/gateway.env
test -f /etc/opensandbox-gateway/egress-policy.v1.json
test -f /etc/opensandbox-gateway/tls/fullchain.pem
test -f /etc/opensandbox-gateway/tls/privkey.pem
test "$(systemctl show opensandbox.service -p ActiveState --value)" = active
test "$(systemctl show opensandbox.service -p FragmentPath --value)" = /etc/systemd/system/opensandbox.service
ss -ltn | grep -q '127.0.0.1:8080'

getent group opensandbox-gateway >/dev/null 2>&1 || groupadd --system opensandbox-gateway
id opensandbox-gateway >/dev/null 2>&1 || useradd --system --gid opensandbox-gateway --home-dir /nonexistent --shell /usr/sbin/nologin opensandbox-gateway
install -d -o root -g root -m 0755 /opt/opensandbox-gateway/services/opensandbox_gateway
install -o root -g root -m 0644 "$SOURCE_ROOT"/services/opensandbox_gateway/*.py /opt/opensandbox-gateway/services/opensandbox_gateway/
install -d -o opensandbox-gateway -g opensandbox-gateway -m 0700 /var/lib/opensandbox-gateway
chown -R root:opensandbox-gateway /etc/opensandbox-gateway
chmod 0750 /etc/opensandbox-gateway /etc/opensandbox-gateway/secrets /etc/opensandbox-gateway/tls
chmod 0640 /etc/opensandbox-gateway/gateway.env /etc/opensandbox-gateway/egress-policy.v1.json /etc/opensandbox-gateway/tls/fullchain.pem
chmod 0440 /etc/opensandbox-gateway/secrets/* /etc/opensandbox-gateway/tls/privkey.pem
setfacl -m u:opensandbox-gateway:rwx,d:u:opensandbox-gateway:rwx /data/opensandbox/workspaces
install -o root -g root -m 0644 "$SOURCE_ROOT/deploy/opensandbox/opensandbox-gateway.service" /etc/systemd/system/opensandbox-gateway.service
systemctl daemon-reload
systemctl enable --now opensandbox-gateway.service
systemctl is-active --quiet opensandbox-gateway.service
