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
ENV_FILE=/etc/default/docker-user-firewall

if [ "$(id -u)" -ne 0 ]; then
    echo "This script must run as root" >&2
    exit 1
fi

# Checked before anything changes, and narrow enough that the values need no
# escaping: systemd re-parses them from $ENV_FILE at boot, where a quote or a
# backslash would name a different interface than the one applied now.
if [ -n "${PUBLIC_TCP_PORTS:-}" ] \
    && ! [[ "$PUBLIC_TCP_PORTS" =~ ^[0-9]+(:[0-9]+)?(,[0-9]+(:[0-9]+)?)*$ ]]; then
    echo "PUBLIC_TCP_PORTS must be a comma-separated list of ports or port:port ranges" >&2
    exit 1
fi
if [ -n "${PUBLIC_INTERFACE:-}" ] && ! [[ "$PUBLIC_INTERFACE" =~ ^[A-Za-z0-9_.@-]+$ ]]; then
    echo "PUBLIC_INTERFACE may contain only letters, digits and . _ @ -" >&2
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

# The unit re-applies the Docker rules on every boot and Docker restart without
# this shell's environment, so the settings of this run are kept where it reads
# them. Written only after the rules applied: a value they reject must not reach
# the unit, whose failure at boot would leave every published port open.
# Rewritten on every run: a setting left out goes back to its default.
install -m 0644 /dev/null "$ENV_FILE"
if [ -n "${PUBLIC_TCP_PORTS:-}" ]; then
    printf 'PUBLIC_TCP_PORTS=%s\n' "$PUBLIC_TCP_PORTS" >> "$ENV_FILE"
fi
if [ -n "${PUBLIC_INTERFACE:-}" ]; then
    printf 'PUBLIC_INTERFACE=%s\n' "$PUBLIC_INTERFACE" >> "$ENV_FILE"
fi

systemctl daemon-reload
# Re-applies the Docker rules on boot and whenever the daemon is restarted,
# because Docker rebuilds its chains when it starts.
systemctl enable --now docker-user-firewall.service

# fail2ban keeps its bans in the filter table and loses them when ufw resets it.
if systemctl is-active --quiet fail2ban; then
    systemctl restart fail2ban
fi

ufw status verbose
