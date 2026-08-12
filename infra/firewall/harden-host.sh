#!/usr/bin/env bash
# Closes every port except SSH, HTTP and HTTPS on a deployed host.
#
# Two layers are needed:
#   * ufw    - protects services listening on the host itself (sshd, pm2, ...);
#   * DOCKER-USER - protects ports published by Docker, which bypass ufw.
#
# Safe to re-run: both layers are rebuilt from scratch on every invocation.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SSH_PORT="${SSH_PORT:-22}"

if [ "$(id -u)" -ne 0 ]; then
    echo "This script must run as root" >&2
    exit 1
fi

install -m 0755 "${SCRIPT_DIR}/docker-user-rules.sh" /usr/local/sbin/docker-user-rules.sh
install -m 0644 "${SCRIPT_DIR}/docker-user-firewall.service" \
    /etc/systemd/system/docker-user-firewall.service

ufw --force reset >/dev/null
ufw default deny incoming
ufw default allow outgoing
ufw allow "${SSH_PORT}/tcp" comment 'ssh'
ufw allow 80/tcp comment 'http'
ufw allow 443/tcp comment 'https'
ufw --force enable

/usr/local/sbin/docker-user-rules.sh

systemctl daemon-reload
# Re-applies the Docker rules on boot and whenever the daemon is restarted,
# because Docker rebuilds its chains when it starts.
systemctl enable --now docker-user-firewall.service

# fail2ban keeps its bans in the filter table and loses them when ufw resets it.
if systemctl is-active --quiet fail2ban; then
    systemctl restart fail2ban
fi

ufw status verbose
