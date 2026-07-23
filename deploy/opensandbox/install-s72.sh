#!/bin/sh
set -eu

SOURCE_ROOT=${1:?usage: install-s72.sh /path/to/clean-ai-platform-clone}
AUTHORITY_REF=${OPENSANDBOX_GATEWAY_AUTHORITY_REF:-origin/main}
test "$(id -u)" -eq 0
test "$(git -C "$SOURCE_ROOT" rev-parse --show-toplevel)" = "$(cd "$SOURCE_ROOT" && pwd -P)"
SOURCE_COMMIT=$(git -C "$SOURCE_ROOT" rev-parse HEAD)
test -n "$SOURCE_COMMIT"
RELEASE_ROOT=/opt/opensandbox-gateway/releases/$SOURCE_COMMIT
git -C "$SOURCE_ROOT" diff-index --quiet HEAD --
test -z "$(git -C "$SOURCE_ROOT" ls-files --others --exclude-standard)"
git -C "$SOURCE_ROOT" show-ref --verify --quiet "refs/remotes/$AUTHORITY_REF"
git -C "$SOURCE_ROOT" merge-base --is-ancestor "$SOURCE_COMMIT" "$AUTHORITY_REF"
test -f /etc/opensandbox-gateway/gateway.env
test -f /etc/opensandbox-gateway/egress-policy.v1.json
test -f /etc/opensandbox-gateway/tls/fullchain.pem
test -f /etc/opensandbox-gateway/tls/privkey.pem
test "$(systemctl show opensandbox.service -p ActiveState --value)" = active
test "$(systemctl show opensandbox.service -p FragmentPath --value)" = /etc/systemd/system/opensandbox.service
ss -ltn | grep -q '127.0.0.1:8080'

getent group opensandbox-gateway >/dev/null 2>&1 || groupadd --system opensandbox-gateway
id opensandbox-gateway >/dev/null 2>&1 || useradd --system --gid opensandbox-gateway --home-dir /nonexistent --shell /usr/sbin/nologin opensandbox-gateway
install -d -o root -g root -m 0755 /opt/opensandbox-gateway/releases
install -d -o opensandbox-gateway -g opensandbox-gateway -m 0700 /var/lib/opensandbox-gateway

STAGE=$(mktemp -d /opt/opensandbox-gateway/releases/.stage.XXXXXX)
BACKUP=$(mktemp -d /var/lib/opensandbox-gateway/.rollback.XXXXXX)
SUCCESS=0
CURRENT=$(readlink /opt/opensandbox-gateway/current 2>/dev/null || true)
rollback_install() {
  test "$SUCCESS" -eq 0 || return 0
  test -f "$BACKUP/opensandbox-gateway.service" && install -o root -g root -m 0644 "$BACKUP/opensandbox-gateway.service" /etc/systemd/system/opensandbox-gateway.service
  test -f "$BACKUP/opensandbox-gateway-helper.service" && install -o root -g root -m 0644 "$BACKUP/opensandbox-gateway-helper.service" /etc/systemd/system/opensandbox-gateway-helper.service
  test -f "$BACKUP/workspaces.acl" && setfacl --restore="$BACKUP/workspaces.acl"
  if test -n "$CURRENT"; then
    ln -s "$CURRENT" /opt/opensandbox-gateway/current.rollback
    mv -Tf /opt/opensandbox-gateway/current.rollback /opt/opensandbox-gateway/current
  fi
  systemctl daemon-reload || true
  systemctl restart opensandbox-gateway-helper.service opensandbox-gateway.service || true
}
trap 'rollback_install; rm -rf "$STAGE" "$BACKUP"' EXIT HUP INT TERM

test -f /etc/systemd/system/opensandbox-gateway.service && cp -a /etc/systemd/system/opensandbox-gateway.service "$BACKUP/"
test -f /etc/systemd/system/opensandbox-gateway-helper.service && cp -a /etc/systemd/system/opensandbox-gateway-helper.service "$BACKUP/"
getfacl -p /data/opensandbox/workspaces > "$BACKUP/workspaces.acl"
git -C "$SOURCE_ROOT" archive "$SOURCE_COMMIT" services/opensandbox_gateway deploy/opensandbox | tar -x -C "$STAGE"
test -f "$STAGE/services/opensandbox_gateway/gateway.py"
printf '%s\n' "$SOURCE_COMMIT" > "$STAGE/SOURCE_COMMIT"
chmod 0444 "$STAGE/SOURCE_COMMIT"
install -d -o root -g opensandbox-gateway -m 0750 "$STAGE/config"
install -o root -g opensandbox-gateway -m 0640 /etc/opensandbox-gateway/gateway.env "$STAGE/config/gateway.env"
install -o root -g opensandbox-gateway -m 0640 /etc/opensandbox-gateway/egress-policy.v1.json "$STAGE/config/egress-policy.v1.json"
sed -i "s#/etc/opensandbox-gateway/egress-policy.v1.json#$RELEASE_ROOT/config/egress-policy.v1.json#g" "$STAGE/config/gateway.env"
getfacl -p /data/opensandbox/workspaces > "$STAGE/config/workspaces.acl"
sed "s#/opt/opensandbox-gateway/current#$RELEASE_ROOT#g;s#EnvironmentFile=/etc/opensandbox-gateway/gateway.env#EnvironmentFile=$RELEASE_ROOT/config/gateway.env#g" \
  "$STAGE/deploy/opensandbox/opensandbox-gateway.service" > "$STAGE/config/opensandbox-gateway.service"
sed "s#/opt/opensandbox-gateway/current#$RELEASE_ROOT#g" \
  "$STAGE/deploy/opensandbox/opensandbox-gateway-helper.service" > "$STAGE/config/opensandbox-gateway-helper.service"

test ! -e "$RELEASE_ROOT"
mv "$STAGE" "$RELEASE_ROOT"
STAGE=$RELEASE_ROOT
install -o root -g root -m 0644 "$RELEASE_ROOT/config/opensandbox-gateway.service" /etc/systemd/system/opensandbox-gateway.service
install -o root -g root -m 0644 "$RELEASE_ROOT/config/opensandbox-gateway-helper.service" /etc/systemd/system/opensandbox-gateway-helper.service
chown -R root:opensandbox-gateway /etc/opensandbox-gateway
chmod 0750 /etc/opensandbox-gateway /etc/opensandbox-gateway/secrets /etc/opensandbox-gateway/tls
chmod 0640 /etc/opensandbox-gateway/gateway.env /etc/opensandbox-gateway/egress-policy.v1.json /etc/opensandbox-gateway/tls/fullchain.pem
chmod 0440 /etc/opensandbox-gateway/secrets/* /etc/opensandbox-gateway/tls/privkey.pem
setfacl -m u:opensandbox-gateway:rwx,d:u:opensandbox-gateway:rwx /data/opensandbox/workspaces
getfacl -p /data/opensandbox/workspaces > "$RELEASE_ROOT/config/workspaces.acl"
systemctl daemon-reload
systemctl enable opensandbox-gateway-helper.service opensandbox-gateway.service
systemctl restart opensandbox-gateway-helper.service opensandbox-gateway.service
test "$(systemctl show opensandbox-gateway.service -p WorkingDirectory --value)" = "$RELEASE_ROOT"
test "$(systemctl show opensandbox-gateway-helper.service -p WorkingDirectory --value)" = "$RELEASE_ROOT"
test "$(cat "$RELEASE_ROOT/SOURCE_COMMIT")" = "$SOURCE_COMMIT"
READBACK=$(mktemp -d /var/lib/opensandbox-gateway/.readback.XXXXXX)
git -C "$SOURCE_ROOT" archive "$SOURCE_COMMIT" services/opensandbox_gateway | tar -x -C "$READBACK"
diff -r "$READBACK/services/opensandbox_gateway" "$RELEASE_ROOT/services/opensandbox_gateway"
rm -rf "$READBACK"
systemctl is-active --quiet opensandbox-gateway-helper.service
systemctl is-active --quiet opensandbox-gateway.service

printf '%s\n' "$CURRENT" > /var/lib/opensandbox-gateway/previous-release
ln -s "releases/$SOURCE_COMMIT" /opt/opensandbox-gateway/current.next
mv -Tf /opt/opensandbox-gateway/current.next /opt/opensandbox-gateway/current
test "$(cat /opt/opensandbox-gateway/current/SOURCE_COMMIT)" = "$SOURCE_COMMIT"
SUCCESS=1
rm -rf "$BACKUP"
trap - EXIT HUP INT TERM
